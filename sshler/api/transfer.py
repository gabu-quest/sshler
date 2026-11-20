"""Cross-box file transfer with SSE progress streaming."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat as stat_mod
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool

from ..config import AppConfig
from ..ssh import SSHError, sftp_session
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path
from .local_files import _local_landing, local_fingerprint
from .placement import (
    REASON_FAILED,
    ensure_source_unchanged,
    failure_entry,
    refuse_a_name_this_request_placed,
    run_to_end,
)
from .rate_limiting import rate_limit_transfer
from .remote import remote_path
from .remote_files import _remote_landing, remote_fingerprint

logger = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024  # 256 KB
MAX_TRANSFER_PATHS = 100


class APITransferRequest(BaseModel):
    src_box: str
    dest_box: str
    paths: list[str] = Field(..., max_length=MAX_TRANSFER_PATHS)
    destination: str
    mode: str = "copy"  # "copy" or "move"
    # Applies to every file: replace a regular file that has the file's name.
    overwrite: StrictBool = False


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["transfer"])

    @router.post("/transfer")
    async def transfer_files(
        request: Request,
        payload: APITransferRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_transfer),
    ):
        """Stream cross-box file transfer with SSE progress events.

        The ``done`` event lists ``failed`` entries as a batch does: ``path``, ``error``
        and ``reason`` ("exists", "cannot_replace" or "failed"). A move removes only the
        sources whose copy landed."""

        if payload.src_box == payload.dest_box:
            raise HTTPException(
                status_code=400, detail="Use batch copy/move for same-box transfers"
            )

        if not payload.paths:
            raise HTTPException(status_code=400, detail="No paths provided")

        if len(payload.paths) > MAX_TRANSFER_PATHS:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_TRANSFER_PATHS} paths per transfer",
            )

        if payload.mode not in ("copy", "move"):
            raise HTTPException(status_code=400, detail="mode must be 'copy' or 'move'")

        src_box = deps.get_box_or_404(application_config, payload.src_box)
        dest_box = deps.get_box_or_404(application_config, payload.dest_box)

        def _validate() -> tuple[list[str], str]:
            """Check every path (a local one is normalised, links resolved: a worker-thread
            job). A remote path is checked and kept as spelled: its ``~`` is resolved on
            that box's connection."""
            paths: list[str] = []
            for path in payload.paths:
                try:
                    if src_box.transport == "local":
                        paths.append(_normalize_local_path(path))
                    else:
                        PathValidator.validate_remote_path(path)
                        paths.append(path)
                except (ValidationError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400, detail=f"Invalid path {path}: {exc}"
                    ) from exc
            try:
                if dest_box.transport == "local":
                    dest = _normalize_local_path(payload.destination)
                else:
                    PathValidator.validate_remote_path(payload.destination)
                    dest = payload.destination
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}") from exc
            return paths, dest

        validated_paths, validated_dest = await asyncio.to_thread(_validate)

        async def event_generator():
            succeeded: list[str] = []
            failed: list[dict] = []
            landed_at: dict[str, str] = {}
            landed: set[str] = set()
            copied_from: dict[str, tuple[str, tuple]] = {}
            total = len(validated_paths)
            ssh_pool = get_pool()

            try:
                async with contextlib.AsyncExitStack() as stack:
                    src_sftp = None
                    dest_sftp = None

                    if src_box.transport != "local":
                        src_conn = await stack.enter_async_context(
                            ssh_pool.connection(
                                src_box,
                                lambda: deps.connect_for_box(src_box, application_config),
                            )
                        )
                        src_sftp = await stack.enter_async_context(sftp_session(src_conn))

                    if dest_box.transport != "local":
                        dest_conn = await stack.enter_async_context(
                            ssh_pool.connection(
                                dest_box,
                                lambda: deps.connect_for_box(dest_box, application_config),
                            )
                        )
                        dest_sftp = await stack.enter_async_context(sftp_session(dest_conn))

                    # `~` is each remote side's own home; the events and answers below
                    # still name each item as the request spelled it.
                    source_paths = validated_paths
                    if src_sftp is not None:
                        source_paths = [await remote_path(src_conn, p) for p in source_paths]
                    destination = validated_dest
                    if dest_sftp is not None:
                        destination = await remote_path(dest_conn, destination)

                    for index, src_path in enumerate(source_paths):
                        # Every answer and event names the item as the request spelled
                        # it; the work runs on the validated path.
                        sent_path = payload.paths[index]
                        if await request.is_disconnected():
                            break

                        filename = (
                            Path(src_path).name
                            if src_box.transport == "local"
                            else PurePosixPath(src_path).name
                        )
                        dest_path = (
                            str(Path(destination) / filename)
                            if dest_box.transport == "local"
                            else str(PurePosixPath(destination) / filename)
                        )

                        try:
                            refuse_a_name_this_request_placed(dest_path, landed)
                            source_mode, file_size, fingerprint = await _source_stat(
                                src_box, src_sftp, src_path
                            )
                            async for event in _copy_one_file(
                                src_box,
                                src_sftp,
                                dest_box,
                                dest_sftp,
                                src_path,
                                dest_path,
                                index,
                                total,
                                request,
                                sent_path=sent_path,
                                source_mode=source_mode,
                                file_size=file_size,
                                overwrite=payload.overwrite,
                            ):
                                yield event

                            if await request.is_disconnected():
                                break

                            succeeded.append(sent_path)
                            landed_at[sent_path] = dest_path
                            landed.add(dest_path)
                            copied_from[sent_path] = (src_path, fingerprint)
                        except Exception as exc:
                            logger.warning(f"Transfer failed for {src_path}: {exc}")
                            entry = failure_entry(sent_path, exc)
                            failed.append(entry)
                            # Said at once: no progress event follows for this item, and
                            # the next item's would otherwise read as this one finished.
                            yield _sse_event("failed", entry)

                    if payload.mode == "move" and succeeded and not await request.is_disconnected():
                        for sent_path in list(succeeded):
                            src_path, fingerprint = copied_from[sent_path]
                            try:
                                await _delete_source(
                                    src_box,
                                    src_sftp,
                                    src_path,
                                    fingerprint,
                                    landed_at[sent_path],
                                )
                            except HTTPException as exc:
                                succeeded.remove(sent_path)
                                failed.append(failure_entry(sent_path, exc))
                            except Exception as exc:
                                logger.warning(f"Delete after move failed for {src_path}: {exc}")
                                succeeded.remove(sent_path)
                                failed.append(
                                    {
                                        "path": sent_path,
                                        "error": f"Copied to {landed_at[sent_path]}, but the "
                                        f"source could not be removed: {exc}",
                                        "reason": REASON_FAILED,
                                    }
                                )

            except SSHError as exc:
                yield _sse_event("error", {"detail": f"SSH error: {exc}"})
                return
            except HTTPException as exc:
                yield _sse_event("error", {"detail": str(exc.detail)})
                return
            except Exception as exc:
                yield _sse_event("error", {"detail": str(exc)})
                return

            if not await request.is_disconnected():
                yield _sse_event("done", {"succeeded": succeeded, "failed": failed})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _source_stat(box, sftp_client, path: str) -> tuple[int, int, tuple]:
    """``(mode, size, fingerprint)`` of a source, links followed; the fingerprint is
    what a move compares before it removes the source."""
    if box.transport == "local":
        st = await asyncio.to_thread(os.stat, path)
        return st.st_mode, st.st_size, local_fingerprint(st)
    attrs = await sftp_client.stat(path)
    return attrs.permissions or 0, attrs.size or 0, remote_fingerprint(attrs)


@contextlib.asynccontextmanager
async def _source_reader(
    box, sftp_client, path: str
) -> AsyncIterator[Callable[[int], Awaitable[bytes]]]:
    """Yields ``read(n)`` for a source file; local reads run in a worker thread."""
    if box.transport == "local":
        handle = await asyncio.to_thread(open, path, "rb")
        try:
            yield lambda n: asyncio.to_thread(handle.read, n)
        finally:
            await asyncio.to_thread(handle.close)
    else:
        async with await sftp_client.open(path, "rb") as handle:
            yield handle.read


class _ThreadedLanding:
    """The async face of a ``_LocalLanding``: each call runs in a worker thread."""

    def __init__(self, landing) -> None:
        self.landing = landing

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self.landing.write, data)

    async def commit(self) -> None:
        await asyncio.to_thread(self.landing.commit)

    async def abort(self) -> None:
        """To the end even if the request is cancelled: a worker-thread job cancelled
        before it starts never runs."""
        await run_to_end(asyncio.to_thread(self.landing.abort))


async def _open_landing(
    box,
    sftp_client,
    path: str,
    *,
    overwrite: bool,
    source_regular: bool,
    source_mode: int | None = None,
):
    """The destination file, decided by the rule, its folder made if missing.

    A replaced file keeps its permission bits. A free name gets ``source_mode``'s nine
    permission bits whatever the destination's umask (setuid, setgid and sticky are
    never carried), from before the first byte; without ``source_mode`` it gets the
    destination's default bits."""
    if box.transport == "local":
        await asyncio.to_thread(os.makedirs, os.path.dirname(path), exist_ok=True)
        landing = await asyncio.to_thread(
            _local_landing,
            path,
            overwrite=overwrite,
            source_regular=source_regular,
            source_mode=source_mode,
        )
        return _ThreadedLanding(landing)
    with contextlib.suppress(Exception):
        await sftp_client.mkdir(str(PurePosixPath(path).parent))
    return await _remote_landing(
        sftp_client,
        path,
        overwrite=overwrite,
        source_regular=source_regular,
        source_mode=source_mode,
    )


async def _copy_one_file(
    src_box,
    src_sftp,
    dest_box,
    dest_sftp,
    src_path: str,
    dest_path: str,
    index: int,
    total: int,
    request: Request,
    *,
    sent_path: str,
    source_mode: int,
    file_size: int,
    overwrite: bool,
):
    """Copy a single file with chunked streaming. Yields SSE progress events.

    The destination follows the rule (``DestinationRefused`` for a taken name); a copy
    that does not finish (an error, a closed browser) removes what it wrote."""
    if await request.is_disconnected():
        return

    landing = await _open_landing(
        dest_box,
        dest_sftp,
        dest_path,
        overwrite=overwrite,
        source_regular=stat_mod.S_ISREG(source_mode),
        source_mode=source_mode,
    )
    committed = False
    try:
        bytes_done = 0
        async with _source_reader(src_box, src_sftp, src_path) as read:
            while True:
                if await request.is_disconnected():
                    return
                chunk = await read(CHUNK_SIZE)
                if not chunk:
                    break
                await landing.write(chunk)
                bytes_done += len(chunk)
                yield _sse_event(
                    "progress",
                    {
                        "file": sent_path,
                        "bytes_done": bytes_done,
                        "bytes_total": file_size,
                        "index": index,
                        "total": total,
                    },
                )
        await landing.commit()
        committed = True
    finally:
        if not committed:
            await landing.abort()


async def _delete_source(box, sftp_client, path: str, before: tuple, target: str) -> None:
    """Remove a moved source, only if it is still the file that was copied to
    ``target`` (``ensure_source_unchanged``)."""
    if box.transport == "local":

        def _check_and_remove() -> None:
            ensure_source_unchanged(before, local_fingerprint(os.stat(path)), target)
            os.unlink(path)

        await asyncio.to_thread(_check_and_remove)
    else:
        ensure_source_unchanged(before, remote_fingerprint(await sftp_client.stat(path)), target)
        await sftp_client.remove(path)
