"""Batch file operations API (delete, move, copy)."""

from __future__ import annotations

import contextlib
import logging
import shlex
import shutil
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path, _normalize_directory_path
from .rate_limiting import rate_limit_delete, rate_limit_file_ops

logger = logging.getLogger(__name__)

MAX_BATCH_PATHS = 100


class APIBatchDeleteRequest(BaseModel):
    paths: list[str]


class APIBatchMoveRequest(BaseModel):
    paths: list[str]
    destination: str


class APIBatchCopyRequest(BaseModel):
    paths: list[str]
    destination: str


class APIBatchResult(BaseModel):
    status: str  # "ok" | "partial"
    succeeded: list[str]
    failed: list[dict]  # [{"path": "...", "error": "..."}]


def _validate_batch_size(paths: list[str]) -> None:
    if len(paths) > MAX_BATCH_PATHS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many paths (max {MAX_BATCH_PATHS})",
        )
    if not paths:
        raise HTTPException(status_code=400, detail="No paths provided")


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/boxes/{name}/batch/delete", response_model=APIBatchResult)
    async def api_batch_delete(
        request: Request,
        name: str,
        payload: APIBatchDeleteRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_delete),
    ) -> APIBatchResult:
        _validate_batch_size(payload.paths)
        box = deps.get_box_or_404(application_config, name)

        succeeded: list[str] = []
        failed: list[dict] = []

        if box.transport == "local":
            for path in payload.paths:
                try:
                    normalized = _normalize_local_path(path)
                    target = Path(normalized)
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    succeeded.append(path)
                except Exception as exc:
                    failed.append({"path": path, "error": str(exc)})
        else:
            # Validate all paths upfront
            validated: list[tuple[str, str]] = []
            for path in payload.paths:
                try:
                    v = PathValidator.validate_remote_path(path)
                    validated.append((path, v))
                except ValidationError as exc:
                    failed.append({"path": path, "error": str(exc)})

            if validated:
                ssh_pool = get_pool()
                try:
                    async with ssh_pool.connection(
                        box, lambda: deps.connect_for_box(box, application_config)
                    ) as connection:
                        sftp_client = await connection.start_sftp_client()
                        try:
                            for orig, vpath in validated:
                                try:
                                    try:
                                        attrs = await sftp_client.stat(vpath)
                                        import stat as stat_mod
                                        if stat_mod.S_ISDIR(attrs.permissions or 0):
                                            await connection.run(
                                                f"rm -rf {shlex.quote(vpath)}",
                                                check=True,
                                            )
                                        else:
                                            await sftp_client.remove(vpath)
                                    except (FileNotFoundError, OSError):
                                        await sftp_client.remove(vpath)
                                    succeeded.append(orig)
                                except Exception as exc:
                                    failed.append({"path": orig, "error": str(exc)})
                        finally:
                            with contextlib.suppress(Exception):
                                await sftp_client.exit()  # type: ignore[func-returns-value]
                except SSHError as exc:
                    # If connection failed entirely, mark remaining as failed
                    already = {f["path"] for f in failed} | set(succeeded)
                    for orig, _ in validated:
                        if orig not in already:
                            failed.append({"path": orig, "error": str(exc)})

        status = "ok" if not failed else "partial"
        return APIBatchResult(status=status, succeeded=succeeded, failed=failed)

    @router.post("/boxes/{name}/batch/move", response_model=APIBatchResult)
    async def api_batch_move(
        request: Request,
        name: str,
        payload: APIBatchMoveRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ) -> APIBatchResult:
        _validate_batch_size(payload.paths)
        box = deps.get_box_or_404(application_config, name)

        succeeded: list[str] = []
        failed: list[dict] = []

        if box.transport == "local":
            dest_dir = Path(_normalize_local_path(payload.destination))
            dest_dir.mkdir(parents=True, exist_ok=True)
            for path in payload.paths:
                try:
                    normalized = _normalize_local_path(path)
                    source = Path(normalized)
                    target = dest_dir / source.name
                    shutil.move(str(source), str(target))
                    succeeded.append(path)
                except Exception as exc:
                    failed.append({"path": path, "error": str(exc)})
        else:
            try:
                dest_validated = PathValidator.validate_remote_path(payload.destination)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}")

            validated: list[tuple[str, str]] = []
            for path in payload.paths:
                try:
                    v = PathValidator.validate_remote_path(path)
                    validated.append((path, v))
                except ValidationError as exc:
                    failed.append({"path": path, "error": str(exc)})

            if validated:
                ssh_pool = get_pool()
                try:
                    async with ssh_pool.connection(
                        box, lambda: deps.connect_for_box(box, application_config)
                    ) as connection:
                        sftp_client = await connection.start_sftp_client()
                        try:
                            for orig, vpath in validated:
                                try:
                                    target = str(PurePosixPath(dest_validated) / PurePosixPath(vpath).name)
                                    await sftp_client.rename(vpath, target)
                                    succeeded.append(orig)
                                except Exception as exc:
                                    failed.append({"path": orig, "error": str(exc)})
                        finally:
                            with contextlib.suppress(Exception):
                                await sftp_client.exit()  # type: ignore[func-returns-value]
                except SSHError as exc:
                    already = {f["path"] for f in failed} | set(succeeded)
                    for orig, _ in validated:
                        if orig not in already:
                            failed.append({"path": orig, "error": str(exc)})

        status = "ok" if not failed else "partial"
        return APIBatchResult(status=status, succeeded=succeeded, failed=failed)

    @router.post("/boxes/{name}/batch/copy", response_model=APIBatchResult)
    async def api_batch_copy(
        request: Request,
        name: str,
        payload: APIBatchCopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ) -> APIBatchResult:
        _validate_batch_size(payload.paths)
        box = deps.get_box_or_404(application_config, name)

        succeeded: list[str] = []
        failed: list[dict] = []

        if box.transport == "local":
            dest_dir = Path(_normalize_local_path(payload.destination))
            dest_dir.mkdir(parents=True, exist_ok=True)
            for path in payload.paths:
                try:
                    normalized = _normalize_local_path(path)
                    source = Path(normalized)
                    target = dest_dir / source.name
                    if source.is_dir():
                        shutil.copytree(str(source), str(target))
                    else:
                        shutil.copy2(str(source), str(target))
                    succeeded.append(path)
                except Exception as exc:
                    failed.append({"path": path, "error": str(exc)})
        else:
            try:
                dest_validated = PathValidator.validate_remote_path(payload.destination)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}")

            validated: list[tuple[str, str]] = []
            for path in payload.paths:
                try:
                    v = PathValidator.validate_remote_path(path)
                    validated.append((path, v))
                except ValidationError as exc:
                    failed.append({"path": path, "error": str(exc)})

            if validated:
                ssh_pool = get_pool()
                try:
                    async with ssh_pool.connection(
                        box, lambda: deps.connect_for_box(box, application_config)
                    ) as connection:
                        for orig, vpath in validated:
                            try:
                                target = str(PurePosixPath(dest_validated) / PurePosixPath(vpath).name)
                                await connection.run(
                                    f"cp -r {shlex.quote(vpath)} {shlex.quote(target)}",
                                    check=True,
                                )
                                succeeded.append(orig)
                            except Exception as exc:
                                failed.append({"path": orig, "error": str(exc)})
                except SSHError as exc:
                    already = {f["path"] for f in failed} | set(succeeded)
                    for orig, _ in validated:
                        if orig not in already:
                            failed.append({"path": orig, "error": str(exc)})

        status = "ok" if not failed else "partial"
        return APIBatchResult(status=status, succeeded=succeeded, failed=failed)

    return router
