from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api.failure import REASON_NOT_FOUND, Failure

SPA_DIST_DIR = Path(__file__).parent / "static" / "dist"


def mount_spa(app: FastAPI, serve_spa: bool) -> None:
    """Mount built SPA assets and index routes when enabled."""

    spa_dist = SPA_DIST_DIR
    if not serve_spa or not spa_dist.exists():
        return

    spa_root = spa_dist.resolve()
    app.mount("/app/assets", StaticFiles(directory=str(spa_dist / "assets")), name="spa-assets")

    @app.get("/app", response_class=HTMLResponse)
    @app.get("/app/", response_class=HTMLResponse)
    @app.get("/app/{path:path}", response_class=HTMLResponse)
    async def vue_index(path: str = ""):
        if path:
            try:
                target = (spa_root / path).resolve()
            except (OSError, ValueError):
                # A path that cannot be a file name is served as any unknown path is.
                target = None
            if target is not None and target.is_relative_to(spa_root) and target.is_file():
                return FileResponse(target)

        index_path = spa_dist / "index.html"
        if not index_path.exists():
            raise Failure(REASON_NOT_FOUND, "Not found")
        response = FileResponse(index_path)
        # Never cache index.html — hashed assets handle their own caching.
        # Without this, browsers may serve stale HTML referencing old asset hashes.
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
