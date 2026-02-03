from __future__ import annotations

from pydantic import BaseModel

from .helpers import MAX_IMAGE_PREVIEW_BYTES


class APIBox(BaseModel):
    """Lightweight box representation for the SPA API."""

    name: str
    host: str
    user: str
    port: int
    transport: str
    pinned: bool = False
    default_dir: str | None = None
    favorites: list[str] = []


class APIDirectoryEntry(BaseModel):
    name: str
    path: str
    is_directory: bool
    size: int | None = None
    modified: float | None = None


class APIDirectoryListing(BaseModel):
    box: str
    directory: str
    entries: list[APIDirectoryEntry]


class APISimpleMessage(BaseModel):
    status: str
    message: str
    path: str | None = None


class APITouchRequest(BaseModel):
    directory: str
    filename: str


class APIDeleteRequest(BaseModel):
    path: str


class APIRenameRequest(BaseModel):
    path: str
    new_name: str


class APIMoveRequest(BaseModel):
    source: str
    destination: str


class APICopyRequest(BaseModel):
    source: str
    destination: str
    new_name: str | None = None


class APIFilePreview(BaseModel):
    box: str
    path: str
    parent: str
    content: str | None = None
    syntax_class: str | None = None
    image_data: str | None = None
    image_mime: str | None = None
    image_too_large: bool = False
    image_limit_kb: int = MAX_IMAGE_PREVIEW_BYTES // 1024
    is_markdown: bool = False


class APISessionInfo(BaseModel):
    sessions: list[str] = []


class APITerminalHandshake(BaseModel):
    ws_url: str
    token_header: str
    token: str | None = None


class APIPinToggle(BaseModel):
    name: str
    pinned: bool


class APIFavoriteToggle(BaseModel):
    path: str
    favorite: bool


class APIBoxStatus(BaseModel):
    name: str
    status: str
    latency_ms: float | None


class APISession(BaseModel):
    id: str
    box: str
    session_name: str
    working_directory: str
    created_at: float
    last_accessed_at: float
    active: bool
    window_count: int
    metadata: dict


class APISessionCreate(BaseModel):
    session_name: str
    working_directory: str
    metadata: dict | None = None


class APISessionUpdate(BaseModel):
    active: bool | None = None
    window_count: int | None = None
    metadata: dict | None = None


class APIBootstrap(BaseModel):
    version: str
    token_header: str
    token: str | None = None
    basic_auth_required: bool
    allow_origins: list[str]
    spa_base: str = "/app/"
    spa_enabled: bool = True


class APIPoolConfig(BaseModel):
    """SSH connection pool configuration."""

    idle_timeout: int | None
    max_lifetime: int | None
    max_connections_per_box: int


class APIPoolConfigUpdate(BaseModel):
    """Update SSH connection pool configuration."""

    idle_timeout: int | None = None
    max_lifetime: int | None = None
    max_connections_per_box: int | None = None


class APIBoxStats(BaseModel):
    """System statistics for a box (CPU, memory, uptime)."""

    name: str
    cpu_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    memory_percent: float | None = None
    uptime_seconds: int | None = None
    error: str | None = None


class APIGitInfo(BaseModel):
    """Git repository info for a directory."""

    branch: str | None = None
    is_repo: bool = False
    dirty: bool = False
    error: str | None = None
