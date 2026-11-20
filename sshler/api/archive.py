"""Archive creation and extraction API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import AppConfig
from ..validation import PathValidator, ValidationError
from .box_files import box_files
from .dependencies import APIDependencies
from .rate_limiting import rate_limit_file_ops

logger = logging.getLogger(__name__)

MAX_ARCHIVE_PATHS = 100
VALID_FORMATS = {"tar.gz", "tgz", "zip"}


class APIArchiveCreateRequest(BaseModel):
    paths: list[str]
    destination: str
    archive_name: str
    format: str  # "tar.gz" | "tgz" | "zip"


class APIArchiveExtractRequest(BaseModel):
    archive_path: str
    destination: str


def _validate_archive_name(name: str, fmt: str) -> str:
    """Validate archive filename."""
    try:
        validated = PathValidator.validate_filename(name)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    expected_exts = {"tar.gz": (".tar.gz",), "tgz": (".tgz",), "zip": (".zip",)}
    valid_exts = expected_exts.get(fmt, ())
    if not any(validated.lower().endswith(ext) for ext in valid_exts):
        raise HTTPException(
            status_code=400, detail=f"Archive name must end in {' or '.join(valid_exts)}"
        )

    return validated


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/boxes/{name}/archive/create")
    async def api_archive_create(
        request: Request,
        name: str,
        payload: APIArchiveCreateRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ):
        if len(payload.paths) > MAX_ARCHIVE_PATHS:
            raise HTTPException(status_code=400, detail=f"Too many paths (max {MAX_ARCHIVE_PATHS})")
        if not payload.paths:
            raise HTTPException(status_code=400, detail="No paths provided")
        if payload.format not in VALID_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid format. Must be one of: {', '.join(VALID_FORMATS)}",
            )

        archive_name = _validate_archive_name(payload.archive_name, payload.format)
        box = deps.get_box_or_404(application_config, name)

        async with box_files(deps, box, application_config) as files:
            archive_path = await files.create_archive(
                payload.paths, payload.destination, archive_name, payload.format
            )
        return {"status": "ok", "message": f"Created {archive_name}", "path": archive_path}

    @router.post("/boxes/{name}/archive/extract")
    async def api_archive_extract(
        request: Request,
        name: str,
        payload: APIArchiveExtractRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ):
        box = deps.get_box_or_404(application_config, name)

        async with box_files(deps, box, application_config) as files:
            destination = await files.extract_archive(payload.archive_path, payload.destination)
        return {"status": "ok", "message": "Extracted", "path": destination}

    return router
