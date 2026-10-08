from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import os
import posixpath
import shlex
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
from ..ssh import SSHError, sftp_chmod, sftp_list_directory
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import (
    IMAGE_CONTENT_TYPES,
    MAX_IMAGE_PREVIEW_BYTES,
    _compose_local_child_path,
    _compose_remote_child_path,
    _is_markdown_file,
    _local_delete_file,
    _local_read_bytes,
    _local_read_text,
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
from .rate_limiting import rate_limit_delete, rate_limit_upload, rate_limit_write

logger = logging.getLogger(__name__)

class _TempFileResponse(FileResponse):
    """A ``FileResponse`` that deletes its file once the response is over.

    A ``background`` task is not enough: Starlette 0.48 answers a malformed or
    unsatisfiable ``Range`` with an early ``return`` that skips it, and a client
    disconnect raises out of ``send`` before it runs. ``finally`` covers every outcome.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


async def _get_gitignored_names(directory: str, names: list[str]) -> set[str]:
    """Check which filenames in a directory are gitignored. Returns set of ignored names."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "check-ignore", "--stdin",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=directory,
        )
        input_data = "\n".join(names).encode()
        stdout, _ = await proc.communicate(input=input_data)
        if proc.returncode not in (0, 1):  # 1 means none matched, 128 means not a git repo
            return set()
        return set(line.strip() for line in stdout.decode().splitlines() if line.strip())
    except Exception:
        return set()


async def _resolve_remote_tilde(connection, path: str) -> str:
    """Expand a leading ``~`` / ``~/`` to the remote home directory.

    The remote path is quoted for the shell later, which stops the remote shell
    from expanding ``~``; resolving it here keeps validation, ``du`` and ``zip``
    all working on the real folder.
    """
    if path != "~" and not path.startswith("~/"):
        return path
    result = await asyncio.wait_for(connection.run('printf %s "$HOME"'), timeout=10)
    home = (result.stdout or "").strip()
    if result.exit_status != 0 or not home.startswith("/"):
        raise HTTPException(status_code=502, detail="Could not resolve the remote home directory")
    # Join with a plain "/" (posixpath.join would drop `home` when the rest starts
    # with "/", as in `~//etc`), then normalise; the caller validates the result.
    return home if path == "~" else posixpath.normpath(f"{home}/{path[2:]}")


async def _remote_path(connection, path: str) -> str:
    """``path`` with ``~`` resolved to the remote home, validated (raises
    ``ValidationError``). SFTP does not expand ``~``: it would be a literal folder."""
    return PathValidator.validate_remote_path(await _resolve_remote_tilde(connection, path))


async def _remote_directory(connection, directory: str | None) -> str:
    """A remote folder with ``~`` resolved the way the listing does, then normalised.
    Without the lookup ``_normalize_directory_path`` maps ``~`` to ``/``."""
    resolved = await _resolve_remote_tilde(connection, (directory or "/").strip())
    return _normalize_directory_path(resolved)


def _compose_remote_child(directory: str, filename: str) -> str:
    """``directory/filename``, validated; a bad result is a 400."""
    try:
        return PathValidator.validate_remote_path(_compose_remote_child_path(directory, filename))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_ALREADY_EXISTS = "File already exists"
_NOT_REGULAR = "Only a regular file can be replaced"


async def _remote_write_new(
    sftp_client, remote_path: str, data: bytes, *, overwrite: bool
) -> None:
    """Write ``data`` to ``remote_path`` without ever replacing what is there, unless
    ``overwrite`` is set and it is a regular file.

    Only "no such file" counts as free: ``lstat`` does not follow links, so a dangling
    symlink is an existing name, and any other failure (permission denied) is a 400. A
    free name is opened exclusively ("xb", ``O_CREAT|O_EXCL``), so a file that appears
    after the check is refused rather than truncated."""
    try:
        attrs = await sftp_client.lstat(remote_path)
    except asyncssh.SFTPNoSuchFile:
        mode = "xb"
    except asyncssh.SFTPError as exc:
        raise HTTPException(
            status_code=400, detail=f"Cannot check whether {remote_path} exists: {exc.reason}"
        ) from exc
    else:
        if not overwrite:
            raise HTTPException(status_code=409, detail=_ALREADY_EXISTS)
        if attrs.permissions is None or not stat_mod.S_ISREG(attrs.permissions):
            raise HTTPException(status_code=400, detail=_NOT_REGULAR)
        mode = "wb"
    try:
        remote_file = await sftp_client.open(remote_path, mode)
    except (asyncssh.SFTPFileAlreadyExists, asyncssh.SFTPFailure) as exc:
        if mode != "xb":
            raise
        # OpenSSH answers an exclusive open on an existing name with FX_FAILURE.
        raise HTTPException(status_code=409, detail=_ALREADY_EXISTS) from exc
    async with remote_file:
        await remote_file.write(data)


async def _local_write_new(
    target_path: str, data: bytes, *, overwrite: bool, exists_status: int
) -> None:
    """Local twin of ``_remote_write_new``: ``lexists`` (a dangling link is taken), an
    exclusive create for a free name, and replace only a regular file with ``overwrite``.
    An existing name answers ``exists_status``."""

    def _worker() -> None:
        path = Path(target_path)
        if os.path.lexists(path):
            if not overwrite:
                raise HTTPException(status_code=exists_status, detail=_ALREADY_EXISTS)
            if path.is_symlink() or not path.is_file():
                raise HTTPException(status_code=400, detail=_NOT_REGULAR)
            mode = "wb"
        else:
            mode = "xb"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, mode) as handle:
                handle.write(data)
        except FileExistsError as exc:
            raise HTTPException(status_code=exists_status, detail=_ALREADY_EXISTS) from exc

    await asyncio.to_thread(_worker)


def _attachment_header(filename: str) -> str:
    """``Content-Disposition`` for a download, safe for any file name.

    ``filename*`` carries the exact name percent-encoded as UTF-8 (RFC 5987/6266);
    ``filename`` is an ASCII fallback with ``"``, ``\\``, ``%`` and non-ASCII or
    control characters replaced, so a Japanese or quoted name cannot break the header.
    """
    fallback = "".join(
        ch if 0x20 <= ord(ch) < 0x7F and ch not in '"\\%' else "_" for ch in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8\'\'{quote(filename, safe='')}"


async def _du_bytes(connection, command: str, multiplier: int) -> int | None:
    """Run a ``du`` command and return its total times ``multiplier``, or None.

    ``du`` exits non-zero when a subfolder is unreadable but still prints the
    total, so the printed number decides, not the exit status.
    """
    result = await asyncio.wait_for(connection.run(command), timeout=30)
    first_field = (result.stdout or "").strip().split("\t")[0]
    return int(first_field) * multiplier if first_field.isdigit() else None


# Remote folder zips are streamed to a temp file and aborted past this size,
# whatever `du` reported. Read at call time so tests can lower them.
MAX_DIR_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard limit
ZIP_STREAM_CHUNK_BYTES = 1024 * 1024
ZIP_STREAM_TIMEOUT_SECONDS = 300
ZIP_STDERR_KEEP_BYTES = 64 * 1024  # tail of zip's stderr kept for the error message


class _ZipTooLargeError(Exception):
    """The remote zip passed ``MAX_DIR_DOWNLOAD_BYTES``; its process was killed."""


async def _drain_tail(stream, chunk_size: int) -> bytes:
    """Read ``stream`` to EOF in chunks; return its last ``ZIP_STDERR_KEEP_BYTES``."""
    tail = b""
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            return tail
        tail = (tail + chunk)[-ZIP_STDERR_KEEP_BYTES:]


async def _spool_remote_zip(connection, command: str, dest: str) -> None:
    """Run ``command`` and write its stdout to ``dest`` one chunk at a time.

    At most one chunk is held in memory. Past ``MAX_DIR_DOWNLOAD_BYTES`` the remote
    process is killed and ``_ZipTooLargeError`` raised; the caller must not pool the
    connection afterwards. A non-zero exit raises ``HTTPException(500)`` with stderr.
    """
    limit = MAX_DIR_DOWNLOAD_BYTES
    chunk_size = ZIP_STREAM_CHUNK_BYTES
    process = await connection.create_process(command, encoding=None)
    # stdout and stderr share one SSH channel window: stderr is read alongside
    # stdout, or a zip with many warnings stalls until the timeout.
    stderr_task = asyncio.ensure_future(_drain_tail(process.stderr, chunk_size))
    # Mark a reader error as retrieved when an abort leaves it unawaited.
    stderr_task.add_done_callback(lambda t: t.cancelled() or t.exception())
    try:
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = await process.stdout.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    with contextlib.suppress(Exception):
                        process.kill()
                    raise _ZipTooLargeError
                out.write(chunk)
        stderr = await stderr_task
        completed = await process.wait()
    finally:
        # A no-op once drained; on an abort the reader is stopped, not awaited, so an
        # outer cancel is never swallowed.
        stderr_task.cancel()
        process.close()
    if completed.exit_status != 0:
        message = stderr.decode(errors="replace")
        raise HTTPException(status_code=500, detail=f"zip failed: {message}")


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

            # Check gitignored status for all entries in one batch
            ignored_names: set[str] = set()
            if children:
                ignored_names = await _get_gitignored_names(
                    normalized, [c.name for c, _ in children]
                )

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
                        gitignored=child.name in ignored_names,
                    )
                )
            return APIDirectoryListing(box=box.name, directory=normalized, entries=entries)

        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                # `~` is the remote home, as for the folder zip (not `/`).
                normalized_remote = await _remote_directory(connection, directory)
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
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="renamed", path=str(target))

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
                validated_path = await _remote_path(connection, payload.path)
                target_path = str(PurePosixPath(validated_path).parent / new_name)
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.rename(validated_path, target_path)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, payload.directory), validated_filename
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_write_new(sftp_client, remote_path, b"", overwrite=False)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, payload.directory), validated_name
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    try:
                        await sftp_client.stat(remote_path)
                        raise HTTPException(status_code=400, detail="Directory already exists")
                    except HTTPException:
                        raise
                    except Exception:
                        pass
                    await sftp_client.mkdir(remote_path)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                validated_path = await _remote_path(connection, payload.path)
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.remove(validated_path)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="moved", path=str(target))

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
                remote_src = await _remote_path(connection, payload.source)
                remote_dest_dir = await _remote_path(connection, payload.destination)
                remote_target = str(
                    PurePosixPath(remote_dest_dir) / PurePosixPath(remote_src).name
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.rename(remote_src, remote_target)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
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
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="copied", path=str(target))

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
                remote_src = await _remote_path(connection, payload.source)
                remote_dest_dir = await _remote_path(connection, payload.destination)
                # Named after the resolved source: `~` alone has no usable name.
                new_name = PathValidator.validate_filename(
                    payload.new_name or PurePosixPath(remote_src).name
                )
                remote_target = str(PurePosixPath(remote_dest_dir) / new_name)
                sftp_client = await connection.start_sftp_client()
                try:
                    async with contextlib.AsyncExitStack() as stack:
                        src_file = await stack.enter_async_context(
                            await sftp_client.open(remote_src, "rb")
                        )
                        dest_file = await stack.enter_async_context(
                            await sftp_client.open(remote_target, "wb")
                        )
                        data = await src_file.read()
                        await dest_file.write(data)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
        except FileNotFoundError as exc:
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
                validated_path = await _remote_path(connection, path)
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
                validated_path = await _remote_path(connection, path)
                sftp_client = await connection.start_sftp_client()
                try:
                    async with await sftp_client.open(
                        validated_path, "w", encoding="utf-8"
                    ) as remote_file:
                        await remote_file.write(content)
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                validated_path = await _remote_path(connection, path)
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
                validated_path = await _remote_path(connection, path)
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
                validated_path = await _remote_path(connection, path)
                quoted = shlex.quote(validated_path)
                remote_size = await _du_bytes(connection, f"du -sb {quoted}", 1)
                if remote_size is None:
                    # BSD/macOS `du` has no -b; -k works everywhere.
                    remote_size = await _du_bytes(connection, f"du -sk {quoted}", 1024)
                if remote_size is None:
                    exists = await asyncio.wait_for(
                        connection.run(f"test -d {quoted}"), timeout=10
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
                validated_path = await _remote_path(connection, path)
                if validated_path == "/":
                    # No folder name to zip: archive the contents of `/` as "root".
                    dirname, parent, target = "root", "/", "."
                else:
                    dirname = PurePosixPath(validated_path).name
                    parent = PurePosixPath(validated_path).parent.as_posix()
                    target = dirname
                cmd = f"cd {shlex.quote(parent)} && zip -r -q - {shlex.quote(target)}"
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
            raise HTTPException(
                status_code=413, detail="Directory too large to download"
            ) from exc
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
                validated_path = await _remote_path(connection, path)
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
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/boxes/{name}/upload", response_model=APISimpleMessage)
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
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, directory), validated_filename
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_write_new(
                        sftp_client, remote_path, contents, overwrite=overwrite
                    )
                finally:
                    with contextlib.suppress(Exception):
                        await sftp_client.exit()  # type: ignore[func-returns-value]
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
                validated_path = await _remote_path(connection, payload.path)
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

    return router
