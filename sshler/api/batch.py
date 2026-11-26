"""Batch file operations API (delete, move, copy)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SerializerFunctionWrapHandler, StrictBool, model_serializer

from ..config import AppConfig
from .box_files import box_files
from .dependencies import APIDependencies
from .failure import REASON_INVALID, Failure
from .file_contract import BatchOutcome
from .placement import refuse_replace_and_keep_both
from .rate_limiting import rate_limit_delete, rate_limit_file_ops

logger = logging.getLogger(__name__)

MAX_BATCH_PATHS = 100


class APIBatchDeleteRequest(BaseModel):
    paths: list[str]


class APIBatchMoveRequest(BaseModel):
    paths: list[str]
    destination: str
    # Applies to every item: replace a regular file that has the item's name, or land
    # beside a taken name under the first free name of its series; never both.
    overwrite: StrictBool = False
    keep_both: StrictBool = False


class APIBatchCopyRequest(BaseModel):
    paths: list[str]
    destination: str
    overwrite: StrictBool = False
    keep_both: StrictBool = False


class APIBatchResult(BaseModel):
    status: str  # "ok" | "partial"
    succeeded: list[str]
    # [{"path", "error", "reason"}]: `failure_entry` (`failure.py`) gives each item the
    # reason and text a single request on that path would answer.
    failed: list[dict]
    # With `keep_both` only: [{"path": <as sent>, "placed": <landed path>}], one entry per
    # item that landed under another name than asked; left out of every other answer.
    placed: list[dict] | None = None

    # No return annotation: pydantic would publish it as the answer's schema, a bare
    # object, in place of the model's own fields.
    @model_serializer(mode="wrap")
    def _without_placed_unless_kept_both(self, handler: SerializerFunctionWrapHandler):
        body: dict = handler(self)
        if self.placed is None:
            body.pop("placed", None)
        return body

    @classmethod
    def of(cls, outcome: BatchOutcome, *, keep_both: bool = False) -> APIBatchResult:
        return cls(
            status="ok" if not outcome.failed else "partial",
            succeeded=outcome.succeeded,
            failed=outcome.failed,
            placed=outcome.placed if keep_both else None,
        )


def _validate_batch_size(paths: list[str]) -> None:
    if len(paths) > MAX_BATCH_PATHS:
        raise Failure(REASON_INVALID, f"Too many paths (max {MAX_BATCH_PATHS})")
    if not paths:
        raise Failure(REASON_INVALID, "No paths provided")


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    # Each batch collects its own failures, one entry per path; `box_files` answers a
    # failure of the whole batch (a destination refused, a box not reached first).

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
        async with box_files(deps, box, application_config) as files:
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
        return await _place_many(name, payload, application_config, move=True)

    @router.post("/boxes/{name}/batch/copy", response_model=APIBatchResult)
    async def api_batch_copy(
        request: Request,
        name: str,
        payload: APIBatchCopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_file_ops),
    ) -> APIBatchResult:
        return await _place_many(name, payload, application_config, move=False)

    async def _place_many(
        name: str,
        payload: APIBatchMoveRequest | APIBatchCopyRequest,
        application_config: AppConfig,
        *,
        move: bool,
    ) -> APIBatchResult:
        _validate_batch_size(payload.paths)
        refuse_replace_and_keep_both(payload.overwrite, payload.keep_both)
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config) as files:
            outcome = await files.place_many(
                payload.paths,
                payload.destination,
                move=move,
                overwrite=payload.overwrite,
                keep_both=payload.keep_both,
            )
        return APIBatchResult.of(outcome, keep_both=payload.keep_both)

    return router
