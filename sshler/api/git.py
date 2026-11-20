"""Git integration API for Commander.

Each route runs git through the runner of its box (``box_runner``) with the same words
on both kinds, and does not ask which kind of box it holds. Show and blame ask git for
the repository's root, then work out the file's path under the root here, with the
runner's ``file`` (the one place the kinds differ: the local box follows links, a
remote box can only normalise the text). Nothing but git runs on a remote box.
"""

from __future__ import annotations

import logging
import posixpath
import re
from datetime import datetime
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..validation import ValidationError
from .box_runner import BoxRunner, box_runner, remote_command_line
from .dependencies import APIDependencies

logger = logging.getLogger(__name__)

# A ref is given to git as a bare word: one starting with "-" would be read as an
# option (`--output` makes `git diff` write a file), so it is refused.
_REF_RE = re.compile(r"^(?!-)[a-zA-Z0-9_./-]{1,200}$")
_LOG_FIELD_SEP = "\x1f"
MAX_BLAME_LINES = 10_000
MAX_SHOW_BYTES = 1_048_576  # 1 MB
MAX_DIFF_FILES = 2000
GIT_TIMEOUT = 30
BLAME_TIMEOUT = 60


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


async def _git(
    runner: BoxRunner,
    words: list[str],
    folder: str,
    timeout: float | None = None,
    output_limit: int | None = None,
) -> tuple[bool, str]:
    """Run git on the box in ``folder``: (success, its output without surrounding
    space). Past the limit (``GIT_TIMEOUT`` unless given) it is the 504; a run that
    fails otherwise (no git program, a dropped channel) is (False, ""). With
    ``output_limit`` the status is not known, so success is False."""
    try:
        ran = await runner.run(
            words,
            folder=folder,
            timeout=GIT_TIMEOUT if timeout is None else timeout,
            output_limit=output_limit,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Git command timed out") from exc
    except SSHError:
        raise
    except Exception as exc:  # pragma: no cover - process failures vary
        logger.warning("Git command failed in %s: %s", folder, exc)
        return False, ""
    return ran.ok, ran.output.strip()


async def _git_folder(runner: BoxRunner, directory: str) -> str:
    """The folder a git command runs in: on a remote box ``~`` is the home and a path
    that fails its check is a 400."""
    try:
        return await runner.folder(directory)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def git_log_words(fmt: str, limit: int) -> list[str]:
    return ["git", "log", f"--pretty=format:{fmt}", "-n", str(limit)]


def git_diff_files_words(ref_a: str, ref_b: str) -> list[str]:
    """Files changed between two validated refs, with their status letter. The ``--``
    ends the revisions: a file or folder named like a ref is not read as one."""
    return ["git", "diff", "--name-status", ref_a, ref_b, "--"]


async def _repo_root(runner: BoxRunner, folder: str) -> str | None:
    ok, root = await _git(runner, ["git", "rev-parse", "--show-toplevel"], folder)
    if not ok or not root:
        return None
    return root


async def _file_in_repo(runner: BoxRunner, directory: str, path: str) -> tuple[str, str]:
    """The root of the repository at ``directory`` and the path of ``path`` (absolute,
    or relative to ``directory``) under it, ``/`` separated. A path that is empty is a
    400 before anything runs; a folder outside any repository is the 404; a path
    outside the repository, or the root itself, is a 400."""
    wanted = _require_path(path)
    folder = await _git_folder(runner, directory)
    root = await _repo_root(runner, folder)
    if not root:
        raise HTTPException(status_code=404, detail="Not a git repository")
    try:
        file = await runner.file(wanted, folder)
        top = await runner.file(root, folder)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    relative = _relative_to(file, top)
    if relative is None:
        relative = await _named_from_the_folder(runner, file, folder)
    if relative is None:
        raise HTTPException(status_code=400, detail="Path is outside the git repository")
    if relative == ".":
        raise HTTPException(
            status_code=400, detail="Path must point to a file inside the repository"
        )
    return root, relative


async def _named_from_the_folder(runner: BoxRunner, file: PurePath, folder: str) -> str | None:
    """``file`` as git names it, worked out from where git says ``folder`` is in the
    repository; None when that is outside it.

    For a folder reached through a link: git names the root by its real path, so the
    text of a path through the link is not under the root, while the folder's own place
    in the repository (``git rev-parse --show-prefix``) is the same either way."""
    ok, prefix = await _git(runner, ["git", "rev-parse", "--show-prefix"], folder)
    if not ok:
        return None
    here = await runner.file(folder, folder)
    from_the_folder = posixpath.relpath(file.as_posix(), here.as_posix())
    named = posixpath.normpath(posixpath.join(prefix, from_the_folder))
    return None if named == ".." or named.startswith("../") else named


def _relative_to(file: PurePath, top: PurePath) -> str | None:
    """``file`` under ``top`` as git names it (``/`` separated, ``.`` for the root
    itself); None when it is not under it."""
    try:
        return file.relative_to(top).as_posix()
    except ValueError:
        return None


def remote_git_log_command(directory: str, fmt: str, limit: int) -> str:
    """The line the remote runner sends for the log."""
    return remote_command_line(git_log_words(fmt, limit), directory)


def remote_git_diff_files_command(directory: str, ref_a: str, ref_b: str) -> str:
    """The line the remote runner sends for the diff of two refs."""
    return remote_command_line(git_diff_files_words(ref_a, ref_b), directory)


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

        try:
            async with box_runner(deps, box, application_config) as runner:
                folder = await _git_folder(runner, directory)
                ok, output = await _git(runner, git_log_words(fmt, limit), folder)
                if not ok:
                    # A repository with no commit has no log: the root tells it from a
                    # folder outside any repository.
                    is_repo = bool(await _repo_root(runner, folder))
                    return GitLogResponse(
                        box=name, directory=directory, commits=[], is_repo=is_repo
                    )
        except SSHError as exc:
            return GitLogResponse(box=name, directory=directory, commits=[], error=str(exc))

        if not output:
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

        try:
            async with box_runner(deps, box, application_config) as runner:
                root, relative = await _file_in_repo(runner, directory, path)
                shown = f"{ref}:{relative}"
                # The cut leaves no exit status: an empty answer is asked once more,
                # to tell an empty file from one that is not at the ref.
                _, content = await _git(
                    runner, ["git", "show", shown], root, output_limit=MAX_SHOW_BYTES
                )
                if not content:
                    found, _ = await _git(runner, ["git", "cat-file", "-e", shown], root)
                    if not found:
                        raise HTTPException(status_code=404, detail=f"File not found at ref {ref}")
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return GitShowResponse(content=content[:MAX_SHOW_BYTES], ref=ref, path=path)

    @router.get("/boxes/{name}/git/blame", response_model=GitBlameResponse)
    async def git_blame(
        name: str,
        directory: str = "/",
        path: str = "",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> GitBlameResponse:
        box = deps.get_box_or_404(application_config, name)

        try:
            async with box_runner(deps, box, application_config) as runner:
                root, relative = await _file_in_repo(runner, directory, path)
                ok, output = await _git(
                    runner, ["git", "blame", "--porcelain", "--", relative], root, BLAME_TIMEOUT
                )
        except SSHError as exc:
            return GitBlameResponse(box=name, path=path, lines=[], error=str(exc))

        if not ok:
            return GitBlameResponse(
                box=name, path=path, lines=[], error="Not a git repository or file not tracked"
            )

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

        try:
            async with box_runner(deps, box, application_config) as runner:
                root = await _repo_root(runner, await _git_folder(runner, directory))
                if not root:
                    return GitBranchesResponse(
                        box=name, directory=directory, branches=[], is_repo=False
                    )
                ok, branch_output = await _git(runner, ["git", "branch", f"--format={fmt}"], root)
                if not ok:
                    return GitBranchesResponse(
                        box=name, directory=directory, branches=[], is_repo=False
                    )
        except SSHError as exc:
            return GitBranchesResponse(box=name, directory=directory, branches=[], error=str(exc))

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
            root=root,
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

        try:
            async with box_runner(deps, box, application_config) as runner:
                folder = await _git_folder(runner, directory)
                ok, output = await _git(runner, git_diff_files_words(ref_a, ref_b), folder)
                if not ok:
                    output = ""  # not a repository, or git stopped: no files
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
                commit_info[current_hash]["date"] = datetime.fromtimestamp(timestamp).strftime(
                    "%Y-%m-%d"
                )
            except (ValueError, OSError):
                continue

    return lines
