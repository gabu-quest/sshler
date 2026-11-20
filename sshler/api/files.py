from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import posixpath
import stat as stat_mod
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import asyncssh
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .. import state
from ..config import AppConfig
from ..proc import run_bounded
from ..ssh import SSHError, sftp_chmod, sftp_list_directory, sftp_session
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import (
    IMAGE_CONTENT_TYPES,
    MAX_IMAGE_PREVIEW_BYTES,
    _compose_local_child_path,
    _is_markdown_file,
    _local_delete_file,
    _local_read_bytes,
    _local_read_text,
    _local_write_text,
    _normalize_local_path,
    _read_file_bytes,
    _read_remote_text,
    _syntax_from_filename,
)
from .local_files import (
    _local_children,
    _local_entry_stats,
    _local_place,
    _local_place_into,
    _local_write_new,
)
from .models import (
    APIChmodRequest,
    APICopyRequest,
    APIDeleteRequest,
    APIDirectoryEntry,
    APIDirectoryListing,
    APIFilePreview,
    APIMoveRequest,
    APIRenameRequest,
    APISimpleMessage,
    APITouchRequest,
)
from .placement import _RefusalAnswers
from .rate_limiting import rate_limit_delete, rate_limit_upload, rate_limit_write
from .remote import (
    compose_remote_child,
    remote_directory,
    remote_path,
)
from .remote_files import (
    ZIP_STREAM_TIMEOUT_SECONDS,
    _du_bytes,
    _remote_place,
    _remote_write_new,
    _spool_remote_zip,
    _ZipTooLargeError,
    directory_test_command,
    du_command,
    folder_zip_command,
)

logger = logging.getLogger(__name__)


class _TempFileResponse(FileResponse):
    """A ``FileResponse`` that deletes its file once the response is over.

    A ``background`` task is not enough: Starlette answers a malformed or
    unsatisfiable ``Range`` with an early ``return`` that skips it, and a client
    disconnect raises out of ``send`` before it runs. ``finally`` covers every outcome.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


# The gitignore marks of a listing are a nicety: a git that does not answer in
# this time is given up on and the listing goes out without them.
GITIGNORE_TIMEOUT = 5.0


async def _get_gitignored_names(directory: str, names: list[str]) -> set[str]:
    """Check which filenames in a directory are gitignored. Returns set of ignored names."""
    try:
        returncode, stdout, _ = await run_bounded(
            ["git", "check-ignore", "--stdin"],
            GITIGNORE_TIMEOUT,
            cwd=directory,
            stdin_data="\n".join(names).encode(),
        )
        if returncode not in (0, 1):  # 1 means none matched, 128 means not a git repo
            return set()
        return set(line.strip() for line in stdout.decode().splitlines() if line.strip())
    except Exception:
        return set()


def _attachment_header(filename: str) -> str:
    """``Content-Disposition`` for a download, safe for any file name.

    ``filename*`` carries the exact name percent-encoded as UTF-8 (RFC 5987/6266);
    ``filename`` is an ASCII fallback with ``"``, ``\\``, ``%`` and non-ASCII or
    control characters replaced, so a Japanese or quoted name cannot break the header.
    """
    fallback = "".join(
        ch if 0x20 <= ord(ch) < 0x7F and ch not in '"\\%' else "_" for ch in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()
    # The routes that place a file under a new name answer a refusal with its reason.
    placing = APIRouter(route_class=_RefusalAnswers)

    @router.get("/boxes/{name}/ls", response_model=APIDirectoryListing)
    async def api_list_directory(
        name: str,
        directory: str = Query("/"),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIDirectoryListing:
        box = deps.get_box_or_404(application_config, name)

        ssh_pool = get_pool()
        entries: list[APIDirectoryEntry] = []

        if box.transport == "local":
            normalized = _normalize_local_path(directory)
            target = Path(normalized)
            if not target.exists():
                raise HTTPException(status_code=404, detail="Directory not found")
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Path is not a directory")
            children = await asyncio.to_thread(_local_children, target)

            # Check gitignored status for all entries in one batch
            ignored_names: set[str] = set()
            if children:
                ignored_names = await _get_gitignored_names(
                    normalized, [c.name for c, _ in children]
                )

            for child, is_dir, size, modified, mode in await asyncio.to_thread(
                _local_entry_stats, children
            ):
                entries.append(
                    APIDirectoryEntry(
                        name=child.name,
                        path=str(child),
                        is_directory=is_dir,
                        size=size,
                        modified=modified,
                        mode=mode,
                        gitignored=child.name in ignored_names,
                    )
                )
            return APIDirectoryListing(box=box.name, directory=normalized, entries=entries)

        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                # `~` is the remote home, as for the folder zip (not `/`).
                normalized_remote = await remote_directory(connection, directory)
                raw_entries = await sftp_list_directory(connection, normalized_remote)
        except HTTPException:
            raise
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - remote errors vary
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        for entry in raw_entries:
            entry_path = posixpath.join(normalized_remote, str(entry["name"]))
            entries.append(
                APIDirectoryEntry(
                    name=str(entry["name"]),
                    path=entry_path,
                    is_directory=bool(entry.get("is_directory")),
                    size=int(str(entry["size"])) if entry.get("size") is not None else None,
                    modified=float(str(entry["modified"]))
                    if entry.get("modified") is not None
                    else None,
                    mode=int(str(entry["mode"])) if entry.get("mode") is not None else None,
                )
            )

        # Track directory visit for frecency-based search (remote boxes only)
        await state.record_directory_visit_async(box.name, normalized_remote)

        return APIDirectoryListing(box=box.name, directory=normalized_remote, entries=entries)

    @placing.post("/boxes/{name}/rename", response_model=APISimpleMessage)
    async def api_rename(
        name: str,
        payload: APIRenameRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            try:
                new_name = PathValidator.validate_filename(payload.new_name)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            def _rename() -> str:
                source = Path(_normalize_local_path(payload.path))
                target = str(source.parent / new_name)
                _local_place(str(source), target, move=True, overwrite=payload.overwrite)
                return target

            try:
                target = await asyncio.to_thread(_rename)
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="renamed", path=target)

        try:
            PathValidator.validate_remote_path(payload.path)
            new_name = PathValidator.validate_filename(payload.new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, payload.path)
                target_path = str(PurePosixPath(validated_path).parent / new_name)
                async with sftp_session(connection) as sftp_client:
                    await _remote_place(
                        sftp_client,
                        validated_path,
                        target_path,
                        move=True,
                        overwrite=payload.overwrite,
                    )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="renamed", path=target_path)

    @placing.post("/boxes/{name}/touch", response_model=APISimpleMessage)
    async def api_touch_file(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            directory_path = _normalize_local_path(payload.directory)
            target_path = _compose_local_child_path(directory_path, payload.filename)
            try:
                await _local_write_new(target_path, b"", overwrite=False, exists_status=400)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="created", path=target_path)

        try:
            validated_filename = PathValidator.validate_filename(payload.filename)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = compose_remote_child(
                    await remote_directory(connection, payload.directory), validated_filename
                )
                async with sftp_session(connection) as sftp_client:
                    await _remote_write_new(sftp_client, remote_path, b"", overwrite=False)
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="created", path=remote_path)

    @router.post("/boxes/{name}/mkdir", response_model=APISimpleMessage)
    async def api_mkdir(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            directory_path = _normalize_local_path(payload.directory)
            target_path = _compose_local_child_path(directory_path, payload.filename)
            path_obj = Path(target_path)
            if path_obj.exists():
                raise HTTPException(status_code=400, detail="Directory already exists")
            try:
                path_obj.mkdir(parents=False, exist_ok=False)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="created", path=target_path)

        try:
            validated_name = PathValidator.validate_filename(payload.filename)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = compose_remote_child(
                    await remote_directory(connection, payload.directory), validated_name
                )
                async with sftp_session(connection) as sftp_client:
                    try:
                        await sftp_client.stat(remote_path)
                        raise HTTPException(status_code=400, detail="Directory already exists")
                    except HTTPException:
                        raise
                    except Exception:
                        pass
                    await sftp_client.mkdir(remote_path)
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="created", path=remote_path)

    @router.post("/boxes/{name}/delete", response_model=APISimpleMessage)
    async def api_delete_file(
        request: Request,
        name: str,
        payload: APIDeleteRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_delete),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            target_path = _normalize_local_path(payload.path)
            try:
                await _local_delete_file(target_path)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="deleted", path=target_path)

        try:
            PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, payload.path)
                async with sftp_session(connection) as sftp_client:
                    await sftp_client.remove(validated_path)
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="deleted", path=validated_path)

    @placing.post("/boxes/{name}/move", response_model=APISimpleMessage)
    async def api_move(
        name: str,
        payload: APIMoveRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            try:
                target = await asyncio.to_thread(
                    _local_place_into,
                    payload.source,
                    payload.destination,
                    None,
                    move=True,
                    overwrite=payload.overwrite,
                )
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Source not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="moved", path=target)

        try:
            PathValidator.validate_remote_path(payload.source)
            PathValidator.validate_remote_path(payload.destination)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_src = await remote_path(connection, payload.source)
                remote_dest_dir = await remote_path(connection, payload.destination)
                remote_target = str(PurePosixPath(remote_dest_dir) / PurePosixPath(remote_src).name)
                async with sftp_session(connection) as sftp_client:
                    await _remote_place(
                        sftp_client,
                        remote_src,
                        remote_target,
                        move=True,
                        overwrite=payload.overwrite,
                    )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="moved", path=remote_target)

    @placing.post("/boxes/{name}/copy", response_model=APISimpleMessage)
    async def api_copy(
        name: str,
        payload: APICopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            if payload.new_name:
                try:
                    PathValidator.validate_filename(payload.new_name)
                except ValidationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                target = await asyncio.to_thread(
                    _local_place_into,
                    payload.source,
                    payload.destination,
                    payload.new_name,
                    move=False,
                    overwrite=payload.overwrite,
                    allow_dirs=False,
                )
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Source not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="copied", path=target)

        try:
            PathValidator.validate_remote_path(payload.source)
            PathValidator.validate_remote_path(payload.destination)
            if payload.new_name:
                PathValidator.validate_filename(payload.new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_src = await remote_path(connection, payload.source)
                remote_dest_dir = await remote_path(connection, payload.destination)
                # Named after the resolved source: `~` alone has no usable name.
                new_name = PathValidator.validate_filename(
                    payload.new_name or PurePosixPath(remote_src).name
                )
                remote_target = str(PurePosixPath(remote_dest_dir) / new_name)
                async with sftp_session(connection) as sftp_client:
                    await _remote_place(
                        sftp_client,
                        remote_src,
                        remote_target,
                        move=False,
                        overwrite=payload.overwrite,
                        allow_dirs=False,
                    )
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="copied", path=remote_target)

    @router.get("/boxes/{name}/file", response_model=APIFilePreview)
    async def api_file_preview(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIFilePreview:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            suffix = Path(normalized_path).suffix.lower()
            image_mime = IMAGE_CONTENT_TYPES.get(suffix)
            image_data: str | None = None
            image_too_large = False
            if image_mime:
                image_bytes, too_large = await _local_read_bytes(
                    normalized_path, MAX_IMAGE_PREVIEW_BYTES
                )
                if too_large:
                    image_too_large = True
                else:
                    image_data = base64.b64encode(image_bytes).decode("ascii")

            text_content: str | None = None
            if not image_mime or image_too_large:
                text_content = await _local_read_text(
                    normalized_path, deps.settings.max_upload_bytes
                )

            parent_dir = str(Path(normalized_path).parent)
            is_markdown = _is_markdown_file(normalized_path)
            syntax_class = _syntax_from_filename(normalized_path)

            return APIFilePreview(
                box=box.name,
                path=normalized_path,
                parent=parent_dir,
                content=text_content or None,
                syntax_class=syntax_class,
                image_data=image_data,
                image_mime=image_mime,
                image_too_large=image_too_large,
                is_markdown=is_markdown,
            )

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                suffix = Path(validated_path).suffix.lower()
                image_mime = IMAGE_CONTENT_TYPES.get(suffix)
                remote_image_data: str | None = None
                image_too_large = False
                if image_mime:
                    image_bytes, too_large = await _read_file_bytes(
                        connection, validated_path, MAX_IMAGE_PREVIEW_BYTES
                    )
                    if too_large:
                        image_too_large = True
                    else:
                        remote_image_data = base64.b64encode(image_bytes).decode("ascii")

                remote_text_content: str | None = None
                if not image_mime or image_too_large:
                    remote_text_content = await _read_remote_text(
                        connection, validated_path, deps.settings.max_upload_bytes
                    )

                parent_dir = str(PurePosixPath(validated_path).parent)
                is_markdown = _is_markdown_file(validated_path)
                syntax_class = _syntax_from_filename(validated_path)

                return APIFilePreview(
                    box=box.name,
                    path=validated_path,
                    parent=parent_dir,
                    content=remote_text_content or None,
                    syntax_class=syntax_class,
                    image_data=remote_image_data,
                    image_mime=image_mime,
                    image_too_large=image_too_large,
                    is_markdown=is_markdown,
                )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/boxes/{name}/write", response_model=APISimpleMessage)
    async def api_write_file(
        request: Request,
        name: str,
        path: str = Body(...),
        content: str = Body(...),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_write),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        encoded_len = len(content.encode("utf-8"))
        if encoded_len > deps.settings.max_upload_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds {deps.settings.max_upload_bytes // 1024}KB editing limit",
            )

        if box.transport == "local":
            target_path = _normalize_local_path(path)
            try:
                await _local_write_text(target_path, content)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="saved", path=target_path)

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                async with sftp_session(connection) as sftp_client:
                    async with await sftp_client.open(
                        validated_path, "w", encoding="utf-8"
                    ) as remote_file:
                        await remote_file.write(content)
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="saved", path=validated_path)

    @router.get("/boxes/{name}/download")
    async def api_download_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            file_path = Path(normalized_path)
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            return FileResponse(file_path)

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(
                        status_code=413, detail="File too large to download via API"
                    )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        filename = PurePosixPath(validated_path).name
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": _attachment_header(filename)},
        )

    @router.get("/boxes/{name}/view")
    async def api_view_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Serve a file inline with its natural MIME type (for browser rendering)."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            file_path = Path(normalized_path)
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            mime, _ = mimetypes.guess_type(normalized_path)
            return FileResponse(
                file_path,
                media_type=mime or "application/octet-stream",
                content_disposition_type="inline",
            )

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(status_code=413, detail="File too large to view")
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        mime, _ = mimetypes.guess_type(validated_path)
        return Response(
            content=content,
            media_type=mime or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )

    @router.get("/boxes/{name}/dir-size")
    async def api_directory_size(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Get the total size of a directory in bytes."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)

            def _calc_size() -> int:
                total = 0
                base = Path(normalized)
                if not base.is_dir():
                    raise ValueError("Not a directory")
                for child in base.rglob("*"):
                    if child.is_file():
                        try:
                            total += child.stat().st_size
                        except OSError:
                            pass
                return total

            try:
                size = await asyncio.to_thread(_calc_size)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"size_bytes": size}

        # Remote
        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                remote_size = await _du_bytes(connection, du_command("-sb", validated_path), 1)
                if remote_size is None:
                    # BSD/macOS `du` has no -b; -k works everywhere.
                    remote_size = await _du_bytes(
                        connection, du_command("-sk", validated_path), 1024
                    )
                if remote_size is None:
                    exists = await asyncio.wait_for(
                        connection.run(directory_test_command(validated_path)), timeout=10
                    )
                    if exists.exit_status != 0:
                        raise HTTPException(status_code=404, detail="Directory not found")
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Directory size timed out") from exc
        except Exception as exc:
            logger.warning(f"Failed to get dir size: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # None: both `du` forms failed on a folder that exists; the size is unknown
        # (never 0) and the UI asks.
        return {"size_bytes": remote_size}

    @router.get("/boxes/{name}/download-dir")
    async def api_download_directory(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Download a directory as a zip file."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)
            base = Path(normalized)
            if not base.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            tmp_path = tmp.name
            tmp.close()

            def _create_zip():
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for child in base.rglob("*"):
                        if child.is_file():
                            arcname = str(child.relative_to(base))
                            zf.write(str(child), arcname)

            try:
                await asyncio.to_thread(_create_zip)
            except Exception as exc:
                Path(tmp_path).unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            dirname = base.name or "download"
            return _TempFileResponse(
                tmp_path,
                media_type="application/zip",
                headers={"Content-Disposition": _attachment_header(f"{dirname}.zip")},
            )

        # Remote: stream the zip through SSH into a temp file, capped in size.
        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        tmp.close()
        connection = None
        try:
            try:
                connection = await ssh_pool.acquire(
                    box, lambda: deps.connect_for_box(box, application_config)
                )
                validated_path = await remote_path(connection, path)
                dirname, cmd = folder_zip_command(validated_path)
                async with asyncio.timeout(ZIP_STREAM_TIMEOUT_SECONDS):
                    await _spool_remote_zip(connection, cmd, tmp_path)
                # Inside the cleanup `try`: a cancel during the release's health check
                # must still delete the zip. `release` closes the connection itself
                # then, so it is no longer ours to release or discard.
                done, connection = connection, None
                await ssh_pool.release(box.name, done)
            except BaseException as exc:
                Path(tmp_path).unlink(missing_ok=True)
                if connection is not None:
                    # A capped, timed-out or cancelled zip may still be running on the
                    # connection: close it rather than pool it.
                    reusable = isinstance(exc, Exception) and not isinstance(
                        exc, (TimeoutError, _ZipTooLargeError)
                    )
                    if reusable:
                        await ssh_pool.release(box.name, connection)
                    else:
                        ssh_pool.discard(box.name, connection)
                raise
        except _ZipTooLargeError as exc:
            raise HTTPException(status_code=413, detail="Directory too large to download") from exc
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Download timed out") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return _TempFileResponse(
            tmp_path,
            media_type="application/zip",
            headers={"Content-Disposition": _attachment_header(f"{dirname}.zip")},
        )

    @router.get("/boxes/{name}/stat")
    async def api_stat_path(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Check if a path exists and whether it's a file or directory."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)
            p = Path(normalized)
            if not p.exists():
                return {"exists": False, "is_directory": False, "is_file": False}
            return {
                "exists": True,
                "is_directory": p.is_dir(),
                "is_file": p.is_file(),
            }

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, path)
                async with sftp_session(connection) as sftp_client:
                    try:
                        attrs = await sftp_client.stat(validated_path)
                        perms = attrs.permissions
                        is_dir = stat_mod.S_ISDIR(perms) if perms else False
                        is_file = stat_mod.S_ISREG(perms) if perms else False
                        return {"exists": True, "is_directory": is_dir, "is_file": is_file}
                    except (FileNotFoundError, OSError):
                        return {"exists": False, "is_directory": False, "is_file": False}
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @placing.post("/boxes/{name}/upload", response_model=APISimpleMessage)
    async def api_upload(
        request: Request,
        name: str,
        directory: str = Form(...),
        file: UploadFile = File(...),
        overwrite: bool = Form(False),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_upload),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        candidate_name = (file.filename or "").strip()
        if not candidate_name:
            raise HTTPException(status_code=400, detail="Missing filename")

        try:
            contents = await file.read()
        finally:
            await file.close()

        if len(contents) > deps.settings.max_upload_bytes:
            limit_kb = deps.settings.max_upload_bytes // 1024
            raise HTTPException(status_code=400, detail=f"Upload exceeds {limit_kb} KB limit")

        if box.transport == "local":
            directory_path = _normalize_local_path(directory)
            try:
                target_path = _compose_local_child_path(directory_path, candidate_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                await _local_write_new(
                    target_path, contents, overwrite=overwrite, exists_status=409
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="uploaded", path=target_path)

        try:
            validated_filename = PathValidator.validate_filename(candidate_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = compose_remote_child(
                    await remote_directory(connection, directory), validated_filename
                )
                async with sftp_session(connection) as sftp_client:
                    await _remote_write_new(sftp_client, remote_path, contents, overwrite=overwrite)
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="uploaded", path=remote_path)

    @router.post("/boxes/{name}/chmod", response_model=APISimpleMessage)
    async def api_chmod(
        name: str,
        payload: APIChmodRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        try:
            mode = int(payload.mode, 8)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid octal mode") from exc
        if mode < 0 or mode > 0o7777:
            raise HTTPException(status_code=400, detail="Mode out of range")

        if box.transport == "local":
            target = Path(_normalize_local_path(payload.path))
            if not target.exists():
                raise HTTPException(status_code=404, detail="File not found")
            try:
                target.chmod(mode)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="chmod applied", path=str(target))

        try:
            PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await remote_path(connection, payload.path)
                await sftp_chmod(connection, validated_path, mode)
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="chmod applied", path=validated_path)

    router.include_router(placing)
    return router
