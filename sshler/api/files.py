from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import posixpath
import shlex
import stat as stat_mod
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)

from .. import state
from ..config import AppConfig
from ..ssh import SSHError, sftp_chmod, sftp_list_directory
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .rate_limiting import rate_limit_delete, rate_limit_upload, rate_limit_write
from .helpers import (
    IMAGE_CONTENT_TYPES,
    MAX_IMAGE_PREVIEW_BYTES,
    _compose_local_child_path,
    _compose_remote_child_path,
    _is_markdown_file,
    _local_create_file,
    _local_delete_file,
    _local_read_bytes,
    _local_read_text,
    _local_write_bytes,
    _local_write_text,
    _normalize_directory_path,
    _normalize_local_path,
    _read_file_bytes,
    _read_remote_text,
    _syntax_from_filename,
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


from starlette.background import BackgroundTask


def _cleanup_temp(path: str) -> BackgroundTask:
    """Return a background task that deletes a temp file after response is sent."""
    def _remove():
        Path(path).unlink(missing_ok=True)
    return BackgroundTask(_remove)


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

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
            children = []
            for child in target.iterdir():
                try:
                    is_dir = child.is_dir()
                    children.append((child, is_dir))
                except OSError:
                    continue  # broken symlink or deleted between iterdir and stat
            children.sort(key=lambda item: (not item[1], item[0].name.lower()))
            for child, is_dir in children:
                try:
                    stats = child.stat()
                except OSError:
                    continue
                entries.append(
                    APIDirectoryEntry(
                        name=child.name,
                        path=str(child),
                        is_directory=is_dir,
                        size=stats.st_size if child.is_file() else None,
                        modified=stats.st_mtime,
                        mode=stats.st_mode & 0o7777,
                    )
                )
            return APIDirectoryListing(box=box.name, directory=normalized, entries=entries)

        normalized_remote = _normalize_directory_path(directory)

        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                raw_entries = await sftp_list_directory(connection, normalized_remote)
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:  # pragma: no cover - remote errors vary
            raise HTTPException(status_code=500, detail=str(exc))

        for entry in raw_entries:
            entry_path = posixpath.join(normalized_remote, str(entry["name"]))
            entries.append(
                APIDirectoryEntry(
                    name=str(entry["name"]),
                    path=entry_path,
                    is_directory=bool(entry.get("is_directory")),
                    size=int(str(entry["size"])) if entry.get("size") is not None else None,
                    modified=float(str(entry["modified"])) if entry.get("modified") is not None else None,
                    mode=int(str(entry["mode"])) if entry.get("mode") is not None else None,
                )
            )

        # Track directory visit for frecency-based search (remote boxes only)
        await state.record_directory_visit_async(box.name, normalized_remote)

        return APIDirectoryListing(box=box.name, directory=normalized_remote, entries=entries)

    @router.post("/boxes/{name}/rename", response_model=APISimpleMessage)
    async def api_rename(
        name: str,
        payload: APIRenameRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            source = Path(_normalize_local_path(payload.path))
            target = source.parent / payload.new_name
            if not source.exists():
                raise HTTPException(status_code=404, detail="File not found")
            try:
                source.rename(target)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="renamed", path=str(target))

        try:
            validated_path = PathValidator.validate_remote_path(payload.path)
            new_name = PathValidator.validate_filename(payload.new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        target_path = str(PurePosixPath(validated_path).parent / new_name)
        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.rename(validated_path, target_path)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return APISimpleMessage(status="ok", message="renamed", path=target_path)

    @router.post("/boxes/{name}/touch", response_model=APISimpleMessage)
    async def api_touch_file(
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
                raise HTTPException(status_code=400, detail="File already exists")
            try:
                await _local_write_bytes(target_path, b"")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="created", path=target_path)

        directory_path = _normalize_directory_path(payload.directory)
        try:
            validated_filename = PathValidator.validate_filename(payload.filename)
            remote_path = _compose_remote_child_path(directory_path, validated_filename)
            PathValidator.validate_remote_path(remote_path)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    try:
                        await sftp_client.stat(remote_path)
                        raise HTTPException(status_code=400, detail="File already exists")
                    except Exception:
                        pass
                    async with await sftp_client.open(remote_path, "w", encoding="utf-8") as remote_file:
                        await remote_file.write("")
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

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
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="File not found")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="deleted", path=target_path)

        try:
            validated_path = PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.remove(validated_path)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return APISimpleMessage(status="ok", message="deleted", path=validated_path)

    @router.post("/boxes/{name}/move", response_model=APISimpleMessage)
    async def api_move(
        name: str,
        payload: APIMoveRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            source = Path(_normalize_local_path(payload.source))
            dest_dir = Path(_normalize_local_path(payload.destination))
            if not source.exists():
                raise HTTPException(status_code=404, detail="Source not found")
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / source.name
            try:
                source.rename(target)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="moved", path=str(target))

        try:
            remote_src = PathValidator.validate_remote_path(payload.source)
            remote_dest_dir = PathValidator.validate_remote_path(payload.destination)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        remote_target = str(PurePosixPath(remote_dest_dir) / PurePosixPath(remote_src).name)
        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.rename(remote_src, remote_target)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Source not found")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return APISimpleMessage(status="ok", message="moved", path=remote_target)

    @router.post("/boxes/{name}/copy", response_model=APISimpleMessage)
    async def api_copy(
        name: str,
        payload: APICopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            source = Path(_normalize_local_path(payload.source))
            dest_dir = Path(_normalize_local_path(payload.destination))
            if not source.exists():
                raise HTTPException(status_code=404, detail="Source not found")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_name = payload.new_name or source.name
            target = dest_dir / dest_name
            try:
                if source.is_dir():
                    raise HTTPException(status_code=400, detail="Copying directories not supported")
                target.write_bytes(source.read_bytes())
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="copied", path=str(target))

        try:
            remote_src = PathValidator.validate_remote_path(payload.source)
            remote_dest_dir = PathValidator.validate_remote_path(payload.destination)
            new_name = payload.new_name or PurePosixPath(remote_src).name
            new_name = PathValidator.validate_filename(new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        remote_target = str(PurePosixPath(remote_dest_dir) / new_name)
        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    async with contextlib.AsyncExitStack() as stack:
                        src_file = await stack.enter_async_context(await sftp_client.open(remote_src, "rb"))
                        dest_file = await stack.enter_async_context(await sftp_client.open(remote_target, "wb"))
                        data = await src_file.read()
                        await dest_file.write(data)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Source not found")
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
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
                text_content = await _local_read_text(normalized_path, deps.settings.max_upload_bytes)

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
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
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
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

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
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="File not found")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="saved", path=target_path)

        try:
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    async with await sftp_client.open(validated_path, "w", encoding="utf-8") as remote_file:
                        await remote_file.write(content)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

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
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(status_code=413, detail="File too large to download via API")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        filename = PurePosixPath(validated_path).name
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(status_code=413, detail="File too large to view")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        mime, _ = mimetypes.guess_type(validated_path)
        return Response(
            content=content,
            media_type=mime or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )

    MAX_DIR_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard limit

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
                raise HTTPException(status_code=400, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return {"size_bytes": size}

        # Remote
        try:
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                result = await asyncio.wait_for(
                    connection.run(
                        f"du -sb {shlex.quote(validated_path)} 2>/dev/null || echo '0\t'",
                    ),
                    timeout=30,
                )
                output = (result.stdout or "").strip()
                size = int(output.split("\t")[0]) if output else 0
        except Exception as exc:
            logger.warning(f"Failed to get dir size: {exc}")
            size = 0

        return {"size_bytes": size}

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
                raise HTTPException(status_code=500, detail=str(exc))

            dirname = base.name or "download"
            return FileResponse(
                tmp_path,
                media_type="application/zip",
                filename=f"{dirname}.zip",
                background=_cleanup_temp(tmp_path),
            )

        # Remote — pipe zip through SSH
        try:
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        dirname = PurePosixPath(validated_path).name or "download"
        parent = PurePosixPath(validated_path).parent.as_posix()
        cmd = f"cd {shlex.quote(parent)} && zip -r -q - {shlex.quote(dirname)}"

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                result = await asyncio.wait_for(
                    connection.run(cmd, encoding=None),
                    timeout=300,
                )
                if result.exit_status != 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"zip failed: {(result.stderr or b'').decode(errors='replace')}",
                    )
                content = result.stdout or b""
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Download timed out")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if len(content) > MAX_DIR_DOWNLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Directory too large to download")

        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{dirname}.zip"'},
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
            validated_path = PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    attrs = await sftp_client.stat(validated_path)
                    is_dir = stat_mod.S_ISDIR(attrs.permissions) if attrs.permissions else False
                    is_file = stat_mod.S_ISREG(attrs.permissions) if attrs.permissions else False
                    return {"exists": True, "is_directory": is_dir, "is_file": is_file}
                except (FileNotFoundError, OSError):
                    return {"exists": False, "is_directory": False, "is_file": False}
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/boxes/{name}/upload", response_model=APISimpleMessage)
    async def api_upload(
        request: Request,
        name: str,
        directory: str = Form(...),
        file: UploadFile = File(...),
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
                raise HTTPException(status_code=400, detail=str(exc))
            path_obj = Path(target_path)
            if path_obj.exists():
                raise HTTPException(status_code=400, detail="File already exists")
            try:
                await _local_write_bytes(target_path, contents)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="uploaded", path=target_path)

        directory_path = _normalize_directory_path(directory)
        try:
            validated_filename = PathValidator.validate_filename(candidate_name)
            remote_path = _compose_remote_child_path(directory_path, validated_filename)
            PathValidator.validate_remote_path(remote_path)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                sftp_client = await connection.start_sftp_client()
                try:
                    try:
                        await sftp_client.stat(remote_path)
                        raise HTTPException(status_code=400, detail="File already exists")
                    except Exception:
                        pass
                    async with await sftp_client.open(remote_path, "wb") as remote_file:
                        await remote_file.write(contents)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

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
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid octal mode")
        if mode < 0 or mode > 0o7777:
            raise HTTPException(status_code=400, detail="Mode out of range")

        if box.transport == "local":
            target = Path(_normalize_local_path(payload.path))
            if not target.exists():
                raise HTTPException(status_code=404, detail="File not found")
            try:
                target.chmod(mode)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))
            return APISimpleMessage(status="ok", message="chmod applied", path=str(target))

        try:
            validated_path = PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                await sftp_chmod(connection, validated_path, mode)
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return APISimpleMessage(status="ok", message="chmod applied", path=validated_path)

    return router
