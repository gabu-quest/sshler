# Claude Context: sshler

**Version:** 0.4.0 | **Type:** Full-Stack Web Application (FastAPI + Vue 3)

---

## What This Is

**sshler** is a local-only web UI for browsing remote files over SFTP and accessing tmux sessions in the browser. No remote installation required.

**Key characteristics:**
- Single-user localhost tool (not a multi-tenant service)
- Dual UI: Legacy HTMX at `/`, Vue 3 SPA at `/app`
- Real-time terminal via WebSocket + xterm.js
- Security-first: CSRF tokens, origin validation, session auth

---

## Repository Structure

```
/
├── sshler/                 ← Python backend (FastAPI)
│   ├── webapp.py           ← Main app, routes, WebSocket handler (~3k lines)
│   ├── api/                ← API v1 endpoints (modular)
│   ├── cli.py              ← CLI entry point
│   ├── config.py           ← Config loading (boxes.yaml + SSH config)
│   ├── ssh.py              ← SSH/SFTP operations (asyncssh)
│   ├── ssh_pool.py         ← Connection pooling
│   ├── state.py            ← SQLite state (sessions, favorites)
│   ├── session.py          ← Session auth store
│   ├── auth.py             ← Auth middleware, rate limiting
│   ├── validation.py       ← Path validation, security
│   ├── templates/          ← Jinja2 templates (legacy HTMX UI)
│   └── static/             ← Static assets + Vue dist
├── frontend/               ← Vue 3 SPA
│   ├── src/
│   │   ├── views/          ← Page components (FilesView, TerminalView, etc.)
│   │   ├── components/     ← Reusable components
│   │   ├── stores/         ← Pinia stores
│   │   ├── api/            ← API client
│   │   └── router/         ← Vue Router config
│   └── package.json
├── tests/                  ← pytest tests
│   ├── e2e/                ← Playwright E2E tests
│   └── test_*.py           ← Unit/integration tests
└── pyproject.toml          ← Project config (uv/pip)
```

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | FastAPI + uvicorn | Python 3.12+, async |
| SSH | asyncssh | SFTP + remote tmux |
| State | SQLite (sqler) | Sessions, favorites |
| Frontend | Vue 3 + Pinia | Composition API, `<script setup>` |
| Terminal | xterm.js | WebSocket binary protocol |
| Testing | pytest, Vitest, Playwright | Full coverage |
| Package | uv | PEP 621 pyproject.toml |

---

## Key Patterns

### Backend

**webapp.py is the monolith** — Routes, WebSocket handler, middleware all here. API endpoints are modular in `sshler/api/`.

**WebSocket terminal flow:**
1. Client calls `/api/v1/terminal/handshake` for connection info
2. Client connects to `/ws/term?host=...&dir=...&session=...&token=...`
3. Server opens tmux via SSH (remote) or subprocess (local)
4. Binary data flows bidirectionally

**Security layers:**
- `X-SSHLER-TOKEN` header or `token` query param for auth
- Origin header validation (CSRF protection)
- Path traversal prevention in file operations
- Rate limiting on auth endpoints

**Local vs Remote boxes:**
- `box.name == "local"` → subprocess tmux, direct filesystem
- Otherwise → asyncssh connection, SFTP operations

### Frontend

**Pinia stores** manage all state (`stores/`):
- `bootstrap` — Initial config from `/api/v1/bootstrap`
- `boxes` — Available SSH boxes
- `files` — File browser state
- `favorites` — Pinned directories

**API client** in `src/api/http.ts` handles auth headers automatically.

**Terminal component** wraps xterm.js with WebSocket management.

**Directory search** uses frecency-based ranking:
- Local box: queries zoxide directly for instant results
- Remote boxes: SQLite frecency table + SSH `find` for discovery
- Formula: `score = visit_count * exp(-0.1 * days_since_last_visit)`

---

## Project CLI (just)

Run `just` with no args to see all recipes.

```bash
just test              # All tests (backend + frontend)
just test-backend      # pytest only
just test-frontend     # Vitest only
just test-e2e          # Playwright E2E
just test-mobile       # Mobile responsive E2E
just build             # Build frontend
just typecheck         # Type check everything
just dev               # Start dev server (backend + Vite HMR)
just ci                # Full CI: build + test + typecheck
just install           # Install all dependencies
```

---

## Testing

### Running Tests

```bash
# Via just (preferred)
just test              # Everything
just test-backend      # Backend only
just test-frontend     # Frontend only

# Or directly
uv run pytest                    # All backend tests
uv run pytest tests/test_*.py    # Unit/integration only
uv run pytest tests/e2e/         # Playwright E2E
pnpm --prefix frontend test -- --run  # Frontend Vitest
```

### Test Coverage

| Area | Tests | Notes |
|------|-------|-------|
| WebSocket | test_websocket.py, test_httpx_ws.py | Connection lifecycle |
| API | test_api_v1.py | REST endpoints |
| Security | test_command_injection.py, test_path_validation.py | Input sanitization |
| Auth | test_session_auth.py, test_rate_limit.py | Session + rate limiting |
| Search | test_search.py | Frecency tracking + zoxide |
| E2E | tests/e2e/ | Playwright browser tests |
| Frontend | src/**/*.spec.ts | Vitest component tests |

### E2E Setup

```bash
uv run playwright install chromium
uv run pytest tests/e2e/
```

---

## Security Considerations

**MUST validate:**
- All file paths (symlink escape, traversal)
- Session names (shell injection prevention)
- Origin headers on state-changing requests
- Token presence on all authenticated endpoints

**MUST NOT:**
- Execute user-uploaded content
- Store secrets in tracked files
- Disable auth in production configs
- Trust client-provided paths without normalization

**Auth flow:**
- Session cookies (httpOnly, Secure in production)
- CSRF via origin validation
- Optional basic auth for exposed deployments

---

## Development Workflow

### Backend

```bash
# Run dev server with auto-reload
uv run sshler serve --log-level debug

# Or with explicit settings
SSHLER_HOST=127.0.0.1 SSHLER_PORT=8822 uv run sshler serve
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev              # Vite dev server (proxies to backend)
pnpm build            # Build to sshler/static/dist
pnpm test -- --run    # Run tests
```

### Full Stack Dev

```bash
# RECOMMENDED: Single command that starts both servers
uv run sshler serve --dev

# This starts:
# - FastAPI backend at http://localhost:8822
# - Vite dev server at http://localhost:5173
# - Opens browser to Vite URL automatically
```

Access: `http://localhost:5173/app/` (Vite with HMR) or `http://localhost:8822/app/` (built)

**IMPORTANT:** The `--dev` flag is REQUIRED when using the Vite dev server. It:
1. Starts both FastAPI and Vite dev servers together
2. Adds `http://localhost:5173` to allowed origins
3. Enables auto-reload for backend changes

Without `--dev`, POST requests from Vite will fail with 403 Forbidden.

---

## Common Tasks

### Adding an API Endpoint

1. Add route in `sshler/api/<module>.py` or `sshler/webapp.py`
2. Add tests in `tests/test_api_v1.py`
3. Update frontend API client if needed

### Adding a Vue Component

1. Create in `frontend/src/components/`
2. Add tests in `*.spec.ts` alongside
3. Use Composition API + `<script setup>`

### Modifying WebSocket Protocol

1. Update `webapp.py` WebSocket handler
2. Update `TerminalView.vue` client code
3. Add/update tests in `test_websocket.py` and `test_terminal_websocket.py`

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SSHLER_HOST` | 127.0.0.1 | Bind address |
| `SSHLER_PORT` | 8822 | Port |
| `SSHLER_CONFIG_DIR` | Platform default | Config location |
| `SSHLER_PUBLIC_URL` | - | For origin validation |
| `SSHLER_COOKIE_SECURE` | true | Secure cookie flag |

---

## ADRs (Architectural Decisions)

### ADR-001: Cookie Sessions over JWTs
Sessions are revocable, simpler, and correct for single-backend browser apps. JWTs solve distributed auth problems we don't have.

### ADR-002: Dual UI Strategy
Legacy HTMX UI at `/` for stability while Vue SPA at `/app` reaches feature parity. Both share the same API.

### ADR-003: Local Box Special Case
`box.name == "local"` triggers subprocess-based tmux instead of SSH. Enables local filesystem browsing without SSH.

---

## Known Gotchas

### Origin Validation (403 on POST)
The backend validates the `Origin` header on all state-changing requests. If you get 403 errors on POST/PUT/DELETE:
1. Check you're running with `--dev` flag when using Vite dev server
2. Check `SSHLER_PUBLIC_URL` is set correctly if behind a proxy

### Favorites Persistence
Favorites are stored in both:
- SQLite state database (via `state.replace_favorites_async`)
- YAML config (via `save_config`)

The `refresh_box` endpoint resets connection overrides but should NOT touch favorites.

### Asset Paths in Frontend
Use **relative paths** in `index.html` and `manifest.webmanifest` (e.g., `favicon.png` not `/app/favicon.png`). Vite handles base path resolution during build. Absolute paths break the dev server.

---

## Mobile Terminal UX

### MobileInputBar Component

Located at `frontend/src/components/MobileInputBar.vue`. Provides quick-access buttons for keys that are hard to type on mobile keyboards.

**Quick Keys (Phosphor Icons):**

| Icon | Key | Color | Purpose |
|------|-----|-------|---------|
| PhCaretUp/Down/Left/Right | Arrow keys | neutral | Menu navigation |
| PhKeyReturn | Enter | blue | Confirm/submit |
| PhArrowElbowDownRight | Tab | purple | Autocomplete/next |
| PhStopCircle | Escape | yellow | Stop/cancel (interrupt Claude) |
| PhHandPalm | Ctrl+C | red | Kill process (danger) |
| PhScroll | Ctrl+B [ | orange | Enter tmux copy mode |
| PhArrowFatLinesUp/Down | PgUp/PgDn | orange | Scroll in copy mode |
| PhSignOut | Ctrl+D | teal | Exit/EOF |
| PhQuestion | ? | blue | Show help legend |

**Help Legend Modal:**
- Tap `?` button to show all icons with descriptions
- Color-coded icons match button colors
- Tap outside to dismiss

### Mobile Header (AppHeader.vue)

Ultra-thin 14px header for maximum terminal space:
- Logo (10px) on left
- CPU/MEM stats on right (9px mono font)
- Stats color: green (<75%), orange (75-89%), red (90%+)
- No theme toggle or menu buttons on mobile

### Key Files

- `frontend/src/components/MobileInputBar.vue` — Quick keys + help legend
- `frontend/src/components/Terminal.vue` — xterm.js wrapper with mobile viewport handling
- `frontend/src/components/AppHeader.vue` — Responsive header with mobile stats
- `frontend/src/composables/useResponsive.ts` — Mobile detection hooks

---

## Before Committing

- [ ] Tests pass (`uv run pytest && npm --prefix frontend test -- --run`)
- [ ] Type checks pass (`uv run mypy sshler/`)
- [ ] No security regressions (path validation, auth)
- [ ] Commit message is imperative and specific
