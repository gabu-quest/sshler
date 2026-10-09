"""Projects and registrations for the local HTML artifact catalog."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import state
from ..artifacts import (
    ArtifactValidationError,
    artifact_alias_path,
    clean_alias_slug,
    clean_group_path,
    clean_mount_path,
    clean_project_name,
    clean_title,
    clear_page_cache,
    discover_pages,
    infer_project_name,
    registration_exists,
    resolve_registration,
)
from .dependencies import APIDependencies


class APIArtifactProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class APIArtifactProjectUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class APIArtifactProject(BaseModel):
    id: str
    name: str
    slug: str
    registration_count: int
    created_at: float
    updated_at: float


class APIArtifactProjectList(BaseModel):
    projects: list[APIArtifactProject]


class APIArtifactCreate(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=4096)
    project_id: str | None = Field(default=None, max_length=64)
    project: str | None = Field(default=None, max_length=100)
    group_path: str = Field(default="", max_length=500)
    mode: str = Field(default="auto")
    entrypoint: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    slug: str | None = Field(default=None, max_length=80)
    mount_path: str | None = Field(default=None, max_length=500)


class APIArtifactUpdate(BaseModel):
    source_path: str | None = Field(default=None, min_length=1, max_length=4096)
    project_id: str | None = Field(default=None, max_length=64)
    project: str | None = Field(default=None, max_length=100)
    group_path: str | None = Field(default=None, max_length=500)
    mode: str | None = None
    entrypoint: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    slug: str | None = Field(default=None, max_length=80)
    mount_path: str | None = Field(default=None, max_length=500)


class APIArtifactPage(BaseModel):
    relative_path: str
    title: str
    group_path: str
    serve_path: str


class APIArtifact(BaseModel):
    id: str
    project_id: str
    project_name: str
    project_slug: str
    group_path: str
    source_path: str
    mode: str
    entrypoint: str | None
    title: str | None
    slug: str
    alias_path: str
    mount_path: str | None
    exists: bool
    created_at: float
    updated_at: float


class APIArtifactCreateResult(APIArtifact):
    created: bool


class APIArtifactList(BaseModel):
    artifacts: list[APIArtifact]


class APIArtifactPageList(BaseModel):
    pages: list[APIArtifactPage]


async def _project_to_api(project: state.ArtifactProject) -> APIArtifactProject:
    registrations = await state.list_artifact_registrations_async(project.id)
    return APIArtifactProject(
        id=project.id,
        name=project.name,
        slug=project.slug,
        registration_count=len(registrations),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def _artifact_to_api(
    registration: state.ArtifactRegistration,
) -> APIArtifact:
    project = await state.get_artifact_project_async(registration.project_id)
    return APIArtifact(
        id=registration.id,
        project_id=registration.project_id,
        project_name=project.name if project else "Unknown",
        project_slug=project.slug if project else "unknown",
        group_path=registration.group_path,
        source_path=registration.source_path,
        mode=registration.mode,
        entrypoint=registration.entrypoint,
        title=registration.title_override,
        slug=registration.slug,
        alias_path=(
            artifact_alias_path(registration, project) if project else f"/a/{registration.id}/"
        ),
        mount_path=f"/{registration.mount_path}" if registration.mount_path else None,
        exists=registration_exists(registration),
        created_at=registration.created_at,
        updated_at=registration.updated_at,
    )


async def _resolve_project(
    *,
    project_id: str | None,
    project_name: str | None,
    source_path: Path,
) -> state.ArtifactProject:
    if project_id and project_name:
        raise HTTPException(status_code=400, detail="Use project_id or project, not both")
    if project_id:
        project = await state.get_artifact_project_async(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Artifact project not found")
        return project
    try:
        name = clean_project_name(project_name or infer_project_name(source_path))
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project, _ = await state.get_or_create_artifact_project_async(name)
    return project


def _resolve_source(
    source_path: str, mode: str, entrypoint: str | None
) -> tuple[Path, str, str | None]:
    try:
        return resolve_registration(source_path, mode, entrypoint)
    except (ArtifactValidationError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_router(deps: APIDependencies) -> APIRouter:  # noqa: ARG001
    router = APIRouter()

    @router.get("/artifact-projects", response_model=APIArtifactProjectList)
    async def list_projects() -> APIArtifactProjectList:
        projects = await state.list_artifact_projects_async()
        return APIArtifactProjectList(
            projects=[await _project_to_api(project) for project in projects]
        )

    @router.post("/artifact-projects", response_model=APIArtifactProject)
    async def create_project(body: APIArtifactProjectCreate) -> APIArtifactProject:
        try:
            name = clean_project_name(body.name)
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project, _ = await state.get_or_create_artifact_project_async(name)
        return await _project_to_api(project)

    @router.patch("/artifact-projects/{project_id}", response_model=APIArtifactProject)
    async def rename_project(project_id: str, body: APIArtifactProjectUpdate) -> APIArtifactProject:
        try:
            name = clean_project_name(body.name)
            project = await state.rename_artifact_project_async(project_id, name)
        except (ArtifactValidationError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if project is None:
            raise HTTPException(status_code=404, detail="Artifact project not found")
        return await _project_to_api(project)

    @router.delete("/artifact-projects/{project_id}")
    async def delete_project(
        project_id: str, cascade: bool = Query(default=False)
    ) -> dict[str, int | bool]:
        try:
            removed, registration_count = await state.delete_artifact_project_async(
                project_id, cascade=cascade
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if cascade:
            clear_page_cache()
        return {
            "ok": True,
            "removed": removed,
            "registrations_removed": registration_count,
        }

    @router.get("/artifacts", response_model=APIArtifactList)
    async def list_artifacts(
        project_id: str | None = Query(default=None),
        project: str | None = Query(default=None, max_length=100),
        q: str | None = Query(default=None, max_length=200),
        group: str | None = Query(default=None, max_length=500),
        mode: str | None = Query(default=None),
        exists: bool | None = Query(default=None),
    ) -> APIArtifactList:
        if mode is not None and mode not in {"file", "site", "collection"}:
            raise HTTPException(status_code=400, detail="Invalid artifact mode")
        rows = await state.list_artifact_registrations_async(project_id)
        projects = {item.id: item for item in await state.list_artifact_projects_async()}
        if project:
            wanted_project = project.casefold()
            rows = [
                row
                for row in rows
                if (project_row := projects.get(row.project_id))
                and (
                    project_row.name.casefold() == wanted_project
                    or project_row.slug == wanted_project
                )
            ]
        if group:
            try:
                wanted_group = clean_group_path(group).casefold()
            except ArtifactValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            rows = [
                row
                for row in rows
                if row.group_path.casefold() == wanted_group
                or row.group_path.casefold().startswith(f"{wanted_group}/")
            ]
        if mode:
            rows = [row for row in rows if row.mode == mode]
        if exists is not None:
            rows = [row for row in rows if registration_exists(row) is exists]
        if q and q.strip():
            query = q.strip().casefold()
            matches: list[state.ArtifactRegistration] = []
            for row in rows:
                project_row = projects.get(row.project_id)
                fields = (
                    row.title_override or "",
                    row.slug,
                    row.group_path,
                    row.source_path,
                    row.mount_path,
                    project_row.name if project_row else "",
                    project_row.slug if project_row else "",
                )
                matched = any(query in field.casefold() for field in fields)
                if not matched:
                    try:
                        pages = await asyncio.to_thread(discover_pages, row)
                    except ArtifactValidationError:
                        pages = []
                    matched = any(
                        query in page.title.casefold() or query in page.relative_path.casefold()
                        for page in pages
                    )
                if matched:
                    matches.append(row)
            rows = matches
        return APIArtifactList(
            artifacts=[await _artifact_to_api(registration) for registration in rows]
        )

    @router.post("/artifacts", response_model=APIArtifactCreateResult)
    async def create_artifact(body: APIArtifactCreate) -> APIArtifactCreateResult:
        source, mode, entrypoint = _resolve_source(body.source_path, body.mode, body.entrypoint)
        project = await _resolve_project(
            project_id=body.project_id,
            project_name=body.project,
            source_path=source,
        )
        try:
            group_path = clean_group_path(body.group_path)
            title = clean_title(body.title)
            slug = clean_alias_slug(body.slug)
            mount_path = clean_mount_path(body.mount_path)
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        existing = await state.find_artifact_registration_async(str(source), mode, entrypoint)
        if existing:
            if (
                existing.project_id != project.id
                or existing.group_path != group_path
                or existing.title_override != title
                or (slug is not None and existing.slug != slug)
                or (body.mount_path is not None and existing.mount_path != mount_path)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Artifact is already registered with different metadata; update it instead"
                    ),
                )
            api = await _artifact_to_api(existing)
            return APIArtifactCreateResult(**api.model_dump(), created=False)

        try:
            registration = await state.create_artifact_registration_async(
                project_id=project.id,
                group_path=group_path,
                source_path=str(source),
                mode=mode,
                entrypoint=entrypoint,
                title_override=title,
                slug=slug,
                mount_path=mount_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            await asyncio.to_thread(discover_pages, registration, refresh=True)
        except ArtifactValidationError as exc:
            await state.delete_artifact_registration_async(registration.id)
            clear_page_cache(registration.id)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        api = await _artifact_to_api(registration)
        return APIArtifactCreateResult(**api.model_dump(), created=True)

    @router.get("/artifacts/{registration_id}", response_model=APIArtifact)
    async def get_artifact(registration_id: str) -> APIArtifact:
        registration = await state.get_artifact_registration_async(registration_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return await _artifact_to_api(registration)

    @router.patch("/artifacts/{registration_id}", response_model=APIArtifact)
    async def update_artifact(registration_id: str, body: APIArtifactUpdate) -> APIArtifact:
        registration = await state.get_artifact_registration_async(registration_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        source_text = body.source_path or registration.source_path
        requested_mode = body.mode or registration.mode
        if "entrypoint" in body.model_fields_set:
            requested_entrypoint = body.entrypoint
        elif body.mode is not None and body.mode != registration.mode:
            requested_entrypoint = None
        else:
            requested_entrypoint = registration.entrypoint
        source, mode, entrypoint = _resolve_source(
            source_text, requested_mode, requested_entrypoint
        )
        project = (
            await _resolve_project(
                project_id=body.project_id,
                project_name=body.project,
                source_path=source,
            )
            if body.project_id or body.project
            else await state.get_artifact_project_async(registration.project_id)
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Artifact project not found")

        try:
            group_path = (
                clean_group_path(body.group_path)
                if body.group_path is not None
                else registration.group_path
            )
            title = (
                clean_title(body.title)
                if "title" in body.model_fields_set
                else registration.title_override
            )
            slug = clean_alias_slug(body.slug) if body.slug is not None else registration.slug
            mount_path = (
                clean_mount_path(body.mount_path)
                if "mount_path" in body.model_fields_set
                else registration.mount_path
            )
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        duplicate = await state.find_artifact_registration_async(str(source), mode, entrypoint)
        if duplicate and duplicate.id != registration_id:
            raise HTTPException(status_code=409, detail="Artifact source is already registered")

        candidate = SimpleNamespace(
            id=registration.id,
            project_id=project.id,
            group_path=group_path,
            source_path=str(source),
            mode=mode,
            entrypoint=entrypoint,
            title_override=title,
            slug=slug,
            mount_path=mount_path,
        )
        try:
            await asyncio.to_thread(discover_pages, candidate, refresh=True)  # type: ignore[arg-type]
        except ArtifactValidationError as exc:
            clear_page_cache(registration_id)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            updated = await state.update_artifact_registration_async(
                registration_id,
                project_id=project.id,
                group_path=group_path,
                source_path=str(source),
                mode=mode,
                entrypoint=entrypoint,
                title_override=title,
                slug=slug,
                mount_path=mount_path,
            )
        except ValueError as exc:
            clear_page_cache(registration_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        assert updated is not None
        return await _artifact_to_api(updated)

    @router.get("/artifacts/{registration_id}/pages", response_model=APIArtifactPageList)
    async def list_pages(
        registration_id: str, refresh: bool = Query(default=False)
    ) -> APIArtifactPageList:
        registration = await state.get_artifact_registration_async(registration_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        try:
            pages = await asyncio.to_thread(discover_pages, registration, refresh=refresh)
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project = await state.get_artifact_project_async(registration.project_id)
        return APIArtifactPageList(
            pages=[
                APIArtifactPage(
                    relative_path=page.relative_path,
                    title=page.title,
                    group_path=page.group_path,
                    serve_path=(
                        artifact_alias_path(
                            registration,
                            project,
                            page.relative_path if registration.mode == "collection" else "",
                        )
                        if project
                        else page.serve_path
                    ),
                )
                for page in pages
            ]
        )

    @router.post("/artifacts/{registration_id}/rescan", response_model=APIArtifactPageList)
    async def rescan_artifact(registration_id: str) -> APIArtifactPageList:
        registration = await state.get_artifact_registration_async(registration_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        try:
            pages = await asyncio.to_thread(discover_pages, registration, refresh=True)
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project = await state.get_artifact_project_async(registration.project_id)
        return APIArtifactPageList(
            pages=[
                APIArtifactPage(
                    relative_path=page.relative_path,
                    title=page.title,
                    group_path=page.group_path,
                    serve_path=(
                        artifact_alias_path(
                            registration,
                            project,
                            page.relative_path if registration.mode == "collection" else "",
                        )
                        if project
                        else page.serve_path
                    ),
                )
                for page in pages
            ]
        )

    @router.delete("/artifacts/{registration_id}")
    async def delete_artifact(registration_id: str) -> dict[str, bool]:
        removed = await state.delete_artifact_registration_async(registration_id)
        clear_page_cache(registration_id)
        return {"ok": True, "removed": removed}

    return router
