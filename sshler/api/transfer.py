"""Cross-box file transfer with SSE progress streaming."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path
from .rate_limiting import rate_limit_transfer

logger = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024  # 256 KB
MAX_TRANSFER_PATHS = 100


class APITransferRequest(BaseModel):
    src_box: str
    dest_box: str
    paths: list[str] = Field(..., max_length=MAX_TRANSFER_PATHS)
    destination: str
    mode: str = "copy"  # "copy" or "move"


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["transfer"])

    @router.post("/transfer")
    async def transfer_files(
        request: Request,
        payload: APITransferRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_transfer),
    ):
        """Stream cross-box file transfer with SSE progress events."""

        if payload.src_box == payload.dest_box:
            raise HTTPException(status_code=400, detail="Use batch copy/move for same-box transfers")

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

        validated_paths: list[str] = []
        for path in payload.paths:
            try:
                if src_box.transport == "local":
                    validated_paths.append(_normalize_local_path(path))
                else:
                    validated_paths.append(PathValidator.validate_remote_path(path))
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid path {path}: {exc}") from exc

        try:
            if dest_box.transport == "local":
                validated_dest = _normalize_local_path(payload.destination)
            else:
                validated_dest = PathValidator.validate_remote_path(payload.destination)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}") from exc

        async def event_generator():
            succeeded: list[str] = []
            failed: list[dict] = []
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
                        src_sftp = await src_conn.start_sftp_client()
                        stack.push_async_callback(_safe_exit_sftp, src_sftp)

                    if dest_box.transport != "local":
                        dest_conn = await stack.enter_async_context(
                            ssh_pool.connection(
                                dest_box,
                                lambda: deps.connect_for_box(dest_box, application_config),
                            )
                        )
                        dest_sftp = await dest_conn.start_sftp_client()
                        stack.push_async_callback(_safe_exit_sftp, dest_sftp)

                    for index, src_path in enumerate(validated_paths):
                        if await request.is_disconnected():
                            break

                        filename = (
                            Path(src_path).name
                            if src_box.transport == "local"
                            else PurePosixPath(src_path).name
                        )
                        dest_path = (
                            str(Path(validated_dest) / filename)
                            if dest_box.transport == "local"
                            else str(PurePosixPath(validated_dest) / filename)
                        )

                        try:
                            file_size = await _get_file_size(src_box, src_sftp, src_path)
                            async for event in _copy_one_file(
                                src_box,
                                src_sftp,
                                dest_box,
                                dest_sftp,
                                src_path,
                                dest_path,
                                file_size,
                                index,
                                total,
                                request,
                            ):
                                yield event

                            if await request.is_disconnected():
                                break

                            succeeded.append(src_path)
                        except Exception as exc:
                            logger.warning(f"Transfer failed for {src_path}: {exc}")
                            failed.append({"path": src_path, "error": str(exc)})

                    if payload.mode == "move" and succeeded and not await request.is_disconnected():
                        for src_path in succeeded:
                            try:
                                await _delete_source(src_box, src_sftp, src_path)
                            except Exception as exc:
                                logger.warning(f"Delete after move failed for {src_path}: {exc}")

            except SSHError as exc:
                yield _sse_event("error", {"detail": f"SSH error: {exc}"})
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


async def _safe_exit_sftp(sftp_client) -> None:
    with contextlib.suppress(Exception):
        await sftp_client.exit()


async def _get_file_size(box, sftp_client, path: str) -> int:
    if box.transport == "local":
        return Path(path).stat().st_size
    attrs = await sftp_client.stat(path)
    return attrs.size or 0


async def _copy_one_file(
    src_box,
    src_sftp,
    dest_box,
    dest_sftp,
    src_path: str,
    dest_path: str,
    file_size: int,
    index: int,
    total: int,
    request: Request,
):
    """Copy a single file with chunked streaming. Yields SSE progress events."""
    if await request.is_disconnected():
        return

    bytes_done = 0

    if dest_box.transport == "local":
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    else:
        dest_dir = str(PurePosixPath(dest_path).parent)
        try:
            await dest_sftp.mkdir(dest_dir)
        except Exception:
            pass

    if src_box.transport == "local" and dest_box.transport == "local":
        await asyncio.to_thread(shutil.copy2, src_path, dest_path)
        if await request.is_disconnected():
            return
        yield _sse_event(
            "progress",
            {
                "file": src_path,
                "bytes_done": file_size,
                "bytes_total": file_size,
                "index": index,
                "total": total,
            },
        )
        return

    if src_box.transport == "local":
        with open(src_path, "rb") as src_fh:
            async with await dest_sftp.open(dest_path, "wb") as dest_fh:
                while True:
                    if await request.is_disconnected():
                        return
                    chunk = await asyncio.to_thread(src_fh.read, CHUNK_SIZE)
                    if not chunk:
                        break
                    await dest_fh.write(chunk)
                    bytes_done += len(chunk)
                    yield _sse_event(
                        "progress",
                        {
                            "file": src_path,
                            "bytes_done": bytes_done,
                            "bytes_total": file_size,
                            "index": index,
                            "total": total,
                        },
                    )
        return

    if dest_box.transport == "local":
        async with await src_sftp.open(src_path, "rb") as src_fh:
            with open(dest_path, "wb") as dest_fh:
                while True:
                    if await request.is_disconnected():
                        return
                    chunk = await src_fh.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    await asyncio.to_thread(dest_fh.write, chunk)
                    bytes_done += len(chunk)
                    yield _sse_event(
                        "progress",
                        {
                            "file": src_path,
                            "bytes_done": bytes_done,
                            "bytes_total": file_size,
                            "index": index,
                            "total": total,
                        },
                    )
        return

    async with await src_sftp.open(src_path, "rb") as src_fh:
        async with await dest_sftp.open(dest_path, "wb") as dest_fh:
            while True:
                if await request.is_disconnected():
                    return
                chunk = await src_fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                await dest_fh.write(chunk)
                bytes_done += len(chunk)
                yield _sse_event(
                    "progress",
                    {
                        "file": src_path,
                        "bytes_done": bytes_done,
                        "bytes_total": file_size,
                        "index": index,
                        "total": total,
                    },
                )


async def _delete_source(box, sftp_client, path: str) -> None:
    if box.transport == "local":
        Path(path).unlink()
    else:
        await sftp_client.remove(path)
