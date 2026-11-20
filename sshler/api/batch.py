"""Batch file operations API (delete, move, copy)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool

from ..config import AppConfig
from .box_files import box_files
from .dependencies import APIDependencies
from .file_contract import BatchOutcome
from .rate_limiting import rate_limit_delete, rate_limit_file_ops

logger = logging.getLogger(__name__)

MAX_BATCH_PATHS = 100


class APIBatchDeleteRequest(BaseModel):
    paths: list[str]


class APIBatchMoveRequest(BaseModel):
    paths: list[str]
    destination: str
    # Applies to every item: replace a regular file that has the item's name.
    overwrite: StrictBool = False


class APIBatchCopyRequest(BaseModel):
    paths: list[str]
    destination: str
    overwrite: StrictBool = False


class APIBatchResult(BaseModel):
    status: str  # "ok" | "partial"
    succeeded: list[str]
    # [{"path", "error", "reason"}]; move and copy give `reason` as one of the
    # REASON_* values of `placement.py` ("exists", "cannot_replace", "failed").
    failed: list[dict]

    @classmethod
    def of(cls, outcome: BatchOutcome) -> APIBatchResult:
        return cls(
            status="ok" if not outcome.failed else "partial",
            succeeded=outcome.succeeded,
            failed=outcome.failed,
        )


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

    # Each batch collects its own failures, one entry per path: `box_files` answers
    # none of them (answers=None).

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
        async with box_files(deps, box, application_config, answers=None) as files:
            outcome = await files.delete_many(payload.paths)
        return APIBatchResult.of(outcome)

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
        async with box_files(deps, box, application_config, answers=None) as files:
            outcome = await files.place_many(
                payload.paths, payload.destination, move=True, overwrite=payload.overwrite
            )
        return APIBatchResult.of(outcome)

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
        async with box_files(deps, box, application_config, answers=None) as files:
            outcome = await files.place_many(
                payload.paths, payload.destination, move=False, overwrite=payload.overwrite
            )
        return APIBatchResult.of(outcome)

    return router
