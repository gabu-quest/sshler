"""Local artifact discovery and isolated static-file serving."""

from __future__ import annotations

import html
import logging
import mimetypes
import os
import re
import stat
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import unquote_to_bytes, urlsplit

from . import state

logger = logging.getLogger(__name__)

ARTIFACT_MODES = frozenset({"file", "site", "collection"})
MAX_COLLECTION_PAGES = 1_000
MAX_TITLE_BYTES = 256 * 1024

_TITLE_RE = re.compile(r"<title[^>]*>\s*(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,32}$")
_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_MOUNT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")
_CACHE_LOCK = threading.RLock()
_PAGE_CACHE: dict[str, list[ArtifactPage]] = {}


class ArtifactValidationError(ValueError):
    """Raised when an artifact registration or request is unsafe or invalid."""


@dataclass(frozen=True)
class ArtifactPage:
    relative_path: str
    title: str
    group_path: str
    serve_path: str


def clear_page_cache(registration_id: str | None = None) -> None:
    with _CACHE_LOCK:
        if registration_id is None:
            _PAGE_CACHE.clear()
        else:
            _PAGE_CACHE.pop(registration_id, None)


def clean_project_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 100 or any(ord(char) < 32 for char in cleaned):
        raise ArtifactValidationError("Project name must be 1-100 printable characters")
    return cleaned


def clean_group_path(group_path: str | None) -> str:
    if not group_path:
        return ""
    raw = group_path.replace("\\", "/").strip("/")
    parts = raw.split("/")
    if (
        len(raw) > 500
        or any(not part or part in {".", ".."} for part in parts)
        or any(len(part) > 100 or any(ord(char) < 32 for char in part) for part in parts)
    ):
        raise ArtifactValidationError("Invalid artifact group path")
    return "/".join(parts)


def clean_title(title: str | None) -> str | None:
    if title is None:
        return None
    cleaned = " ".join(title.split())
    if not cleaned:
        return None
    if len(cleaned) > 200 or any(ord(char) < 32 for char in cleaned):
        raise ArtifactValidationError("Artifact title must be at most 200 printable characters")
    return cleaned


def clean_entrypoint(entrypoint: str | None) -> str | None:
    if entrypoint is None:
        return None
    raw = entrypoint.replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
    ):
        raise ArtifactValidationError("Entrypoint must be a non-hidden relative path")
    return path.as_posix()


def clean_alias_slug(slug: str | None) -> str | None:
    if slug is None:
        return None
    cleaned = state.artifact_slug(slug)
    if not _ALIAS_RE.fullmatch(cleaned):
        raise ArtifactValidationError(
            "Artifact alias must contain lowercase letters, numbers, and hyphens"
        )
    return cleaned


def clean_mount_path(mount_path: str | None) -> str:
    if not mount_path:
        return ""
    raw = mount_path.replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or len(raw) > 500
        or any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts)
        or any(not _MOUNT_SEGMENT_RE.fullmatch(part) for part in path.parts)
        or path.parts[0] in {"a", "r"}
    ):
        raise ArtifactValidationError(
            "Mount path must be a non-hidden relative URL outside /a and /r"
        )
    return path.as_posix()


def artifact_alias_path(
    registration: state.ArtifactRegistration,
    project: state.ArtifactProject,
    relative_path: str = "",
) -> str:
    root = f"/r/{project.slug}/{registration.slug}/"
    return f"{root}{relative_path}" if relative_path else root


def _is_html_file(path: Path) -> bool:
    return path.suffix.casefold() in {".html", ".htm"}


def resolve_registration(
    source: str,
    requested_mode: str = "auto",
    entrypoint: str | None = None,
) -> tuple[Path, str, str | None]:
    """Validate and canonicalize a requested registration."""
    source_path = Path(source).expanduser().resolve(strict=True)
    if requested_mode not in ARTIFACT_MODES | {"auto"}:
        raise ArtifactValidationError("Mode must be auto, file, site, or collection")

    mode = requested_mode
    if mode == "auto":
        if source_path.is_file():
            mode = "file"
        elif (source_path / "index.html").is_file():
            mode = "site"
        else:
            mode = "collection"

    cleaned_entrypoint = clean_entrypoint(entrypoint)
    if mode == "file":
        if not source_path.is_file() or not _is_html_file(source_path):
            raise ArtifactValidationError("File artifacts require an HTML file")
        if cleaned_entrypoint is not None:
            raise ArtifactValidationError("File artifacts do not use an entrypoint")
        return source_path, mode, None

    if not source_path.is_dir():
        raise ArtifactValidationError(f"{mode.title()} artifacts require a directory")

    if mode == "site":
        cleaned_entrypoint = cleaned_entrypoint or "index.html"
        target = (source_path / cleaned_entrypoint).resolve(strict=True)
        if (
            not target.is_relative_to(source_path)
            or not target.is_file()
            or not _is_html_file(target)
        ):
            raise ArtifactValidationError(
                "Site entrypoint must be an HTML file inside the site root"
            )
    elif cleaned_entrypoint is not None:
        raise ArtifactValidationError("Collection artifacts do not use an entrypoint")

    return source_path, mode, cleaned_entrypoint


def infer_project_name(source_path: Path) -> str:
    """Infer a stable project label from the nearest worktree or source directory."""
    start = source_path if source_path.is_dir() else source_path.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return clean_project_name(candidate.name)
    return clean_project_name(start.name or "Artifacts")


def title_for(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_TITLE_BYTES).decode("utf-8", errors="replace")
    except OSError:
        raw = ""
    match = _TITLE_RE.search(raw)
    if match:
        title = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
        if title:
            return title[:200]
    return path.stem.replace("-", " ").replace("_", " ").title()


def registration_exists(registration: state.ArtifactRegistration) -> bool:
    try:
        source = Path(registration.source_path)
        if registration.mode == "file":
            return source.is_file()
        if not source.is_dir():
            return False
        if registration.mode == "site" and registration.entrypoint:
            return (source / registration.entrypoint).is_file()
        return True
    except OSError:
        return False


def _joined_group(prefix: str, relative_parent: str) -> str:
    parts = [part for part in (prefix, relative_parent) if part and part != "."]
    return "/".join(parts)


def discover_pages(
    registration: state.ArtifactRegistration,
    *,
    refresh: bool = False,
) -> list[ArtifactPage]:
    with _CACHE_LOCK:
        if not refresh and registration.id in _PAGE_CACHE:
            return list(_PAGE_CACHE[registration.id])

    source = Path(registration.source_path)
    pages: list[ArtifactPage] = []
    if registration.mode == "file":
        if source.is_file():
            pages.append(
                ArtifactPage(
                    relative_path="",
                    title=registration.title_override or title_for(source),
                    group_path=registration.group_path,
                    serve_path=f"/a/{registration.id}/",
                )
            )
    elif registration.mode == "site":
        entrypoint = registration.entrypoint or "index.html"
        target = source / entrypoint
        if target.is_file():
            pages.append(
                ArtifactPage(
                    relative_path=entrypoint,
                    title=registration.title_override or title_for(target),
                    group_path=registration.group_path,
                    serve_path=f"/a/{registration.id}/",
                )
            )
    elif source.is_dir():
        for root_text, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = sorted(
                directory
                for directory in dirs
                if not directory.startswith(".") and not (Path(root_text) / directory).is_symlink()
            )
            root = Path(root_text)
            for filename in sorted(files):
                if filename.startswith("."):
                    continue
                path = root / filename
                if not _is_html_file(path) or path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                pages.append(
                    ArtifactPage(
                        relative_path=relative,
                        title=title_for(path),
                        group_path=_joined_group(
                            registration.group_path, path.relative_to(source).parent.as_posix()
                        ),
                        serve_path=f"/a/{registration.id}/{relative}",
                    )
                )
                if len(pages) > MAX_COLLECTION_PAGES:
                    raise ArtifactValidationError(
                        f"Collection exceeds the {MAX_COLLECTION_PAGES}-page limit"
                    )

    with _CACHE_LOCK:
        _PAGE_CACHE[registration.id] = list(pages)
    return pages


def _decode_relative_path(raw_path: str) -> str:
    try:
        decoded = unquote_to_bytes(raw_path).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ArtifactValidationError("Invalid URL encoding") from exc
    if "\x00" in decoded or "\\" in decoded:
        raise ArtifactValidationError("Invalid artifact path")
    stripped = decoded.strip("/")
    if not stripped:
        return ""
    parts = PurePosixPath(stripped).parts
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ArtifactValidationError("Invalid artifact path")
    return PurePosixPath(*parts).as_posix()


def resolve_served_file(
    registration: state.ArtifactRegistration,
    raw_relative_path: str,
) -> Path:
    """Resolve one sidecar request without allowing registration-root escape."""
    relative = _decode_relative_path(raw_relative_path)
    source = Path(registration.source_path)
    if registration.mode == "file":
        if relative:
            raise ArtifactValidationError("File artifact has no sibling assets")
        target = source.resolve(strict=True)
        allowed_root = target.parent
    else:
        allowed_root = source.resolve(strict=True)
        if not relative:
            if registration.mode != "site" or not registration.entrypoint:
                raise ArtifactValidationError("Collection has no root page")
            relative = registration.entrypoint
        target = (allowed_root / relative).resolve(strict=True)
        if not target.is_relative_to(allowed_root):
            raise ArtifactValidationError("Artifact path escapes its registered root")

    file_stat = target.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ArtifactValidationError("Artifact path is not a regular file")
    return target


class _ArtifactHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ArtifactSidecar:
    """Own the loopback-only static server for registered artifacts."""

    def __init__(self, requested_port: int = 0) -> None:
        self.requested_port = requested_port
        self.port: int | None = None
        self._server: _ArtifactHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        if self._server is not None and self.port is not None:
            return self.port

        class Handler(BaseHTTPRequestHandler):
            server_version = "sshler-artifacts"
            sys_version = ""

            def log_message(self, format: str, *args: object) -> None:
                logger.debug("artifact request: " + format, *args)

            def _headers(self, length: int, content_type: str) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")

            def _error(self, status_code: int, message: bytes) -> None:
                self.send_response(status_code)
                self._headers(len(message), "text/plain; charset=utf-8")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(message)

            def _serve(self) -> None:
                # Binding to loopback does not stop DNS rebinding: a hostile page can
                # resolve its own hostname to 127.0.0.1 and read us as same-origin.
                # Only answer requests addressed to a loopback name on our port.
                port = cast("_ArtifactHTTPServer", self.server).server_port
                allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
                if (self.headers.get("Host") or "").lower() not in allowed_hosts:
                    self._error(HTTPStatus.MISDIRECTED_REQUEST, b"Misdirected request")
                    return
                path = urlsplit(self.path).path
                match = re.fullmatch(r"/a/([^/]+)(?:/(.*))?", path)
                registration = None
                raw_relative = ""
                if match and _ID_RE.fullmatch(match.group(1)):
                    registration = state.get_artifact_registration(match.group(1))
                    raw_relative = match.group(2) or ""
                else:
                    alias_match = re.fullmatch(r"/r/([^/]+)/([^/]+)(?:/(.*))?", path)
                    if (
                        alias_match
                        and _ALIAS_RE.fullmatch(alias_match.group(1))
                        and _ALIAS_RE.fullmatch(alias_match.group(2))
                    ):
                        registration = state.get_artifact_registration_by_alias(
                            alias_match.group(1), alias_match.group(2)
                        )
                        raw_relative = alias_match.group(3) or ""
                    else:
                        try:
                            mounted_path = _decode_relative_path(path)
                        except ArtifactValidationError:
                            mounted_path = ""
                        mounted = (
                            state.find_artifact_registration_by_mount(mounted_path)
                            if mounted_path
                            else None
                        )
                        if mounted is not None:
                            registration, raw_relative = mounted
                if registration is None:
                    self._error(HTTPStatus.NOT_FOUND, b"Artifact not found")
                    return
                try:
                    target = resolve_served_file(registration, raw_relative)
                    size = target.stat().st_size
                    content_type, encoding = mimetypes.guess_type(target.name)
                    if encoding:
                        content_type = "application/octet-stream"
                    content_type = content_type or "application/octet-stream"
                    if content_type.startswith("text/") or content_type in {
                        "application/javascript",
                        "application/json",
                    }:
                        content_type += "; charset=utf-8"
                    self.send_response(HTTPStatus.OK)
                    self._headers(size, content_type)
                    self.end_headers()
                    if self.command != "HEAD":
                        with target.open("rb") as handle:
                            while chunk := handle.read(64 * 1024):
                                self.wfile.write(chunk)
                except (ArtifactValidationError, FileNotFoundError, OSError):
                    self._error(HTTPStatus.NOT_FOUND, b"Artifact file not found")

            def do_GET(self) -> None:  # noqa: N802
                self._serve()

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve()

            def do_POST(self) -> None:  # noqa: N802
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, b"Method not allowed")

            def do_PUT(self) -> None:  # noqa: N802
                self.do_POST()

            def do_PATCH(self) -> None:  # noqa: N802
                self.do_POST()

            def do_DELETE(self) -> None:  # noqa: N802
                self.do_POST()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.do_POST()

        server = _ArtifactHTTPServer(("127.0.0.1", self.requested_port), Handler)
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="sshler-artifacts",
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self.port = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
