"""Archive creation and extraction API."""

from __future__ import annotations

import asyncio
import logging
import shlex
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path, _normalize_directory_path
from .rate_limiting import rate_limit_file_ops

logger = logging.getLogger(__name__)

MAX_ARCHIVE_PATHS = 100
ARCHIVE_TIMEOUT = 120
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
        raise HTTPException(status_code=400, detail=str(exc))

    expected_exts = {"tar.gz": (".tar.gz",), "tgz": (".tgz",), "zip": (".zip",)}
    valid_exts = expected_exts.get(fmt, ())
    if not any(validated.lower().endswith(ext) for ext in valid_exts):
        raise HTTPException(status_code=400, detail=f"Archive name must end in {' or '.join(valid_exts)}")

    return validated


def _check_zip_traversal(zf: zipfile.ZipFile) -> None:
    """Check for path traversal in zip entries."""
    for info in zf.infolist():
        # Reject absolute paths and parent references
        if info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise HTTPException(
                status_code=400,
                detail=f"Archive contains unsafe path: {info.filename}",
            )


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
            raise HTTPException(status_code=400, detail=f"Invalid format. Must be one of: {', '.join(VALID_FORMATS)}")

        archive_name = _validate_archive_name(payload.archive_name, payload.format)
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            dest_dir = Path(_normalize_local_path(payload.destination))
            archive_path = dest_dir / archive_name

            def _create_local():
                if payload.format in ("tar.gz", "tgz"):
                    with tarfile.open(str(archive_path), "w:gz") as tf:
                        for p in payload.paths:
                            normalized = _normalize_local_path(p)
                            source = Path(normalized)
                            tf.add(str(source), arcname=source.name)
                else:
                    with zipfile.ZipFile(str(archive_path), "w", zipfile.ZIP_DEFLATED) as zf:
                        for p in payload.paths:
                            normalized = _normalize_local_path(p)
                            source = Path(normalized)
                            if source.is_dir():
                                for child in source.rglob("*"):
                                    if child.is_file():
                                        arcname = str(source.name / child.relative_to(source))
                                        zf.write(str(child), arcname)
                            else:
                                zf.write(str(source), source.name)

            try:
                await asyncio.to_thread(_create_local)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

            return {"status": "ok", "message": f"Created {archive_name}", "path": str(archive_path)}

        # Remote
        try:
            dest_validated = PathValidator.validate_remote_path(payload.destination)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        validated_paths: list[str] = []
        for p in payload.paths:
            try:
                validated_paths.append(PathValidator.validate_remote_path(p))
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid path {p}: {exc}")

        archive_target = str(PurePosixPath(dest_validated) / archive_name)
        quoted_paths = " ".join(shlex.quote(p) for p in validated_paths)

        if payload.format in ("tar.gz", "tgz"):
            cmd = f"tar czf {shlex.quote(archive_target)} -C / {quoted_paths}"
        else:
            cmd = f"cd / && zip -r {shlex.quote(archive_target)} {quoted_paths}"

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                try:
                    result = await asyncio.wait_for(
                        connection.run(cmd, check=True),
                        timeout=ARCHIVE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raise HTTPException(status_code=504, detail="Archive creation timed out")
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {"status": "ok", "message": f"Created {archive_name}", "path": archive_target}

    @router.post("/boxes/{name}/archive/extract")
    async def api_archive_extract(
        request: Request,
        name: str,
        payload: APIArchiveExtractRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ):
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            archive = Path(_normalize_local_path(payload.archive_path))
            dest_dir = Path(_normalize_local_path(payload.destination))

            if not archive.exists():
                raise HTTPException(status_code=404, detail="Archive not found")

            def _extract_local():
                name_lower = archive.name.lower()
                if name_lower.endswith((".tar.gz", ".tgz")):
                    with tarfile.open(str(archive), "r:gz") as tf:
                        tf.extractall(path=str(dest_dir), filter="data")
                elif name_lower.endswith(".zip"):
                    with zipfile.ZipFile(str(archive), "r") as zf:
                        _check_zip_traversal(zf)
                        zf.extractall(path=str(dest_dir))
                else:
                    raise ValueError("Unsupported archive format")

            try:
                await asyncio.to_thread(_extract_local)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

            return {"status": "ok", "message": "Extracted", "path": str(dest_dir)}

        # Remote
        try:
            validated_archive = PathValidator.validate_remote_path(payload.archive_path)
            validated_dest = PathValidator.validate_remote_path(payload.destination)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        name_lower = PurePosixPath(validated_archive).name.lower()
        if name_lower.endswith((".tar.gz", ".tgz")):
            cmd = f"tar xzf {shlex.quote(validated_archive)} -C {shlex.quote(validated_dest)}"
        elif name_lower.endswith(".zip"):
            cmd = f"unzip -o {shlex.quote(validated_archive)} -d {shlex.quote(validated_dest)}"
        else:
            raise HTTPException(status_code=400, detail="Unsupported archive format")

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                try:
                    await asyncio.wait_for(
                        connection.run(cmd, check=True),
                        timeout=ARCHIVE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raise HTTPException(status_code=504, detail="Extraction timed out")
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {"status": "ok", "message": "Extracted", "path": validated_dest}

    return router
