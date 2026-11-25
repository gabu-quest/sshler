"""Cross-box file transfer with SSE progress streaming."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool

from ..config import AppConfig
from ..validation import ValidationError
from .box_files import box_files
from .dependencies import APIDependencies
from .failure import REASON_INVALID, Failure, failure_body, failure_entry
from .file_contract import BoxFiles
from .placement import refuse_a_name_this_request_placed, source_not_removed
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
        sources whose copy landed. A transfer that fails as a whole after the stream
        began ends with one ``error`` event, the body a failed request would answer:
        ``detail`` and ``reason``."""

        if payload.src_box == payload.dest_box:
            raise Failure(REASON_INVALID, "Use batch copy/move for same-box transfers")

        if not payload.paths:
            raise Failure(REASON_INVALID, "No paths provided")

        if len(payload.paths) > MAX_TRANSFER_PATHS:
            raise Failure(REASON_INVALID, f"Maximum {MAX_TRANSFER_PATHS} paths per transfer")

        if payload.mode not in ("copy", "move"):
            raise Failure(REASON_INVALID, "mode must be 'copy' or 'move'")

        src_box = deps.get_box_or_404(application_config, payload.src_box)
        dest_box = deps.get_box_or_404(application_config, payload.dest_box)

        # Neither block reaches its box: the checks need no connection.
        async with (
            box_files(deps, src_box, application_config, answers=None) as source,
            box_files(deps, dest_box, application_config, answers=None) as dest,
        ):

            def _validate() -> tuple[list[str], str]:
                """Check every path, then the destination, in a worker thread (a local
                path is normalised, links resolved). A remote path is kept as spelled:
                its ``~`` is resolved on that box's connection."""
                paths: list[str] = []
                for path in payload.paths:
                    try:
                        paths.append(source.check_path(path))
                    except (ValidationError, ValueError) as exc:
                        raise Failure(REASON_INVALID, f"Invalid path {path}: {exc}") from exc
                try:
                    checked_dest = dest.check_path(payload.destination)
                except (ValidationError, ValueError) as exc:
                    raise Failure(REASON_INVALID, f"Invalid destination: {exc}") from exc
                return paths, checked_dest

            validated_paths, validated_dest = await asyncio.to_thread(_validate)

        async def event_generator():
            succeeded: list[str] = []
            failed: list[dict] = []
            landed_at: dict[str, str] = {}
            landed: set[str] = set()
            copied_from: dict[str, tuple[str, tuple]] = {}
            total = len(validated_paths)

            try:
                async with (
                    box_files(deps, src_box, application_config, answers=None) as source,
                    box_files(deps, dest_box, application_config, answers=None) as dest,
                ):
                    # Both boxes are reached before the first item, the source first: a
                    # box that cannot be reached is the one `error` event of the request.
                    await source.prepare()
                    await dest.prepare()
                    # `~` is each remote side's own home; the events and answers below
                    # still name each item as the request spelled it.
                    source_paths = [await source.resolve(p) for p in validated_paths]
                    destination = await dest.resolve(validated_dest)

                    for index, src_path in enumerate(source_paths):
                        # Every answer and event names the item as the request spelled
                        # it; the work runs on the validated path.
                        sent_path = payload.paths[index]
                        if await request.is_disconnected():
                            break

                        dest_path = dest.join(destination, source.name(src_path))

                        try:
                            refuse_a_name_this_request_placed(dest_path, landed)
                            found = await source.source(src_path)
                            async for event in _copy_one_file(
                                source,
                                dest,
                                src_path,
                                dest_path,
                                index,
                                total,
                                request,
                                sent_path=sent_path,
                                source_mode=found.mode,
                                file_size=found.size,
                                overwrite=payload.overwrite,
                            ):
                                yield event

                            if await request.is_disconnected():
                                break

                            succeeded.append(sent_path)
                            landed_at[sent_path] = dest_path
                            landed.add(dest_path)
                            copied_from[sent_path] = (src_path, found.fingerprint)
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
                                await source.remove_if_unchanged(
                                    src_path, fingerprint, landed_at[sent_path]
                                )
                            except Failure as exc:
                                succeeded.remove(sent_path)
                                failed.append(failure_entry(sent_path, exc))
                            except Exception as exc:
                                logger.warning(f"Delete after move failed for {src_path}: {exc}")
                                succeeded.remove(sent_path)
                                not_removed = source_not_removed(landed_at[sent_path], str(exc))
                                failed.append(failure_entry(sent_path, not_removed))

            except Exception as exc:
                yield _sse_event("error", failure_body(exc))
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


async def _copy_one_file(
    source: BoxFiles,
    dest: BoxFiles,
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

    landing = await dest.land(dest_path, source_mode=source_mode, overwrite=overwrite)
    committed = False
    try:
        bytes_done = 0
        async with source.open_read(src_path) as read:
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
