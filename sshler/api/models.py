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
    terminal_theme: str | None = None


class APIDirectoryEntry(BaseModel):
    name: str
    path: str
    is_directory: bool
    size: int | None = None
    modified: float | None = None
    mode: int | None = None


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


class APIChmodRequest(BaseModel):
    path: str
    mode: str  # octal string like "755"


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


class APIRefreshResult(BaseModel):
    """Result of refreshing a box's connection overrides."""

    name: str
    refreshed: bool


class APIGitInfo(BaseModel):
    """Git repository info for a directory."""

    branch: str | None = None
    is_repo: bool = False
    dirty: bool = False
    error: str | None = None


class APISnippet(BaseModel):
    """A saved command snippet."""

    id: str
    box: str
    label: str
    command: str
    category: str = ""
    sort_order: int = 0
    created_at: float = 0


class APISnippetCreate(BaseModel):
    """Request body for creating a snippet."""

    box: str
    label: str
    command: str
    category: str = ""


class APISnippetUpdate(BaseModel):
    """Request body for updating a snippet."""

    label: str | None = None
    command: str | None = None
    category: str | None = None
    sort_order: int | None = None


class APITunnelCreate(BaseModel):
    """Request body for creating an SSH tunnel."""

    tunnel_type: str  # "local" or "remote"
    local_host: str = "127.0.0.1"
    local_port: int
    remote_host: str = "127.0.0.1"
    remote_port: int


class APITunnel(BaseModel):
    """An active SSH tunnel."""

    id: str
    box: str
    tunnel_type: str
    local_host: str
    local_port: int
    remote_host: str
    remote_port: int
    created_at: float
