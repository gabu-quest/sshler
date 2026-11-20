"""Batch file operations API (delete, move, copy)."""

from __future__ import annotations

import asyncio
import logging
import os
import stat as stat_mod
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool

from ..config import AppConfig, Box
from ..ssh import SSHError, sftp_session
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import (
    _local_batch_delete,
    _normalize_local_path,
)
from .local_files import (
    _local_destination,
    _local_make_folder,
    _local_place,
    _local_refuse_a_folder_into_itself,
)
from .placement import failure_entry, refuse_a_name_this_request_placed
from .rate_limiting import rate_limit_delete, rate_limit_file_ops
from .remote import command, path_word, remote_path
from .remote_files import _remote_place

logger = logging.getLogger(__name__)

MAX_BATCH_PATHS = 100


def remove_tree_command(path: str) -> str:
    """The remote command that deletes the folder ``path`` and everything in it."""
    return command("rm", "-rf", path_word(path))


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


def _validate_batch_size(paths: list[str]) -> None:
    if len(paths) > MAX_BATCH_PATHS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many paths (max {MAX_BATCH_PATHS})",
        )
    if not paths:
        raise HTTPException(status_code=400, detail="No paths provided")


async def _resolve_tilde_paths(
    connection, validated: list[tuple[str, str]], failed: list[dict]
) -> list[tuple[str, str]]:
    """Resolve ``~`` in each validated path against the remote home. SFTP and the
    quoted shell commands do not expand it. A path that no longer validates is
    reported in ``failed``; a failed home lookup (HTTP 502) aborts the batch."""
    resolved: list[tuple[str, str]] = []
    for orig, vpath in validated:
        try:
            resolved.append((orig, await remote_path(connection, vpath)))
        except ValidationError as exc:
            failed.append(failure_entry(orig, exc))
    return resolved


async def _resolve_tilde_destination(connection, destination: str) -> str:
    try:
        return await remote_path(connection, destination)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}") from exc


def _local_batch_item(
    path: str, dest_dir: Path, landed: set[str], *, move: bool, overwrite: bool
) -> str:
    """Move or copy one local item into ``dest_dir`` (call from a worker thread)."""
    source = _normalize_local_path(path)
    if not os.path.lexists(source):
        raise FileNotFoundError(f"No such file or directory: {path}")
    _local_refuse_a_folder_into_itself(source, dest_dir, move=move)
    target = str(_local_make_folder(dest_dir) / Path(source).name)
    refuse_a_name_this_request_placed(target, landed)
    _local_place(source, target, move=move, overwrite=overwrite)
    return target


async def _batch_place(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    paths: list[str],
    destination: str,
    *,
    move: bool,
    overwrite: bool,
) -> APIBatchResult:
    """Batch move or copy: every item whose name is free is done; each refused item is
    in ``failed`` with its reason."""
    succeeded: list[str] = []
    failed: list[dict] = []
    landed: set[str] = set()

    if box.transport == "local":
        try:
            dest_dir = await asyncio.to_thread(_local_destination, destination)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        for path in paths:
            try:
                target = await asyncio.to_thread(
                    _local_batch_item, path, dest_dir, landed, move=move, overwrite=overwrite
                )
            except Exception as exc:
                failed.append(failure_entry(path, exc))
            else:
                landed.add(target)
                succeeded.append(path)
        return APIBatchResult(
            status="ok" if not failed else "partial", succeeded=succeeded, failed=failed
        )

    try:
        dest_validated = PathValidator.validate_remote_path(destination)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid destination: {exc}") from exc

    validated: list[tuple[str, str]] = []
    for path in paths:
        try:
            validated.append((path, PathValidator.validate_remote_path(path)))
        except ValidationError as exc:
            failed.append(failure_entry(path, exc))

    if validated:
        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                dest_resolved = await _resolve_tilde_destination(connection, dest_validated)
                validated = await _resolve_tilde_paths(connection, validated, failed)
                async with sftp_session(connection) as sftp_client:
                    for orig, vpath in validated:
                        target = str(PurePosixPath(dest_resolved) / PurePosixPath(vpath).name)
                        try:
                            refuse_a_name_this_request_placed(target, landed)
                            await _remote_place(
                                sftp_client, vpath, target, move=move, overwrite=overwrite
                            )
                        except Exception as exc:
                            failed.append(failure_entry(orig, exc))
                        else:
                            landed.add(target)
                            succeeded.append(orig)
        except SSHError as exc:
            already = {f["path"] for f in failed} | set(succeeded)
            for orig, _ in validated:
                if orig not in already:
                    failed.append(failure_entry(orig, exc))

    return APIBatchResult(
        status="ok" if not failed else "partial", succeeded=succeeded, failed=failed
    )


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
                    normalized = await asyncio.to_thread(_normalize_local_path, path)
                    await _local_batch_delete(normalized)
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
                        validated = await _resolve_tilde_paths(connection, validated, failed)
                        async with sftp_session(connection) as sftp_client:
                            for orig, vpath in validated:
                                try:
                                    try:
                                        attrs = await sftp_client.stat(vpath)
                                        if stat_mod.S_ISDIR(attrs.permissions or 0):
                                            await connection.run(
                                                remove_tree_command(vpath), check=True
                                            )
                                        else:
                                            await sftp_client.remove(vpath)
                                    except (FileNotFoundError, OSError):
                                        await sftp_client.remove(vpath)
                                    succeeded.append(orig)
                                except Exception as exc:
                                    failed.append({"path": orig, "error": str(exc)})
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
        return await _batch_place(
            deps,
            box,
            application_config,
            payload.paths,
            payload.destination,
            move=True,
            overwrite=payload.overwrite,
        )

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
        return await _batch_place(
            deps,
            box,
            application_config,
            payload.paths,
            payload.destination,
            move=False,
            overwrite=payload.overwrite,
        )

    return router
