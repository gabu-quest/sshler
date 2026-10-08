# Claude Context: sshler

**Version:** 1.0.1 | **Type:** Full-Stack Web Application (FastAPI + Vue 3)

---

## What This Is

**sshler** is a local-only web UI for browsing remote files over SFTP and accessing tmux sessions in the browser. No remote installation required.

**Key characteristics:**
- Single-user localhost tool (not a multi-tenant service)
- Single UI: Vue 3 SPA at `/app` (root `/` redirects there). The legacy HTMX UI has been removed.
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
│   ├── pdf.py              ← Playwright-backed PDF renderer (optional [pdf] extra)
│   ├── api/progress.py     ← Progress-bars REST + WS broadcaster wiring
│   ├── api/artifacts.py    ← Local HTML catalog REST API
│   ├── artifacts.py        ← Discovery, path containment, loopback sidecar
│   └── static/dist/        ← Built Vue SPA (served at /app)
├── frontend/               ← Vue 3 SPA
│   ├── src/
│   │   ├── views/          ← Page components (FilesView, TerminalView, etc.)
│   │   ├── components/     ← Reusable components
│   │   ├── stores/         ← Pinia stores
│   │   ├── composables/    ← Reusable logic (usePdfExport, usePrintableHtml, ...)
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

FastAPI + uvicorn (Python 3.12+, async) · asyncssh (SFTP + remote tmux) · SQLite via sqler · Vue 3 + Pinia (`<script setup>`) · xterm.js over binary WebSocket · Playwright for optional `[pdf]` extra · pytest / Vitest / Playwright · uv.

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
- `app` — Theme, terminal settings, `activeBox` (tracks current box across views)
- `bootstrap` — Initial config from `/api/v1/bootstrap`
- `boxes` — Available SSH boxes
- `files` — File browser state
- `favorites` — Pinned directories

**API client** in `src/api/http.ts` handles auth headers automatically.

**Terminal component** wraps xterm.js with WebSocket management.

**Emoji favicon system** in `src/utils/emoji-favicon.ts`:
- Two disjoint pools: `BOX_EMOJIS` (vehicles/buildings) and `DIR_EMOJIS` (animals/nature/food)
- `getEmojiForBox()` — deterministic emoji per box name (never overlaps with directory emojis)
- `getEmojiForString()` — deterministic emoji per `box:path` string
- Uses FNV-1a hashing for uniform distribution

**Active box tracking**: `app.activeBox` ref is set by FilesView/TerminalView/MultiTerminalView. AppHeader nav links carry `?box=` context so switching views preserves the current box.

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

Prefer the `just` recipes above. Direct forms:

```bash
uv run pytest                    # All backend tests
uv run pytest tests/test_*.py    # Unit/integration only
uv run playwright install chromium   # once, for E2E
uv run pytest tests/e2e/         # Playwright E2E
pnpm --prefix frontend test -- --run  # Frontend Vitest
```

Key suites: `test_websocket.py`/`test_httpx_ws.py`/`test_terminal_websocket.py` (WS), `test_api_v1.py`, `test_command_injection.py`/`test_path_validation.py` (security), `test_session_auth.py`/`test_rate_limit.py`, `test_search.py`; frontend specs sit beside components as `*.spec.ts`.

---

## Security Considerations

**MUST validate:**
- All file paths (symlink escape, traversal)
- Session names (shell injection prevention)
- Origin headers on state-changing requests
- Token presence on all authenticated endpoints

**MUST NOT:** execute uploaded content, disable auth in configs, or trust client paths without normalization.

**Auth flow:** httpOnly session cookies (Secure in production), CSRF via origin validation, optional basic auth for exposed deployments.

---

## Development Workflow

### Backend

```bash
uv run sshler serve --log-level debug
SSHLER_HOST=127.0.0.1 SSHLER_PORT=8822 uv run sshler serve

cd frontend && pnpm install
pnpm dev              # Vite dev server (proxies to backend)
pnpm build            # Build to sshler/static/dist
pnpm test -- --run

uv run sshler serve --dev   # RECOMMENDED: FastAPI :8822 + Vite :5173 (HMR at /app/)
```

**`--dev` is REQUIRED with the Vite dev server:** it adds `http://localhost:5173` to allowed origins and enables backend auto-reload. Without it, POSTs from Vite fail with 403.

---

## Common Tasks

- **API endpoint:** route in `sshler/api/<module>.py` (or `webapp.py`), tests in `tests/test_api_v1.py`, update the frontend client if needed.
- **Vue component:** `frontend/src/components/`, `*.spec.ts` alongside, `<script setup>`.
- **WebSocket protocol:** `webapp.py` handler + `TerminalView.vue` client + `test_websocket.py`/`test_terminal_websocket.py`.

## Feature pipelines (architecture + invariants)

CLI usage of ping, progress, artifacts, diff notebook and markdown preview is in the global `sshler*` skills and in `docs/skills/<feature>/SKILL.md`; this section covers only what you need to change the code.

**Shared broadcaster pattern (progress, ping):** REST route in `sshler/api/<x>.py` → `<X>Broadcaster` in `webapp.py`, instantiated **per-app in `make_app()`** (never module-global, so tests don't share sockets), wired via `APIDependencies.broadcast_<x>` to avoid a `webapp.py` ⇄ `api/` circular import → `/ws/<x>` (auth mirrors `/ws/term`, token query param) → Pinia store with exponential-backoff reconnect. Event payloads must be JSON-serializable (`json.dumps(event)`).

### PDF export
- `sshler/pdf.py` `PDF_RENDERER` launches Chromium in the lifespan; soft-fails to `available=False` without the `[pdf]` extra. `POST /api/v1/pdf/render` `{html, filename}` → PDF, 503 when unavailable.
- `bootstrap.pdfAvailable` gates every UI entry: hide when false, no disabled placeholders.
- HTML pipeline is `composables/usePrintableHtml.ts` (marked → DOMPurify → mermaid → image inlining); new entry points call `usePdfExport().exportOne/exportMany`, never re-roll it.

### Progress bars
- `ProgressBar` model in `state.py`; REST `/api/v1/progress[/:name]`, name `^[A-Za-z0-9._:-]{1,64}$`, status `{running, done, failed, cancelled}`. WS sends `snapshot` on connect, then `upsert`/`delete`.
- CLI token discovery: `--token` → `$SSHLER_TOKEN` → `<config_dir>/runtime-token` (0600, written by `serve()`); URL: `--url` → `$SSHLER_PROGRESS_URL` → `http://127.0.0.1:8822`. That is why `httpx` is a core dep.
- `stores/progress.ts`: subscriptions are client-side and **scoped per box** (`subscriptionsByScope`, key = `appStore.activeBox`; no-ops without a box), persisted at `localStorage["sshler:progress:subscribed"]`.
- WS lifecycle is owned by `App.vue`; `<ProgressStrip>` is `v-if`-gated so it must NOT own it. Bars never auto-dismiss (including `done`); percent is floored.
- New WS event: add to `ProgressEvent` in `api/types.ts`, handle in `_handleEvent`, broadcast from `api/progress.py`.

### Local HTML artifacts (`/app/artifacts`)
- `ArtifactProject`/`ArtifactRegistration` in `state.py`; modes `file`/`site`/`collection`. `ArtifactSidecar` (`sshler/artifacts.py`) binds only `127.0.0.1`, GET/HEAD only, no CORS, strict resolved-path containment, rejects hidden/traversal/symlink paths.
- **Deletion invariant:** removal deletes registrations only; never unlink the source path.
- New discovery rules go in `sshler/artifacts.py` with pure tests plus sidecar containment tests. Design: `ROADMAP-ARTIFACTS.md`.

### Diff notebook (`/app/diff`)
- No dedicated diff backend: each side composes `GET /api/v1/boxes/{name}/git/show`; keep blob-based composition the default.
- Saved notebooks (`DiffNotebook`, `/api/v1/diff/notebooks[/:id]`) are **immutable**: every POST is a new id, no PUT; editing a loaded one forks. URL state is versioned base64 JSON in `?n=`; legacy `?lb=&ld=` URLs must still hydrate.
- A `missing` side (404) is not an error; it renders empty so adds/deletes display.
- New command: parser case in `utils/diffCommandParser.ts` + spec + `DiffView.applyCommand` + `DiffHelpOverlay`. New URL field: bump `NOTEBOOK_VERSION` and write a migration.

### Ping
- `POST /api/v1/ping` (no persistence) → `/ws/ping` (no snapshot) → `stores/ping.ts` `pendingPings` → renderless `PingNotificationHandler.vue` inside `<NNotificationProvider>`. Dismiss: per-ping `duration` → `appStore.pingDefaultDuration` → manual.

### Claude session dashboard (`/app/claude`, local box only, pull-based REST)
- Scanner `sshler/claude_sessions.py` reads `<config-dir>/projects/*/*.jsonl` (`$CLAUDE_CONFIG_DIR`, default `~/.claude`), whole-file streamed, cached per `(path, mtime, size)`; globs guarded with `resolve().is_relative_to(base)`. Title priority mirrors `/resume`: `customTitle` → `aiTitle` → `lastPrompt` → first prompt. Groups by `repo_root` (walk up for `.git`).
- `POST /api/v1/claude/sessions/{id}/open` validates a strict UUID BEFORE any fs/tmux op, then opens window `cl-<6hex>` in the **repo root's** tmux session, but with the window cwd = the exact `info.cwd`, because `claude --resume` is cwd-scoped. Idempotent: an existing window is only `select-window`ed, never re-typed into.
- `ts_session_name(dir)` in `sshler/tmux.py` matches the `ts` CLI byte-for-byte (basename, `.`/`:`→`_`, no hash); frontend `generateSessionName` must match it for `box === "local"`. tmux helpers live in `tmux.py` to avoid circular imports.
- Resume command is a client-side template with `{id}` (global + per-session overrides in localStorage); the server (`_resolve_resume_command`) requires `{id}`, rejects control chars, substitutes the validated UUID.

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

## Mobile terminal UX

`components/MobileInputBar.vue` (quick keys + `?` legend; each key has a fixed colour meaning, red = Ctrl+C), `components/Terminal.vue` (xterm wrapper, mobile viewport), `components/AppHeader.vue` (14px mobile header with CPU/MEM stats), `composables/useResponsive.ts`.

---

## Before Committing

- [ ] Tests pass (`uv run pytest && npm --prefix frontend test -- --run`)
- [ ] Type checks pass (`uv run mypy sshler/`)
- [ ] No security regressions (path validation, auth)
