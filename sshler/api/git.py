"""Git integration API for Commander."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
import re
import shlex

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"^[a-zA-Z0-9_./-]{1,200}$")
_LOG_FIELD_SEP = "\x1f"
MAX_BLAME_LINES = 10_000
MAX_SHOW_BYTES = 1_048_576  # 1 MB
MAX_DIFF_FILES = 2000


class GitCommit(BaseModel):
    hash: str
    short_hash: str
    message: str
    author: str
    date: str


class GitLogResponse(BaseModel):
    box: str
    directory: str
    commits: list[GitCommit]
    is_repo: bool = False
    error: str | None = None


class GitShowResponse(BaseModel):
    content: str
    ref: str
    path: str


class GitBlameLine(BaseModel):
    line_number: int
    content: str
    commit: str
    author: str
    date: str


class GitBlameResponse(BaseModel):
    box: str
    path: str
    lines: list[GitBlameLine]
    truncated: bool = False
    error: str | None = None


class GitBranch(BaseModel):
    name: str
    is_current: bool
    last_commit: str | None = None


class GitBranchesResponse(BaseModel):
    box: str
    directory: str
    branches: list[GitBranch]
    root: str | None = None
    is_repo: bool = False
    error: str | None = None


class GitDiffFile(BaseModel):
    path: str
    status: str


class GitDiffFilesResponse(BaseModel):
    box: str
    directory: str
    ref_a: str
    ref_b: str
    files: list[GitDiffFile]
    error: str | None = None


def _validate_ref(ref: str) -> str:
    if not _REF_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid git ref")
    return ref


def _require_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Path is required")
    return cleaned


async def _run_local_git(args: list[str], cwd: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a local git command and return (success, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0, stdout.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Git command timed out")
    except Exception as exc:  # pragma: no cover - local process failures vary
        logger.warning("Local git command failed in %s: %s", cwd, exc)
        return False, ""


async def _run_remote_git(connection, cmd: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a remote git command via SSH and return (success, stdout)."""
    try:
        result = await asyncio.wait_for(connection.run(cmd, check=False), timeout=timeout)
        return (result.exit_status or 0) == 0, (result.stdout or "").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Git command timed out")
    except Exception as exc:  # pragma: no cover - remote process failures vary
        logger.warning("Remote git command failed: %s", exc)
        return False, ""


async def _get_local_repo_root(directory: str) -> str | None:
    normalized = _normalize_local_path(directory)
    ok, root = await _run_local_git(["git", "rev-parse", "--show-toplevel"], cwd=normalized)
    if not ok or not root:
        return None
    return root


async def _get_local_root_and_relpath(directory: str, path: str) -> tuple[str, str]:
    root = await _get_local_repo_root(directory)
    if not root:
        raise HTTPException(status_code=404, detail="Not a git repository")

    normalized_directory = _normalize_local_path(directory)
    requested = Path(_require_path(path)).expanduser()
    candidate = requested if requested.is_absolute() else Path(normalized_directory) / requested
    resolved = candidate.resolve(strict=False)

    try:
        relative = resolved.relative_to(Path(root).resolve(strict=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside the git repository") from exc

    if str(relative) in {"", "."}:
        raise HTTPException(status_code=400, detail="Path must point to a file inside the repository")

    return root, str(relative)


def _build_remote_repo_path_command(directory: str, path: str) -> str:
    return (
        f"cd {shlex.quote(directory)} && "
        "ROOT=$(git rev-parse --show-toplevel 2>/dev/null) && "
        f"REL=$(ROOT=\"$ROOT\" DIR={shlex.quote(directory)} TARGET={shlex.quote(path)} "
        "python3 -c 'import os, sys; "
        "root = os.environ[\"ROOT\"]; "
        "directory = os.environ[\"DIR\"]; "
        "target = os.environ[\"TARGET\"]; "
        "candidate = target if os.path.isabs(target) else os.path.join(directory, target); "
        "rel = os.path.relpath(os.path.normpath(candidate), root); "
        "(rel in {\".\", \"..\"} or rel.startswith(\"../\")) and sys.exit(3); "
        "print(rel)' 2>/dev/null)"
    )


def _validate_remote_directory(directory: str) -> str:
    try:
        return PathValidator.validate_remote_path(directory)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_remote_file_path(path: str) -> str:
    try:
        return PathValidator.validate_remote_path(_require_path(path))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["git"])

    @router.get("/boxes/{name}/git/log", response_model=GitLogResponse)
    async def git_log(
        name: str,
        directory: str = "/",
        limit: int = Query(50, ge=1, le=200),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitLogResponse:
        box = deps.get_box_or_404(application_config, name)
        fmt = f"%H{_LOG_FIELD_SEP}%h{_LOG_FIELD_SEP}%s{_LOG_FIELD_SEP}%an{_LOG_FIELD_SEP}%aI"

        if box.transport == "local":
            root = await _get_local_repo_root(directory)
            if not root:
                return GitLogResponse(box=name, directory=directory, commits=[], is_repo=False)
            ok, output = await _run_local_git(
                ["git", "log", f"--pretty=format:{fmt}", "-n", str(limit)],
                cwd=root,
            )
        else:
            validated = _validate_remote_directory(directory)
            cmd = (
                f"cd {shlex.quote(validated)} && "
                f"git log --pretty=format:{shlex.quote(fmt)} -n {limit} 2>/dev/null"
            )
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(box, lambda: deps.connect_for_box(box, application_config)) as conn:
                    ok, output = await _run_remote_git(conn, cmd)
            except SSHError as exc:
                return GitLogResponse(box=name, directory=directory, commits=[], error=str(exc))
            if not ok:
                return GitLogResponse(box=name, directory=directory, commits=[], is_repo=False)

        if not ok or not output:
            return GitLogResponse(box=name, directory=directory, commits=[], is_repo=True)

        commits = []
        for line in output.splitlines():
            parts = line.split(_LOG_FIELD_SEP, maxsplit=4)
            if len(parts) == 5:
                commits.append(
                    GitCommit(
                        hash=parts[0],
                        short_hash=parts[1],
                        message=parts[2],
                        author=parts[3],
                        date=parts[4],
                    )
                )
        return GitLogResponse(box=name, directory=directory, commits=commits, is_repo=True)

    @router.get("/boxes/{name}/git/show", response_model=GitShowResponse)
    async def git_show(
        name: str,
        directory: str = "/",
        path: str = "",
        ref: str = "HEAD",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitShowResponse:
        box = deps.get_box_or_404(application_config, name)
        ref = _validate_ref(ref)

        if box.transport == "local":
            root, rel_path = await _get_local_root_and_relpath(directory, path)
            ok, content = await _run_local_git(["git", "show", f"{ref}:{rel_path}"], cwd=root)
            if not ok:
                raise HTTPException(status_code=404, detail=f"File not found at ref {ref}")
        else:
            validated_dir = _validate_remote_directory(directory)
            validated_path = _validate_remote_file_path(path)
            cmd = (
                f"{_build_remote_repo_path_command(validated_dir, validated_path)} && "
                f"git -C \"$ROOT\" show {shlex.quote(ref)}:\"$REL\" 2>/dev/null | head -c {MAX_SHOW_BYTES}"
            )
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(box, lambda: deps.connect_for_box(box, application_config)) as conn:
                    ok, content = await _run_remote_git(conn, cmd)
            except SSHError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if not ok:
                raise HTTPException(status_code=404, detail=f"File not found at ref {ref}")

        return GitShowResponse(content=content[:MAX_SHOW_BYTES], ref=ref, path=path)

    @router.get("/boxes/{name}/git/blame", response_model=GitBlameResponse)
    async def git_blame(
        name: str,
        directory: str = "/",
        path: str = "",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitBlameResponse:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            root, rel_path = await _get_local_root_and_relpath(directory, path)
            ok, output = await _run_local_git(
                ["git", "blame", "--porcelain", "--", rel_path],
                cwd=root,
                timeout=60,
            )
        else:
            validated_dir = _validate_remote_directory(directory)
            validated_path = _validate_remote_file_path(path)
            cmd = (
                f"{_build_remote_repo_path_command(validated_dir, validated_path)} && "
                "git -C \"$ROOT\" blame --porcelain -- \"$REL\" 2>/dev/null"
            )
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(box, lambda: deps.connect_for_box(box, application_config)) as conn:
                    ok, output = await _run_remote_git(conn, cmd, timeout=60)
            except SSHError as exc:
                return GitBlameResponse(box=name, path=path, lines=[], error=str(exc))

        if not ok:
            return GitBlameResponse(box=name, path=path, lines=[], error="Not a git repository or file not tracked")

        lines = _parse_blame_porcelain(output)
        truncated = len(lines) > MAX_BLAME_LINES
        if truncated:
            lines = lines[:MAX_BLAME_LINES]

        return GitBlameResponse(box=name, path=path, lines=lines, truncated=truncated)

    @router.get("/boxes/{name}/git/branches", response_model=GitBranchesResponse)
    async def git_branches(
        name: str,
        directory: str = "/",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitBranchesResponse:
        box = deps.get_box_or_404(application_config, name)
        fmt = "%(refname:short)|%(HEAD)|%(objectname:short)"

        if box.transport == "local":
            root = await _get_local_repo_root(directory)
            if not root:
                return GitBranchesResponse(box=name, directory=directory, branches=[], is_repo=False)
            ok, branch_output = await _run_local_git(["git", "branch", f"--format={fmt}"], cwd=root)
            if not ok:
                return GitBranchesResponse(box=name, directory=directory, branches=[], is_repo=False)
        else:
            validated = _validate_remote_directory(directory)
            cmd = (
                f"cd {shlex.quote(validated)} && "
                f"git branch --format={shlex.quote(fmt)} 2>/dev/null && "
                "echo '---ROOT---' && "
                "git rev-parse --show-toplevel 2>/dev/null"
            )
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(box, lambda: deps.connect_for_box(box, application_config)) as conn:
                    ok, output = await _run_remote_git(conn, cmd)
            except SSHError as exc:
                return GitBranchesResponse(box=name, directory=directory, branches=[], error=str(exc))
            if not ok:
                return GitBranchesResponse(box=name, directory=directory, branches=[], is_repo=False)
            if "---ROOT---" in output:
                branch_output, root = output.rsplit("---ROOT---", 1)
                branch_output = branch_output.strip()
                root = root.strip()
            else:
                branch_output = output
                root = None

        branches = []
        for line in branch_output.splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                branches.append(
                    GitBranch(
                        name=parts[0].strip(),
                        is_current=parts[1].strip() == "*",
                        last_commit=parts[2].strip() or None,
                    )
                )

        return GitBranchesResponse(
            box=name,
            directory=directory,
            branches=branches,
            root=root if root else None,
            is_repo=True,
        )

    @router.get("/boxes/{name}/git/diff-files", response_model=GitDiffFilesResponse)
    async def git_diff_files(
        name: str,
        directory: str = "/",
        ref_a: str = "HEAD",
        ref_b: str = "HEAD",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitDiffFilesResponse:
        box = deps.get_box_or_404(application_config, name)
        ref_a = _validate_ref(ref_a)
        ref_b = _validate_ref(ref_b)

        if box.transport == "local":
            root = await _get_local_repo_root(directory)
            if not root:
                return GitDiffFilesResponse(box=name, directory=directory, ref_a=ref_a, ref_b=ref_b, files=[])
            ok, output = await _run_local_git(
                ["git", "diff", "--name-status", ref_a, ref_b],
                cwd=root,
            )
        else:
            validated = _validate_remote_directory(directory)
            cmd = (
                f"cd {shlex.quote(validated)} && "
                f"git diff --name-status {shlex.quote(ref_a)} {shlex.quote(ref_b)} 2>/dev/null"
            )
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(box, lambda: deps.connect_for_box(box, application_config)) as conn:
                    ok, output = await _run_remote_git(conn, cmd)
            except SSHError as exc:
                return GitDiffFilesResponse(
                    box=name,
                    directory=directory,
                    ref_a=ref_a,
                    ref_b=ref_b,
                    files=[],
                    error=str(exc),
                )

        files = []
        for line in output.splitlines()[:MAX_DIFF_FILES]:
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0].strip()
                file_path = parts[-1].strip()
                if status and file_path:
                    files.append(GitDiffFile(path=file_path, status=status[0]))

        return GitDiffFilesResponse(
            box=name,
            directory=directory,
            ref_a=ref_a,
            ref_b=ref_b,
            files=files,
        )

    return router


def _parse_blame_porcelain(output: str) -> list[GitBlameLine]:
    """Parse git blame --porcelain output into structured lines."""
    lines: list[GitBlameLine] = []
    commit_info: dict[str, dict[str, str]] = {}
    current_hash = ""
    line_number = 0

    for raw_line in output.splitlines():
        if not raw_line:
            continue

        if raw_line.startswith("\t"):
            line_number += 1
            info = commit_info.get(current_hash, {})
            lines.append(
                GitBlameLine(
                    line_number=line_number,
                    content=raw_line[1:],
                    commit=current_hash[:7],
                    author=info.get("author", ""),
                    date=info.get("date", ""),
                )
            )
            continue

        if len(raw_line) >= 40 and raw_line[0] in "0123456789abcdef":
            parts = raw_line.split()
            if len(parts) >= 3 and len(parts[0]) == 40:
                current_hash = parts[0]
                commit_info.setdefault(current_hash, {})
                continue

        if raw_line.startswith("author ") and current_hash in commit_info:
            commit_info[current_hash]["author"] = raw_line[7:]
        elif raw_line.startswith("author-time ") and current_hash in commit_info:
            try:
                timestamp = int(raw_line[12:])
                commit_info[current_hash]["date"] = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                continue

    return lines
