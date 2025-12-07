# SSHler Backend Improvement Plan

> **Note:** Frontend modernization (PWA, dark/light theme, command palette, keyboard shortcuts, touch gestures, file preview enhancements, multi-pane terminals, session persistence) has been **completed** as of 2025. See [MODERNIZATION_TODO.md](MODERNIZATION_TODO.md) for details.
>
> This document focuses on **backend** improvements (architecture, performance, security).

## Executive Summary

This document outlines a comprehensive refactoring and improvement plan for the SSHler application, addressing backend architecture, DevX, performance, and security issues identified through codebase analysis.

## Current State Analysis

### Critical Issues
1. **No Session Tracking**: Tmux sessions created but never tracked or persisted
2. **Monolithic Architecture**: `webapp.py` is 2,115 lines - needs modular structure
3. **Performance Bottlenecks**: Config reloaded on every request, no connection pooling
4. **Security Gaps**: Path traversal risks, token exposure, missing validation
5. **Poor Error Handling**: Bare exceptions, swallowed errors, generic messages
6. **Code Duplication**: SSH/SFTP connection pattern repeated 8+ times
7. **UX Issues**: Tmux scroll mode lacks feedback, no multi-session support
8. **Resource Inefficiency**: Window polling every 2s, no WebSocket reconnection

---

## Phase 1: Foundation & Infrastructure (High Priority)

### 1.1 Database Schema for Session Tracking

**Goal**: Persistent tracking of tmux sessions with full metadata

**Implementation**:
```python
# New table: sessions
class Session(SQLerModel):
    __tablename__ = "sessions"

    id: str                    # UUID
    box: str                   # Box name
    session_name: str          # Tmux session name
    working_directory: str     # Initial directory
    created_at: float          # Unix timestamp
    last_accessed_at: float    # Updated on attach/detach
    active: bool               # Is session currently attached?
    window_count: int          # Number of tmux windows
    metadata: str              # JSON: terminal size, colors, etc.
```

**Indexes**:
- `(box, last_accessed_at)` - for recent sessions per box
- `(box, active)` - for filtering active sessions
- `(created_at)` - for cleanup queries

**Migration Strategy**:
1. Add new table with automatic migration
2. Backfill existing tmux sessions (best-effort scan)
3. Add session tracking to WebSocket lifecycle

### 1.2 Modular Architecture Refactoring

**Goal**: Break `webapp.py` into logical modules for maintainability

**New Structure**:
```
sshler/
├── __init__.py
├── webapp.py              # App factory only (100 lines)
├── settings.py            # Settings and configuration
├── models/
│   ├── __init__.py
│   ├── box.py            # Box, StoredBox dataclasses
│   ├── session.py        # Session model and types
│   └── file.py           # FileEntry, FileMetadata
├── services/
│   ├── __init__.py
│   ├── ssh_manager.py    # Connection pooling
│   ├── session_tracker.py # Session CRUD operations
│   ├── file_operations.py # Unified file ops (local/remote)
│   └── config_cache.py   # Cached config loading
├── routes/
│   ├── __init__.py
│   ├── boxes.py          # Box management routes
│   ├── files.py          # File operations routes
│   ├── terminal.py       # Terminal and WebSocket routes
│   └── sessions.py       # NEW: Session management API
├── middleware/
│   ├── __init__.py
│   ├── security.py       # CSRF, auth, path validation
│   └── logging.py        # Request tracing and structured logs
└── utils/
    ├── __init__.py
    ├── errors.py         # Custom exceptions and error codes
    └── validation.py     # Input validation helpers
```

**Benefits**:
- **Maintainability**: Each module <400 lines
- **Testability**: Services can be mocked and tested independently
- **Scalability**: Easy to add new features without touching core logic
- **Team Collaboration**: Developers can work on different modules

### 1.3 SSH Connection Pool Manager

**Goal**: Reuse SSH connections instead of creating one per request

**Design**:
```python
class SSHConnectionPool:
    """Manages connection pooling per box with automatic cleanup."""

    def __init__(self, max_connections_per_box: int = 5, idle_timeout: int = 300):
        self._pools: dict[str, asyncio.Queue[SSHConnection]] = {}
        self._idle_timeout = idle_timeout

    async def acquire(self, box: Box) -> SSHConnection:
        """Get or create connection for box."""
        # Check pool, return if available
        # Create new if pool not full
        # Wait if pool at max capacity

    async def release(self, box_name: str, connection: SSHConnection):
        """Return connection to pool or close if unhealthy."""

    async def invalidate(self, box_name: str):
        """Close all connections for a box (after config change)."""

    @contextmanager
    async def connection(self, box: Box) -> AsyncIterator[SSHConnection]:
        """Context manager for automatic acquire/release."""
        conn = await self.acquire(box)
        try:
            yield conn
        finally:
            await self.release(box.name, conn)
```

**Usage**:
```python
# Before (creates new connection):
connection = await connect(box.connect_host, box.user, ...)
try:
    sftp = await connection.start_sftp_client()
    # ... operations ...
finally:
    await connection.close()

# After (reuses from pool):
async with ssh_pool.connection(box) as conn:
    async with conn.sftp() as sftp:
        # ... operations ...
```

**Benefits**:
- **Performance**: 10-100x faster for repeated operations
- **Resource Efficiency**: Limits concurrent connections
- **Reliability**: Automatic health checks and reconnection

### 1.4 Config Caching

**Goal**: Cache parsed configuration to avoid reloading on every request

**Implementation**:
```python
class ConfigCache:
    """Thread-safe configuration cache with TTL."""

    def __init__(self, ttl: int = 60, watch_files: bool = True):
        self._cache: AppConfig | None = None
        self._lock = asyncio.Lock()
        self._cached_at: float | None = None
        self._ttl = ttl
        self._watch_task: asyncio.Task | None = None

    async def get(self, ssh_config_path: str | None = None) -> AppConfig:
        """Get cached config or reload if expired/invalidated."""
        async with self._lock:
            now = time.time()
            if self._cache and self._cached_at:
                if now - self._cached_at < self._ttl:
                    return self._cache

            # Reload config
            self._cache = load_config(ssh_config_path)
            self._cached_at = now
            return self._cache

    def invalidate(self):
        """Force cache refresh on next request."""
        self._cache = None

    async def _watch_config_files(self):
        """Monitor boxes.yaml and SSH config for changes."""
        # Use watchfiles library to auto-invalidate on change
```

**Integration**:
```python
# In webapp.py
config_cache = ConfigCache(ttl=60, watch_files=True)

async def _get_application_config() -> AppConfig:
    return await config_cache.get(settings.ssh_config_path)
```

**Benefits**:
- **Performance**: Eliminates 100+ file reads per second under load
- **Auto-Refresh**: Picks up config changes without restart (via file watching)
- **Memory Efficient**: Single shared config instance

---

## Phase 2: Security & Validation (High Priority)

### 2.1 Path Traversal Protection

**Current Risk**:
```python
# Vulnerable: directory parameter not validated
directory = request.query_params.get("directory", "/")
await sftp_list_directory(connection, directory)
```

**Solution**:
```python
class PathValidator:
    """Validate and sanitize filesystem paths."""

    @staticmethod
    def validate_remote_path(path: str, allow_absolute: bool = True) -> str:
        """Validate remote path and prevent traversal attacks."""
        # Normalize path (resolve .., ., etc.)
        normalized = posixpath.normpath(path)

        # Check for traversal attempts
        if not allow_absolute and normalized.startswith("/"):
            raise ValueError("Absolute paths not allowed")

        # Block dangerous patterns
        if any(p in normalized for p in ["/../", "/..", "../"]):
            raise ValueError("Path traversal detected")

        return normalized

    @staticmethod
    def validate_filename(filename: str) -> str:
        """Validate filename component (no slashes, no null bytes)."""
        if not filename or filename in {".", ".."}:
            raise ValueError("Invalid filename")

        if "/" in filename or "\0" in filename:
            raise ValueError("Filename cannot contain path separators")

        return filename
```

**Apply Everywhere**:
- File listing endpoints
- File upload/download
- File edit/create
- SFTP operations

### 2.2 Enhanced CSRF Protection

**Current Issue**: Token printed to console and stored in window object

**Improvement**:
```python
# Generate token per-session instead of static
class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionData] = {}

    def create_session(self) -> tuple[str, str]:
        """Create session ID and CSRF token."""
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._sessions[session_id] = SessionData(
            csrf_token=csrf_token,
            created_at=time.time(),
        )
        return session_id, csrf_token

    def validate_csrf(self, session_id: str, token: str) -> bool:
        """Validate CSRF token for session."""
        session = self._sessions.get(session_id)
        return session and session.csrf_token == token
```

**Benefits**:
- No static token exposure
- Per-session tokens reduce attack surface
- Automatic cleanup of expired sessions

### 2.3 Rate Limiting

**Implementation**:
```python
class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: int, per: int):
        self._rate = rate
        self._per = per
        self._buckets: dict[str, TokenBucket] = {}

    async def check(self, key: str) -> bool:
        """Check if request is allowed."""
        bucket = self._buckets.setdefault(key, TokenBucket(self._rate, self._per))
        return bucket.consume()

# Apply to endpoints:
# - File upload: 10 files per minute
# - WebSocket connections: 5 per minute per IP
# - File operations: 100 per minute per box
```

---

## Phase 3: UX Improvements (Medium Priority)

### 3.1 Enhanced Tmux Scroll Mode

**Current Issues**:
- No visual indicator when in scroll mode
- Hardcoded Ctrl+B prefix
- No help text for navigation
- No mouse wheel support detection

**Improvements**:

#### 3.1.1 Visual Scroll Mode Indicator
```javascript
// In term.js
let scrollModeActive = false;

terminal.onData((data) => {
    // Detect tmux copy mode status via escape sequences
    if (data.includes('[copy-mode]')) {
        scrollModeActive = true;
        showScrollModeIndicator();
    }
});

function showScrollModeIndicator() {
    const indicator = document.createElement('div');
    indicator.className = 'scroll-mode-indicator';
    indicator.innerHTML = `
        <span class="icon">📜</span>
        <span>SCROLL MODE</span>
        <span class="hint">↑↓ navigate • q quit • / search</span>
    `;
    terminalContainer.appendChild(indicator);
}
```

#### 3.1.2 Smart Scroll Mode Detection
```javascript
// Detect if mouse wheel is supported
if (terminal.hasMouseWheelSupport()) {
    // Use native terminal mouse wheel
    terminal.attachCustomWheelEventHandler((event) => {
        // Send mouse wheel events directly to tmux
    });
} else {
    // Fall back to Ctrl+B [ for scroll mode
    window.addEventListener('wheel', (event) => {
        if (event.target.closest('.terminal')) {
            enterScrollMode();
        }
    });
}
```

#### 3.1.3 Configurable Tmux Prefix
```python
# In settings
class Settings(BaseSettings):
    tmux_prefix_key: str = "C-b"  # Ctrl+B default

    # Allow user override via environment variable
    # SSHLER_TMUX_PREFIX_KEY=C-a for screen-like bindings
```

#### 3.1.4 Scroll Mode Help Modal
```javascript
// Show help on first scroll attempt
function showScrollModeHelp() {
    modal.show({
        title: 'Terminal Scrolling',
        content: `
            <h3>Navigation</h3>
            <ul>
                <li><kbd>↑</kbd><kbd>↓</kbd> - Scroll line by line</li>
                <li><kbd>Page Up</kbd><kbd>Page Down</kbd> - Scroll page by page</li>
                <li><kbd>/</kbd> - Search forward</li>
                <li><kbd>?</kbd> - Search backward</li>
                <li><kbd>q</kbd> - Exit scroll mode</li>
            </ul>

            <h3>Pro Tips</h3>
            <ul>
                <li>Use <kbd>g</kbd> to jump to top, <kbd>G</kbd> for bottom</li>
                <li>Prefix with numbers: <kbd>10↓</kbd> scrolls 10 lines</li>
            </ul>
        `,
        buttons: [
            { text: "Don't show again", action: () => localStorage.setItem('hide_scroll_help', 'true') },
            { text: 'Got it', primary: true },
        ]
    });
}
```

### 3.2 Multi-Session Support (Split Pane UI)

**Goal**: Display multiple tmux sessions side-by-side or stacked

**Design**:

#### 3.2.1 Session Manager UI Component
```html
<!-- New session manager panel -->
<div class="session-manager">
    <div class="session-list">
        <h3>Active Sessions</h3>
        <div class="session-item" data-session-id="uuid1">
            <span class="session-name">dev-server:/home/user/project</span>
            <span class="session-status active">●</span>
            <button class="btn-detach">Detach</button>
        </div>
        <div class="session-item" data-session-id="uuid2">
            <span class="session-name">db-server:/var/log</span>
            <span class="session-status active">●</span>
            <button class="btn-detach">Detach</button>
        </div>
    </div>

    <div class="session-actions">
        <button class="btn-new-session">+ New Session</button>
        <button class="btn-split-horizontal">Split Horizontal</button>
        <button class="btn-split-vertical">Split Vertical</button>
    </div>
</div>
```

#### 3.2.2 Split Terminal Layout
```javascript
class TerminalLayoutManager {
    constructor() {
        this.panes = [];
        this.layout = 'single'; // 'single', 'hsplit', 'vsplit', 'grid'
    }

    splitHorizontal(sessionId) {
        // Create new terminal pane below current
        const newPane = this.createTerminalPane(sessionId);
        this.resizeExistingPanes('horizontal');
        this.panes.push(newPane);
    }

    splitVertical(sessionId) {
        // Create new terminal pane to the right
        const newPane = this.createTerminalPane(sessionId);
        this.resizeExistingPanes('vertical');
        this.panes.push(newPane);
    }

    createTerminalPane(sessionId) {
        // Each pane gets its own:
        // - xterm.js instance
        // - WebSocket connection
        // - Resize observer
        // - Focus indicator

        const container = document.createElement('div');
        container.className = 'terminal-pane';

        const term = new Terminal({ /* options */ });
        const ws = new WebSocket(`/ws/term?session=${sessionId}`);

        // Connect terminal to websocket
        this.connectTerminalToWS(term, ws);

        return { container, term, ws, sessionId };
    }

    focusPane(index) {
        // Add focus indicator to selected pane
        this.panes.forEach((pane, i) => {
            pane.container.classList.toggle('focused', i === index);
        });
        this.panes[index].term.focus();
    }
}
```

#### 3.2.3 Keyboard Navigation Between Panes
```javascript
// Vim-style pane navigation
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey) {
        switch (e.key) {
            case 'H': layoutManager.focusPaneLeft(); break;
            case 'J': layoutManager.focusPaneDown(); break;
            case 'K': layoutManager.focusPaneUp(); break;
            case 'L': layoutManager.focusPaneRight(); break;
            case 'W': layoutManager.cycleNextPane(); break;
            case 'X': layoutManager.closeCurrentPane(); break;
        }
    }
});
```

#### 3.2.4 Session Persistence Across Page Reloads
```javascript
// Save layout to localStorage
class SessionLayoutPersistence {
    saveLayout() {
        const layout = {
            panes: layoutManager.panes.map(pane => ({
                sessionId: pane.sessionId,
                position: pane.getPosition(),
                size: pane.getSize(),
            })),
            focusedIndex: layoutManager.focusedIndex,
        };
        localStorage.setItem(`terminal_layout_${boxName}`, JSON.stringify(layout));
    }

    restoreLayout() {
        const saved = localStorage.getItem(`terminal_layout_${boxName}`);
        if (saved) {
            const layout = JSON.parse(saved);
            layout.panes.forEach(paneData => {
                layoutManager.restorePane(paneData);
            });
        }
    }
}
```

### 3.3 Session History & Resume UI

**Goal**: Show recent sessions and allow one-click resume

**Implementation**:

#### 3.3.1 Session History Panel
```html
<!-- In box detail page -->
<div class="session-history">
    <h3>Recent Sessions</h3>
    <div class="session-list">
        {% for session in recent_sessions %}
        <div class="session-card">
            <div class="session-header">
                <span class="session-name">{{ session.session_name }}</span>
                <span class="session-status {{ 'active' if session.active else 'idle' }}">
                    {{ '● Active' if session.active else '○ Idle' }}
                </span>
            </div>
            <div class="session-meta">
                <span class="directory">📁 {{ session.working_directory }}</span>
                <span class="timestamp">🕒 {{ session.last_accessed_at | relative_time }}</span>
                <span class="windows">🪟 {{ session.window_count }} windows</span>
            </div>
            <div class="session-actions">
                <button class="btn-resume" data-session-id="{{ session.id }}">
                    Resume
                </button>
                <button class="btn-delete" data-session-id="{{ session.id }}">
                    Delete
                </button>
            </div>
        </div>
        {% endfor %}
    </div>
</div>
```

#### 3.3.2 Backend API for Session Management
```python
# In routes/sessions.py

@router.get("/box/{name}/sessions")
async def list_sessions(name: str, config: AppConfig = Depends(_get_application_config)):
    """List all sessions for a box."""
    sessions = await session_tracker.list_sessions(box_name=name, include_inactive=True)
    return {
        "sessions": [
            {
                "id": s.id,
                "session_name": s.session_name,
                "working_directory": s.working_directory,
                "created_at": s.created_at,
                "last_accessed_at": s.last_accessed_at,
                "active": s.active,
                "window_count": s.window_count,
            }
            for s in sessions
        ]
    }

@router.post("/box/{name}/sessions/{session_id}/resume")
async def resume_session(name: str, session_id: str):
    """Generate terminal URL for resuming a session."""
    session = await session_tracker.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    # Return terminal URL with session parameter
    return {
        "terminal_url": f"/term?box={name}&session={session.session_name}&directory={session.working_directory}"
    }

@router.delete("/box/{name}/sessions/{session_id}")
async def delete_session(name: str, session_id: str):
    """Delete a tmux session and its tracking record."""
    session = await session_tracker.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    # Kill tmux session on remote server
    box = next((b for b in config.all_boxes if b.name == name), None)
    async with ssh_pool.connection(box) as conn:
        await conn.run(f"tmux kill-session -t {session.session_name}", check=False)

    # Remove from database
    await session_tracker.delete_session(session_id)

    return {"status": "deleted"}
```

---

## Phase 4: Performance Optimization (Medium Priority)

### 4.1 WebSocket Improvements

#### 4.1.1 Resize Throttling
```javascript
// Current: resize sends message on every pixel change
// New: debounced resize with max 100ms delay

let resizeTimeout = null;
let lastResize = 0;

function sendResize() {
    const now = Date.now();
    const minInterval = 100; // 100ms minimum between resizes

    clearTimeout(resizeTimeout);

    const elapsed = now - lastResize;
    if (elapsed >= minInterval) {
        performResize();
        lastResize = now;
    } else {
        resizeTimeout = setTimeout(performResize, minInterval - elapsed);
    }
}

function performResize() {
    fitAddon.fit();
    ws.send(JSON.stringify({ op: "resize", cols: term.cols, rows: term.rows }));
}
```

#### 4.1.2 Adaptive Window Polling
```python
# Current: polls every 2 seconds unconditionally
# New: adaptive polling based on activity

class AdaptivePoller:
    def __init__(self):
        self.interval = 2.0  # Start at 2s
        self.min_interval = 1.0
        self.max_interval = 10.0
        self.last_change = time.time()

    def adjust_interval(self, changed: bool):
        """Adjust polling frequency based on window changes."""
        if changed:
            # Windows changed, poll more frequently
            self.interval = max(self.min_interval, self.interval * 0.8)
            self.last_change = time.time()
        else:
            # No changes, slow down polling
            idle_time = time.time() - self.last_change
            if idle_time > 30:
                self.interval = min(self.max_interval, self.interval * 1.2)

# In WebSocket handler:
poller = AdaptivePoller()
while True:
    windows = await get_tmux_windows()
    changed = windows != last_windows
    poller.adjust_interval(changed)
    await asyncio.sleep(poller.interval)
```

#### 4.1.3 WebSocket Reconnection
```javascript
class ResilientWebSocket {
    constructor(url, options = {}) {
        this.url = url;
        this.maxRetries = options.maxRetries || 5;
        this.retryDelay = options.retryDelay || 1000;
        this.retries = 0;
        this.ws = null;
        this.connect();
    }

    connect() {
        this.ws = new WebSocket(this.url);

        this.ws.onclose = (event) => {
            if (this.retries < this.maxRetries && !event.wasClean) {
                // Exponential backoff
                const delay = this.retryDelay * Math.pow(2, this.retries);
                setTimeout(() => {
                    this.retries++;
                    this.connect();
                }, delay);

                this.showReconnecting(delay);
            } else {
                this.showDisconnected();
            }
        };

        this.ws.onopen = () => {
            this.retries = 0;
            this.hideReconnecting();
        };
    }

    showReconnecting(delay) {
        // Show banner: "Connection lost. Reconnecting in 2s..."
    }
}
```

### 4.2 SFTP Operation Batching

**Goal**: Batch multiple file operations into single SFTP session

```python
class SFTPBatchOperation:
    """Context manager for batching SFTP operations."""

    def __init__(self, connection: asyncssh.SSHClientConnection):
        self.connection = connection
        self.sftp: SFTPClient | None = None
        self.operations: list[Callable] = []

    async def __aenter__(self) -> "SFTPBatchOperation":
        self.sftp = await self.connection.start_sftp_client()
        return self

    async def __aexit__(self, *args):
        try:
            await self.sftp.exit()
        except Exception:
            pass

    async def list_directory(self, path: str) -> list[SFTPAttrs]:
        """Queue directory listing."""
        return await self.sftp.readdir(path)

    async def stat(self, path: str) -> SFTPAttrs:
        """Queue stat operation."""
        return await self.sftp.stat(path)

    async def batch_stats(self, paths: list[str]) -> list[SFTPAttrs]:
        """Batch multiple stat calls."""
        return await asyncio.gather(*[self.stat(p) for p in paths])

# Usage:
async with ssh_pool.connection(box) as conn:
    async with SFTPBatchOperation(conn) as sftp:
        # All operations share single SFTP session
        files = await sftp.list_directory("/home/user")
        stats = await sftp.batch_stats([f.filename for f in files])
```

### 4.3 Response Caching

**Goal**: Cache expensive operations (directory listings, file stats)

```python
from functools import lru_cache
import hashlib

class ResponseCache:
    """Cache HTTP responses with TTL."""

    def __init__(self, ttl: int = 60):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl

    def key(self, *args) -> str:
        """Generate cache key from arguments."""
        data = "|".join(str(a) for a in args)
        return hashlib.md5(data.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired."""
        if key in self._cache:
            value, cached_at = self._cache[key]
            if time.time() - cached_at < self._ttl:
                return value
        return None

    def set(self, key: str, value: Any):
        """Cache value with current timestamp."""
        self._cache[key] = (value, time.time())

    def invalidate(self, pattern: str):
        """Invalidate all keys matching pattern."""
        keys_to_delete = [k for k in self._cache if pattern in k]
        for k in keys_to_delete:
            del self._cache[k]

# Apply to directory listing:
cache = ResponseCache(ttl=30)

@router.get("/box/{name}/ls")
async def list_directory(name: str, directory: str):
    cache_key = cache.key(name, directory)
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Fetch from server
    result = await fetch_directory_listing(name, directory)
    cache.set(cache_key, result)
    return result
```

---

## Phase 5: Error Handling & Observability (Low Priority)

### 5.1 Structured Error Handling

**Goal**: Replace bare exceptions with typed errors and codes

```python
# In utils/errors.py

class SSHlerError(Exception):
    """Base exception with error code and user message."""

    code: str
    message: str
    details: dict[str, Any]

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

class ConnectionError(SSHlerError):
    code = "CONNECTION_FAILED"

class AuthenticationError(SSHlerError):
    code = "AUTH_FAILED"

class PathTraversalError(SSHlerError):
    code = "INVALID_PATH"

class FileNotFoundError(SSHlerError):
    code = "FILE_NOT_FOUND"

# Exception handler:
@app.exception_handler(SSHlerError)
async def sshler_error_handler(request: Request, exc: SSHlerError):
    return JSONResponse(
        status_code=400,
        content=exc.to_dict(),
    )
```

### 5.2 Structured Logging

**Goal**: Add request tracing and structured logs

```python
import structlog

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger()

# Middleware for request logging:
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    request_id = secrets.token_hex(8)
    request.state.request_id = request_id

    logger.info(
        "request_started",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client=request.client.host,
    )

    start = time.time()
    try:
        response = await call_next(request)
        duration = time.time() - start

        logger.info(
            "request_completed",
            request_id=request_id,
            status_code=response.status_code,
            duration_ms=int(duration * 1000),
        )

        return response
    except Exception as exc:
        duration = time.time() - start
        logger.error(
            "request_failed",
            request_id=request_id,
            error=str(exc),
            duration_ms=int(duration * 1000),
        )
        raise
```

### 5.3 Health Check and Metrics

**Goal**: Expose metrics for monitoring

```python
from prometheus_client import Counter, Histogram, generate_latest

# Metrics
request_count = Counter('sshler_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
request_duration = Histogram('sshler_request_duration_seconds', 'Request duration', ['endpoint'])
ssh_connection_count = Counter('sshler_ssh_connections_total', 'SSH connections', ['box', 'status'])
active_websockets = Gauge('sshler_active_websockets', 'Active WebSocket connections')

@router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type="text/plain")

@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "uptime": time.time() - app.state.start_time,
        "connections": {
            "ssh_pool_size": ssh_pool.size(),
            "active_websockets": active_websockets._value.get(),
        }
    }
```

---

## Phase 6: Developer Experience (Low Priority)

### 6.1 Type Hints and Validation

**Goal**: Add Pydantic models for all API requests/responses

```python
from pydantic import BaseModel, Field, validator

class CreateBoxRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1)
    user: str = Field(..., min_length=1)
    port: int = Field(22, ge=1, le=65535)
    keyfile: str | None = None

    @validator('name')
    def validate_name(cls, v):
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError("Name must contain only alphanumeric, dash, underscore")
        return v

class FileListResponse(BaseModel):
    entries: list[FileEntry]
    total: int
    directory: str

class ErrorResponse(BaseModel):
    error: ErrorDetail

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
```

### 6.2 API Documentation

**Goal**: Auto-generate OpenAPI docs

```python
# FastAPI automatically generates docs
# Enhance with descriptions:

@router.get(
    "/box/{name}/ls",
    response_model=FileListResponse,
    summary="List directory contents",
    description="""
    List files and directories in the specified path.

    Supports both local and remote boxes.
    Results are cached for 30 seconds.
    """,
    responses={
        200: {"description": "Directory listing retrieved successfully"},
        404: {"description": "Box or directory not found"},
        403: {"description": "Permission denied"},
    }
)
async def list_directory(
    name: str = Path(..., description="Box name"),
    directory: str = Query("/", description="Directory path to list"),
):
    ...
```

### 6.3 Testing Infrastructure

**Goal**: Add comprehensive test suite

```python
# tests/test_session_tracker.py

import pytest
from sshler.services.session_tracker import SessionTracker

@pytest.fixture
async def session_tracker():
    tracker = SessionTracker(":memory:")  # In-memory DB for tests
    await tracker.initialize()
    yield tracker
    await tracker.cleanup()

@pytest.mark.asyncio
async def test_create_session(session_tracker):
    session = await session_tracker.create_session(
        box="test-box",
        session_name="test-session",
        working_directory="/home/user",
    )

    assert session.box == "test-box"
    assert session.active is True
    assert session.window_count == 1

@pytest.mark.asyncio
async def test_list_recent_sessions(session_tracker):
    # Create multiple sessions
    await session_tracker.create_session("box1", "session1", "/home")
    await session_tracker.create_session("box1", "session2", "/var")

    recent = await session_tracker.list_recent_sessions("box1", limit=10)
    assert len(recent) == 2
    assert recent[0].last_accessed_at > recent[1].last_accessed_at  # Sorted

# tests/test_ssh_pool.py

@pytest.mark.asyncio
async def test_connection_reuse(ssh_pool, test_box):
    # First acquire
    async with ssh_pool.connection(test_box) as conn1:
        connection_id_1 = id(conn1)

    # Second acquire should reuse
    async with ssh_pool.connection(test_box) as conn2:
        connection_id_2 = id(conn2)

    assert connection_id_1 == connection_id_2
```

---

## Implementation Priority

### Week 1: Foundation
1. ✅ Session tracking database schema
2. ✅ Refactor webapp.py into modules
3. ✅ SSH connection pool manager
4. ✅ Config caching

### Week 2: Security
5. ✅ Path validation
6. ✅ Enhanced CSRF protection
7. ✅ Rate limiting

### Week 3: UX
8. ✅ Tmux scroll mode improvements
9. ✅ Multi-session split pane UI
10. ✅ Session history and resume

### Week 4: Performance
11. ✅ WebSocket optimizations
12. ✅ SFTP batching
13. ✅ Response caching

### Week 5: Polish
14. ✅ Structured error handling
15. ✅ Logging and metrics
16. ✅ Testing infrastructure

---

## Success Metrics

### Performance
- [ ] Config load time: <10ms (from 100ms+)
- [ ] SSH connection time: <50ms (from 500ms+)
- [ ] Directory listing: <100ms (from 300ms+)
- [ ] WebSocket reconnection: <2s

### UX
- [ ] Session resume: 1-click from history
- [ ] Scroll mode: Visual indicator + help
- [ ] Multi-session: 2+ terminals side-by-side
- [ ] Error messages: Actionable and specific

### Code Quality
- [ ] webapp.py: <500 lines (from 2,115)
- [ ] Test coverage: >70%
- [ ] Type hint coverage: >90%
- [ ] No bare except blocks

### Security
- [ ] No path traversal vulnerabilities
- [ ] Per-session CSRF tokens
- [ ] Rate limiting on all endpoints
- [ ] Secrets not in logs

---

## Migration Plan

### Backwards Compatibility
- All existing routes remain functional
- Config file format unchanged
- Existing tmux sessions continue working
- New features opt-in via query parameters

### Rollout Strategy
1. **Phase 1**: Deploy foundation changes (connection pool, caching)
   - No user-facing changes
   - Performance improvements only
2. **Phase 2**: Enable session tracking
   - New session history UI appears
   - Existing workflows unaffected
3. **Phase 3**: Release multi-session support
   - Feature flag: `?multi_session=true`
   - Gradual rollout to users
4. **Phase 4**: Polish and stabilize
   - Address feedback
   - Performance tuning
   - Documentation updates

---

## Conclusion

This plan transforms SSHler from a functional prototype into a production-ready application:

- **UX**: Multi-session support, better scroll mode, session history
- **DevX**: Modular codebase, type safety, comprehensive tests
- **Backend**: Connection pooling, caching, proper error handling, security hardening

Estimated effort: **4-5 weeks** for full implementation.

All changes maintain backwards compatibility while providing clear upgrade path for new features.
