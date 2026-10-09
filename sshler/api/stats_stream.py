"""SSE streaming endpoint for live box stats.

Connection budget: every open stream (one per overview tab) used to probe
every box with a fresh SSH connection each cycle, and a stream that closed
left its in-flight probes running. Now:

- ``SharedStatsProbes`` keeps at most one probe in flight per box, shared by
  every open stream, so tabs do not multiply connections;
- each probe runs on a pooled connection (``APIDependencies.run_pooled``) and
  is bounded by ``PROBE_TIMEOUT_SECONDS``; a timed-out probe's connection is
  closed, not reused;
- a box named twice in ``?boxes=`` is probed once;
- when the last stream waiting on a probe goes away, the probe is cancelled
  and its connection closed, so a reload never strands one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..config import AppConfig, Box, find_box
from .boxes import _get_local_stats, _get_remote_stats
from .dependencies import APIDependencies, ProbeTimeoutError
from .models import APIBoxStats

logger = logging.getLogger(__name__)

STATS_CYCLE_SECONDS = 10.0
DISCONNECT_POLL_SECONDS = 0.2


@dataclass
class _Inflight:
    task: asyncio.Task[APIBoxStats]
    subscribers: int = 0


class SharedStatsProbes:
    """At most one stats probe in flight per box, shared by all streams.

    ``acquire`` joins the running probe for a box or starts one; ``release``
    leaves it, cancelling the probe when its last subscriber leaves. Both are
    synchronous so a stream's ``finally`` can release even while cancelled.
    """

    def __init__(self, deps: APIDependencies) -> None:
        self._deps = deps
        self._inflight: dict[str, _Inflight] = {}

    def subscriber_count(self, box_name: str) -> int:
        entry = self._inflight.get(box_name)
        return entry.subscribers if entry is not None else 0

    def acquire(self, box: Box, application_config: AppConfig) -> asyncio.Task[APIBoxStats]:
        entry = self._inflight.get(box.name)
        if entry is None or entry.task.done():
            task = asyncio.create_task(_probe_box(self._deps, box, application_config))
            entry = _Inflight(task=task)
            self._inflight[box.name] = entry
        entry.subscribers += 1
        return entry.task

    def release(self, box_name: str, task: asyncio.Task[APIBoxStats]) -> None:
        entry = self._inflight.get(box_name)
        if entry is None or entry.task is not task:
            return  # a newer probe replaced this one; nothing to do
        entry.subscribers -= 1
        if entry.subscribers <= 0:
            del self._inflight[box_name]
            if not task.done():
                task.cancel()


def _stats_event(name: str, task: asyncio.Task[APIBoxStats]) -> str:
    try:
        stats = task.result()
        data = json.dumps(stats.model_dump(), default=str)
    except asyncio.CancelledError:
        data = json.dumps({"name": name, "error": "probe cancelled"})
    except Exception as e:
        data = json.dumps({"name": name, "error": str(e)})
    return f"event: stats\ndata: {data}\n\n"


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["stats-stream"])
    probes = SharedStatsProbes(deps)

    @router.get("/boxes/stats/stream")
    async def stats_stream(
        request: Request,
        boxes: str | None = None,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Stream box stats via SSE. Each box emits independently as results arrive."""

        # Determine which boxes to monitor. Duplicates are collapsed: the
        # stream holds each box's probe once, so a repeated name would
        # subscribe twice and release once, and the probe would never end.
        if boxes:
            box_names = list(dict.fromkeys(b.strip() for b in boxes.split(",") if b.strip()))
        else:
            box_names = list(dict.fromkeys(b.name for b in application_config.boxes))

        async def event_generator():
            held: dict[asyncio.Task[APIBoxStats], str] = {}
            try:
                while True:
                    if await request.is_disconnected():
                        return

                    for name in box_names:
                        box = find_box(application_config, name)
                        if box is not None:
                            held[probes.acquire(box, application_config)] = name

                    # Emit each box as its probe completes; keep watching for
                    # the client leaving while a probe is still running.
                    while held:
                        done, _ = await asyncio.wait(
                            set(held),
                            timeout=DISCONNECT_POLL_SECONDS,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in done:
                            name = held.pop(task)
                            probes.release(name, task)
                            yield _stats_event(name, task)
                        if held and await request.is_disconnected():
                            return

                    # Pause between cycles, checking for disconnect
                    for _ in range(round(STATS_CYCLE_SECONDS / DISCONNECT_POLL_SECONDS)):
                        if await request.is_disconnected():
                            return
                        await asyncio.sleep(DISCONNECT_POLL_SECONDS)
            finally:
                # Runs on disconnect, cancellation or generator close: leave
                # every probe this stream still waits on.
                for task, name in held.items():
                    probes.release(name, task)
                held.clear()

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


async def _probe_box(deps: APIDependencies, box: Box, application_config: AppConfig) -> APIBoxStats:
    """Get stats for a single box. Handles local vs remote."""
    if box.transport == "local":
        return await _get_local_stats(box.name)

    try:
        return await deps.run_pooled(
            box, application_config, lambda conn: _get_remote_stats(box.name, conn)
        )
    except ProbeTimeoutError as e:
        logger.warning(f"Stats probe: {e}")
        return APIBoxStats(name=box.name, error=str(e))
    except Exception as e:
        return APIBoxStats(name=box.name, error=str(e))
