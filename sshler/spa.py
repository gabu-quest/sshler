from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles


SPA_DIST_DIR = Path(__file__).parent / "static" / "dist"


def mount_spa(app: FastAPI, serve_spa: bool) -> None:
    """Mount built SPA assets and index routes when enabled."""

    spa_dist = SPA_DIST_DIR
    if not serve_spa or not spa_dist.exists():
        return

    app.mount("/app/assets", StaticFiles(directory=str(spa_dist / "assets")), name="spa-assets")

    @app.get("/app", response_class=HTMLResponse)
    @app.get("/app/", response_class=HTMLResponse)
    @app.get("/app/{path:path}", response_class=HTMLResponse)
    async def vue_index(path: str = ""):
        target_path = (spa_dist / path).resolve()
        try:
            spa_dist.resolve()
        except Exception:
            raise HTTPException(status_code=404)

        if path and target_path.is_file() and str(target_path).startswith(str(spa_dist)):
            return FileResponse(target_path)

        index_path = spa_dist / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404)
        response = FileResponse(index_path)
        # Never cache index.html — hashed assets handle their own caching.
        # Without this, browsers may serve stale HTML referencing old asset hashes.
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
