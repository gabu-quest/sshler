"""SSE streaming endpoint for live box stats."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..config import AppConfig, find_box
from .boxes import _get_local_stats, _get_remote_stats
from .dependencies import APIDependencies
from .models import APIBoxStats

logger = logging.getLogger(__name__)


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["stats-stream"])

    @router.get("/boxes/stats/stream")
    async def stats_stream(
        request: Request,
        boxes: str | None = None,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Stream box stats via SSE. Each box emits independently as results arrive."""

        # Determine which boxes to monitor
        if boxes:
            box_names = [b.strip() for b in boxes.split(",") if b.strip()]
        else:
            box_names = [b.name for b in application_config.boxes]

        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break

                # Probe all boxes concurrently, emit each as it completes
                pending = set()
                task_to_name = {}
                for name in box_names:
                    box = find_box(application_config, name)
                    if not box:
                        continue
                    task = asyncio.create_task(_probe_box(deps, box, application_config))
                    pending.add(task)
                    task_to_name[task] = name

                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        name = task_to_name[task]
                        try:
                            stats: APIBoxStats = task.result()
                            data = json.dumps(stats.model_dump(), default=str)
                            yield f"event: stats\ndata: {data}\n\n"
                        except Exception as e:
                            error_data = json.dumps({"name": name, "error": str(e)})
                            yield f"event: stats\ndata: {error_data}\n\n"

                # 10s between cycles, checking disconnect every 0.2s
                for _ in range(50):
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(0.2)

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


async def _probe_box(deps: APIDependencies, box, application_config: AppConfig) -> APIBoxStats:
    """Get stats for a single box. Handles local vs remote."""
    if box.transport == "local":
        return await _get_local_stats(box.name)

    try:
        conn = await deps.connect_for_box(box, application_config)
        try:
            return await _get_remote_stats(box.name, conn)
        finally:
            with contextlib.suppress(Exception):
                conn.close()
    except Exception as e:
        return APIBoxStats(name=box.name, error=str(e))
