from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile

from ..config import AppConfig
from .box_files import Answers, box_files
from .dependencies import APIDependencies
from .failure import REASON_INVALID, Failure
from .file_contract import MISSING_FILE, MISSING_FOLDER, MISSING_SOURCE
from .helpers import (
    IMAGE_CONTENT_TYPES,
    MAX_IMAGE_PREVIEW_BYTES,
    _attachment_header,
    _is_markdown_file,
    _syntax_from_filename,
)
from .local_files import FileAnswer
from .models import (
    APIChmodRequest,
    APICopyRequest,
    APIDeleteRequest,
    APIDirectoryListing,
    APIFilePreview,
    APIMoveRequest,
    APIRenameRequest,
    APISimpleMessage,
    APITouchRequest,
)
from .placement import refuse_replace_and_keep_both
from .rate_limiting import rate_limit_delete, rate_limit_upload, rate_limit_write

logger = logging.getLogger(__name__)


class _TempFileResponse(FileAnswer):
    """A ``FileAnswer`` that deletes its file once the response is over.

    A ``background`` task is not enough: Starlette answers a malformed or
    unsatisfiable ``Range`` with an early ``return`` that skips it, and a client
    disconnect raises out of ``send`` before it runs. ``finally`` covers every outcome.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


# The texts a route gives a thing not found and work past its limit (`Answers`); the
# reason and the status of every failure are `failure_of`'s.
_FILE = Answers(missing=MISSING_FILE)
_SOURCE = Answers(missing=MISSING_SOURCE)
_FOLDER = Answers(missing=MISSING_FOLDER)
_DIR_SIZE = Answers(
    missing=MISSING_FOLDER,
    timed_out="Directory size timed out",
    log_other="Failed to get dir size",
)
_FOLDER_ZIP = Answers(missing=MISSING_FOLDER, timed_out="Download timed out")


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes/{name}/ls", response_model=APIDirectoryListing)
    async def api_list_directory(
        name: str,
        directory: str = Query("/"),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIDirectoryListing:
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FOLDER) as files:
            folder = await files.folder(directory)
            entries = await files.list_folder(folder)
        return APIDirectoryListing(box=box.name, directory=folder, entries=entries)

    @router.post("/boxes/{name}/rename", response_model=APISimpleMessage)
    async def api_rename(
        name: str,
        payload: APIRenameRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        refuse_replace_and_keep_both(payload.overwrite, payload.keep_both)
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.rename(
                payload.path,
                payload.new_name,
                overwrite=payload.overwrite,
                keep_both=payload.keep_both,
            )
        return APISimpleMessage(status="ok", message="renamed", path=target)

    @router.post("/boxes/{name}/touch", response_model=APISimpleMessage)
    async def api_touch_file(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config) as files:
            target = await files.child(payload.directory, payload.filename)
            await files.touch(target)
        return APISimpleMessage(status="ok", message="created", path=target)

    @router.post("/boxes/{name}/mkdir", response_model=APISimpleMessage)
    async def api_mkdir(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config) as files:
            target = await files.child(payload.directory, payload.filename)
            await files.make_folder(target)
        return APISimpleMessage(status="ok", message="created", path=target)

    @router.post("/boxes/{name}/delete", response_model=APISimpleMessage)
    async def api_delete_file(
        request: Request,
        name: str,
        payload: APIDeleteRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_delete),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(payload.path)
            await files.delete(target)
        return APISimpleMessage(status="ok", message="deleted", path=target)

    @router.post("/boxes/{name}/move", response_model=APISimpleMessage)
    async def api_move(
        name: str,
        payload: APIMoveRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        refuse_replace_and_keep_both(payload.overwrite, payload.keep_both)
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _SOURCE) as files:
            target = await files.place_into(
                payload.source,
                payload.destination,
                None,
                move=True,
                overwrite=payload.overwrite,
                allow_dirs=True,
                keep_both=payload.keep_both,
            )
        return APISimpleMessage(status="ok", message="moved", path=target)

    @router.post("/boxes/{name}/copy", response_model=APISimpleMessage)
    async def api_copy(
        name: str,
        payload: APICopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        refuse_replace_and_keep_both(payload.overwrite, payload.keep_both)
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _SOURCE) as files:
            target = await files.place_into(
                payload.source,
                payload.destination,
                payload.new_name,
                move=False,
                overwrite=payload.overwrite,
                allow_dirs=False,
                keep_both=payload.keep_both,
            )
        return APISimpleMessage(status="ok", message="copied", path=target)

    @router.get("/boxes/{name}/file", response_model=APIFilePreview)
    async def api_file_preview(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIFilePreview:
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(path)
            image_mime = IMAGE_CONTENT_TYPES.get(Path(target).suffix.lower())
            image_data: str | None = None
            image_too_large = False
            if image_mime:
                image_bytes, too_large = await files.read_bytes(target, MAX_IMAGE_PREVIEW_BYTES)
                if too_large:
                    image_too_large = True
                else:
                    image_data = base64.b64encode(image_bytes).decode("ascii")

            text_content: str | None = None
            if not image_mime or image_too_large:
                text_content = await files.read_text(target, deps.settings.max_upload_bytes)

            return APIFilePreview(
                box=box.name,
                path=target,
                parent=files.parent(target),
                content=text_content or None,
                syntax_class=_syntax_from_filename(target),
                image_data=image_data,
                image_mime=image_mime,
                image_too_large=image_too_large,
                is_markdown=_is_markdown_file(target),
            )

    @router.post("/boxes/{name}/write", response_model=APISimpleMessage)
    async def api_write_file(
        request: Request,
        name: str,
        path: str = Body(...),
        content: str = Body(...),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_write),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        encoded_len = len(content.encode("utf-8"))
        if encoded_len > deps.settings.max_upload_bytes:
            raise Failure(
                REASON_INVALID,
                f"File exceeds {deps.settings.max_upload_bytes // 1024}KB editing limit",
            )

        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(path)
            await files.write_text(target, content)
        return APISimpleMessage(status="ok", message="saved", path=target)

    @router.get("/boxes/{name}/download")
    async def api_download_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(path)
            return await files.serve(target, inline=False, limit=deps.settings.max_upload_bytes)

    @router.get("/boxes/{name}/view")
    async def api_view_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Serve a file inline with its natural MIME type (for browser rendering)."""
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(path)
            return await files.serve(target, inline=True, limit=deps.settings.max_upload_bytes)

    @router.get("/boxes/{name}/dir-size")
    async def api_directory_size(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Get the total size of a directory in bytes."""
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _DIR_SIZE) as files:
            size = await files.folder_size(await files.path(path))
        return {"size_bytes": size}

    @router.get("/boxes/{name}/download-dir")
    async def api_download_directory(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Download a directory as a zip file."""
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config, _FOLDER_ZIP) as files:
            zipped = await files.zip_folder(path)
        return _TempFileResponse(
            zipped.path,
            media_type="application/zip",
            headers={"Content-Disposition": _attachment_header(f"{zipped.name}.zip")},
        )

    @router.get("/boxes/{name}/stat")
    async def api_stat_path(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Check if a path exists and whether it's a file or directory."""
        box = deps.get_box_or_404(application_config, name)
        async with box_files(deps, box, application_config) as files:
            kind = await files.stat(await files.path(path))
        return kind._asdict()

    @router.post("/boxes/{name}/upload", response_model=APISimpleMessage)
    async def api_upload(
        request: Request,
        name: str,
        directory: str = Form(...),
        file: UploadFile = File(...),
        overwrite: bool = Form(False),
        keep_both: bool = Form(False),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_upload),
    ) -> APISimpleMessage:
        refuse_replace_and_keep_both(overwrite, keep_both)
        box = deps.get_box_or_404(application_config, name)
        candidate_name = (file.filename or "").strip()
        if not candidate_name:
            raise Failure(REASON_INVALID, "Missing filename")

        try:
            contents = await file.read()
        finally:
            await file.close()

        if len(contents) > deps.settings.max_upload_bytes:
            limit_kb = deps.settings.max_upload_bytes // 1024
            raise Failure(REASON_INVALID, f"Upload exceeds {limit_kb} KB limit")

        async with box_files(deps, box, application_config) as files:
            target = await files.child(directory, candidate_name)
            target = await files.upload(target, contents, overwrite=overwrite, keep_both=keep_both)
        return APISimpleMessage(status="ok", message="uploaded", path=target)

    @router.post("/boxes/{name}/chmod", response_model=APISimpleMessage)
    async def api_chmod(
        name: str,
        payload: APIChmodRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        try:
            mode = int(payload.mode, 8)
        except ValueError as exc:
            raise Failure(REASON_INVALID, "Invalid octal mode") from exc
        if mode < 0 or mode > 0o7777:
            raise Failure(REASON_INVALID, "Mode out of range")

        async with box_files(deps, box, application_config, _FILE) as files:
            target = await files.path(payload.path)
            await files.chmod(target, mode)
        return APISimpleMessage(status="ok", message="chmod applied", path=target)

    return router
