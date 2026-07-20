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

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | FastAPI + uvicorn | Python 3.12+, async |
| SSH | asyncssh | SFTP + remote tmux |
| State | SQLite (sqler) | Sessions, favorites |
| Frontend | Vue 3 + Pinia | Composition API, `<script setup>` |
| Terminal | xterm.js | WebSocket binary protocol |
| PDF export | Playwright (headless Chromium) | Optional `[pdf]` extra; `POST /api/v1/pdf/render` |
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

### PDF Export Pipeline

PDF export is a server-side conversion of frontend-rendered HTML.

- **Renderer:** `sshler/pdf.py` — `PDF_RENDERER` singleton launches headless Chromium via Playwright in the FastAPI lifespan. Soft-fails to `available=False` if Playwright/Chromium aren't installed (the `[pdf]` extra is optional).
- **Endpoint:** `POST /api/v1/pdf/render` accepts `{html, filename}` and streams `application/pdf`. Returns 503 when unavailable.
- **Bootstrap signal:** `/api/v1/bootstrap` exposes `pdf_available`. The store's `bootstrap.pdfAvailable` gates every UI button — hide entries entirely when `false`, don't show disabled placeholders.
- **Frontend pipeline:** `frontend/src/composables/usePrintableHtml.ts` builds the full HTML document (marked → DOMPurify → mermaid SVG render → image inlining). `usePdfExport.ts` orchestrates fetch → render → POST → blob download with stage-aware spinner + top loading bar.
- **When extending:** if you need a new entry point (e.g., bulk export from search results), import `usePdfExport()` and call `exportOne` / `exportMany`. Don't re-roll the HTML pipeline.

---

### Progress Bars Pipeline

Global progress bars: external scripts push updates via REST, every connected browser tab gets the live event over WebSocket, the dock renders subscribed bars on every page.

- **Storage:** `ProgressBar` SQLerModel in `sshler/state.py` (sync + async CRUD). Lives in the same SQLite DB as sessions/favorites.
- **REST:** `sshler/api/progress.py` — POST/GET/GET-by-name/DELETE at `/api/v1/progress[/:name]`. Name regex `^[A-Za-z0-9._:-]{1,64}$`; status closed-enum `{running, done, failed, cancelled}`. All routes gated by `Depends(deps.require_token)`.
- **Broadcaster:** `ProgressBroadcaster` class in `sshler/webapp.py`. Instantiated **per-app** in `make_app()` (NOT module-global) so tests don't share connection state. Wired into routes via `APIDependencies.broadcast_progress` to avoid a circular import between `webapp.py` and `api/progress.py`.
- **WebSocket:** `/ws/progress` mirrors `/ws/term` auth (`X-SSHLER-TOKEN` query param). On connect, the broadcaster sends a `snapshot` event containing every current bar; subsequent events are `upsert` (POST) and `delete` (DELETE). Dead-socket reaping happens during fan-out.
- **CLI:** `sshler/cli.py` subparser — `progress push/list/delete`. Token discovery: `--token` → `$SSHLER_TOKEN` → `<config_dir>/runtime-token` cache (mode 0600, written by `serve()`). URL discovery: `--url` → `$SSHLER_PROGRESS_URL` → `http://127.0.0.1:8822`. `httpx` is in core deps (not `dev`) because of this.
- **Frontend store:** `frontend/src/stores/progress.ts` — Pinia store with reactive bars map + snapshot/upsert/delete reducer. Exponential-backoff reconnect (`RECONNECT_BACKOFF_MS = [1s, 2s, 4s, 8s, 16s, 30s]`). Subscriptions are client-side and **scoped per box**: `subscriptionsByScope: Record<boxName, string[]>`, keyed by `currentScope` (= `appStore.activeBox`). `subscribe/unsubscribe/isSubscribed` are no-ops when no box is active. Persisted at `localStorage["sshler:progress:subscribed"]` as the object shape (old flat-array shape is discarded on hydration).
- **Surfaces:** `<ProgressStrip>` (thin strip under AppHeader — the only live display surface, renders the current box's bars + a `+` picker button), `<ProgressPicker>` (modal opened from the strip's `+`), and `/app/progress` (`ProgressView`, management page with scope banner). The strip renders `subscribedBars` for the active box; the picker + management page list `allBars` (global pool) with scoped subscribe toggles. (A floating bottom dock existed earlier; it was removed in favor of the strip.)
- **No auto-dismiss:** subscribed bars persist on the strip until the user unsubscribes (client-side) or deletes them (REST). Every status stays, including `done` — subscribing is a deliberate "watch this" act, so the completed state is the payoff, not clutter. (An earlier 10s auto-hide of `done` bars was removed because it hid finished tasks users had subscribed to specifically to see complete.)
- **Metadata / tooltip / floor:** bars carry a fault-tolerant `metadata` bag (`+metadata_error`) — push replaces by default, `merge:true` to accumulate, omitted field leaves it untouched, malformed metadata never blocks progress (surfaces as an error line). Each strip bar has a rich hover tooltip showing fields + error. Displayed percent is **floored** (3299/3300 → 99%). A bar flashes white once when it transitions into `done`. Full detail in the skill doc.
- **WS lifecycle:** owned by `App.vue` (connect + refresh on mount, disconnect on unmount) so the strip stays live across all routes. Idempotent `connect` lets `ProgressView` also call connect without conflict. The strip itself is `v-if`-gated on box context, so it must NOT own the lifecycle.
- **Management page:** `frontend/src/views/ProgressView.vue` at `/app/progress`. Lists all bars (subscribed or not), per-row `<NSwitch>` subscribe, `<NPopconfirm>` delete, refresh button, connection-state pill, empty state with CLI push hint.
- **i18n:** flat dot-notation keys under `progress.*` and `nav.progress` in `frontend/src/locales/{en,ja}.ts`.
- **When extending the WS protocol:** add the new event variant to `ProgressEvent` in `frontend/src/api/types.ts`, handle it in `_handleEvent` in `stores/progress.ts`, and broadcast from `sshler/api/progress.py`. Keep events JSON-serializable; the WS payload is `json.dumps(event)`.

Detailed skill doc: `docs/skills/progress-bars/SKILL.md`.

### Diff Notebook Pipeline

Multi-cell diff workspace at `/app/diff` — review N file diffs at once across boxes, repos, refs. **Reuses existing endpoints**: no new backend code. Each cell is one `(left ↔ right)` file pair; sides are independent.

- **Reuse for git data.** Each side fetches via `GET /api/v1/boxes/{name}/git/show` (already existed); branch autocomplete via `GET /api/v1/boxes/{name}/git/branches`. A real server-side `git_diff` endpoint is intentionally NOT in scope — composing two `gitShow` calls per cell handles the common cases (renames/mode-changes are M4 polish).
- **Server-side notebook persistence (M3).** Four REST routes at `/api/v1/diff/notebooks[/:id]` — POST/GET-list/GET-by-id/DELETE — backed by `DiffNotebook` SQLerModel in `sshler/state.py`. Short URL form: `/app/diff/n/<id>` (~11-char `secrets.token_urlsafe(8)`). All routes token-gated; no per-author binding. **Saves are immutable** — every POST creates a NEW id. There is no PUT/upsert route; editing a server-loaded notebook forks (clears `serverId` in the store, URL falls back to `?n=<base64>`). Shared links stay alive forever.
- **Command parser:** `frontend/src/utils/diffCommandParser.ts` — pure function, no Vue, returns `Command | ParseError`. Grammar: `:add [left] [right]`, `:rm <n>`, `:swap <n> [m]`, `:repo <box> <dir>`, `:clear`, `?`. Side syntax: `box:directory@ref:path` (any segment may be empty, structural colons stay).
- **Store:** `frontend/src/stores/diff.ts` — `cells: DiffCellState[]`, `defaultRepo`. Cell statuses: `idle / loading / ready / error / binary`. Side states: `idle / loading / loaded / missing / error / truncated`. A `missing` side (404 from `git_show`) is NOT an error — it just renders as empty so adds/deletes display correctly. Binary detection is client-side via null-byte sniff on the first 8KB.
- **URL state:** base64-url(JSON.stringify({v: 1, cells: [{l, r}, …], def?})) in `?n=`. Versioned envelope. **Legacy M1 flat-key URLs** (`?lb=&ld=&…`) still hydrate transparently into a one-cell notebook.
- **Auto-fetch:** per-cell debounced 400 ms when both `box` AND `path` are non-empty on at least one side. No manual Compare button.
- **Keyboard:** `c` / `:` focus command bar, `?` toggle help, `j` / `k` next / prev cell (ignored while typing in any input).
- **History:** `frontend/src/composables/useDiffHistory.ts` — last 10 notebooks in localStorage (`sshler:diff:history`), dedupe by base64, capped at 10. UX-only; never a correctness path.
- **Surfaces:** `<DiffView>` (`/app/diff` AND `/app/diff/n/:id`), `<CommandBar>`, `<DiffCell>` (sticky header + embedded pickers + body), `<DiffSidePicker>` (4 inputs), `<DiffHelpOverlay>` (modal), `<DiffNotebookDrawer>` (right-side drawer listing server saves + local recents).
- **i18n:** flat keys `diff.*`, `diff.command.*`, `diff.help.*`, `diff.history.*`, `nav.diff` in `frontend/src/locales/{en,ja}.ts`.
- **When extending:** new command → parser case + parser spec + dispatch in `DiffView.applyCommand` + entry in `DiffHelpOverlay` array. New URL field → bump `NOTEBOOK_VERSION` + write v1→v2 migration. Real `git_diff` endpoint or `git ls-tree` path autocomplete → both M4-polish items, keep blob-based composition as the default.

Detailed skill doc: `docs/skills/diff-notebook/SKILL.md`.

### Ping Notification Pipeline

Fire-and-forget push notifications — an external script calls `POST /api/v1/ping`, the server fans out to every connected `/ws/ping` WebSocket client, and each browser tab shows a Naive UI toast.

- **REST:** `POST /api/v1/ping` — `{title, body?, color?, icon?, duration?, source?, metadata?}` → `{ok:true, id}`. Token-gated. No persistence.
- **Broadcaster:** `PingBroadcaster` in `sshler/webapp.py` (same pattern as `ProgressBroadcaster`, stateless). Wired via `deps.broadcast_ping` to avoid circular imports.
- **WS:** `/ws/ping` — no snapshot on connect. Clients receive `{type:"ping", id, title, body, color, icon, duration, source, metadata, sent_at}` events.
- **Frontend store:** `frontend/src/stores/ping.ts` — WS connect/disconnect with exponential-backoff reconnect. Exposes `pendingPings` queue + `drainPings()`.
- **Notification rendering:** `frontend/src/components/PingNotificationHandler.vue` (renderless) — placed inside `<NNotificationProvider>` in `App.vue`, watches `pendingPings`, calls `useNotification().create()` per ping.
- **Dismiss priority:** per-ping `duration` → `appStore.pingDefaultDuration` (Settings UI) → manual (default).
- **CLI:** `sshler ping --title "..." --body "..." --color success --icon 🚀 --duration 8000 --source deploy-bot`

Detailed skill doc: `docs/skills/ping/SKILL.md`.

### Claude Session Dashboard

`/app/claude` lists resumable Claude Code sessions read from the local filesystem and resumes a chosen one into its own browser terminal. **Local box only** — it reads the same machine sshler runs on. **Pull-based (no WebSocket/broadcaster)** — unlike progress/ping, the data source is the filesystem, so it's a plain REST list + manual/focus refresh.

- **Scanner:** `sshler/claude_sessions.py` reads `<config-dir>/projects/*/*.jsonl` (honors `$CLAUDE_CONFIG_DIR`, default `~/.claude`). Each `.jsonl` is one resumable session (`claude --resume <uuid>`). Parsing is frugal but **whole-file** (streamed in blocks via `_iter_lines`, oversized tool-output lines skipped, only title/metadata lines JSON-parsed): `stat` for mtime (= last-active), `cwd`/`version`/`gitBranch`/first-typed-prompt from the header, and the latest `custom-title`/`ai-title`/`last-prompt` from anywhere in the file (they can sit MB from EOF in long sessions). **Title priority mirrors `/resume`:** latest `customTitle` (user `/rename`) → latest `aiTitle` → latest `lastPrompt` → first typed prompt → "(untitled session)". Cached per `(path, mtime, size)`. `glob` results guarded with `resolve().is_relative_to(base)` so symlinks can't escape the projects tree.
- **REST:** `sshler/api/claude_sessions.py` (`get_router(deps)` factory, token-gated via the `/api/v1` router). `GET /api/v1/claude/sessions?limit=&since_days=` lists; `POST /api/v1/claude/sessions/{id}/open` resumes. `open` validates the id as a strict UUID (`is_valid_session_id`) BEFORE any fs/tmux op, then resumes **into the repo ROOT's `ts` session as a new window** (not a per-Claude session): `session_dir = info.repo_root or info.cwd` names the tmux **session** (`session = ts_session_name(session_dir)`, `window = cl-<6hex>`), so a conversation that ran in `repo/sub` lands as a tab in the repo's top-level session (matching how the user organizes these). **But the window's shell starts in `info.cwd` (the exact subdir), not the repo root** — `claude --resume <uuid>` is **cwd-scoped** (it only finds a session whose project folder maps to the current directory), so resuming from the repo root fails with "No conversation found". Ensures the session exists (`new-session -d -c session_dir`), then if the window doesn't exist opens it (`new-window -n <window> -c info.cwd` — tmux `-c` cd's the window natively, no shell escaping), types `send-keys -l "<cmd>"` + a separate `send-keys Enter`, focuses it (`select-window`), and persists a `state.Session` (metadata `kind:claude`, `window`; `working_directory = session_dir`, the repo root). **Idempotent + non-destructive:** if the window already exists it just `select-window`s and returns `already_open=true` (never re-types into a running claude); the UI toasts "already open in `<dir>`".
- **`ts`-native session naming:** `ts_session_name(dir)` in `sshler/tmux.py` produces the SAME name the `ts` CLI uses for a local dir (basename, `.`/`:`→`_`, hyphens kept, **no hash**), so sshler and `ts` share one tmux session per dir — attach from a plain terminal via `ts` if sshler is down. The frontend `generateSessionName(dir, box)` matches it byte-for-byte for `box === "local"` (remote boxes keep the collision-safe hashed scheme). Consequence: two local dirs with the same basename share a session — `ts`'s own behavior, intentional.
- **tmux helpers:** `_run_local_tmux_command`, `_configure_tmux_bindings`, `_tmux_color_for_session`, `list_local_window_names`, `ts_session_name` live in `sshler/tmux.py` (so `api/*` reuse them without a circular import). `/ws/term` configures bindings/color on attach.
- **Frontend:** `stores/claudeSessions.ts` (minimal pull store), `views/ClaudeSessionsView.vue` at `/app/claude`. **Grouped by git repo root, matching `/resume`** — the scanner returns `repo_root` (walk up from `cwd` for `.git`, cached per dir in `claude_sessions.py::_git_root`), so a session run in `repo/sub` groups under `repo`, not its exact cwd. Each repo is an `<NCollapse>` accordion item; the header shows the **same per-directory emoji the rest of the app uses** (`getEmojiForString("local:"+repoRoot)`, tinted with the repo's accent color) + name + `{n} · {size}` summary. **Repos start collapsed**; a live filter expands all matching repos (so search hits aren't hidden) and clearing it re-collapses; explicit **expand-all / collapse-all** buttons sit next to the filter (`expandedNames` ref). Inside each repo, an `<NTimeline>` with one item per session, dot color by recency (green <1h / blue <1d / amber <1wk / grey), plus per-row tags: subdir (`PhFolder`, when `cwd != repo_root`), git branch, transcript size, Claude version, custom-command badge, and a muted `last_prompt` preview. **Two resume buttons:** primary `Resume` (`handleResume(session, false)`) opens in the **background and stays on the list**; a secondary `PhArrowLineUpRight` button (`handleResume(session, true)`) opens AND `router.push`es to `/app/terminal?box=&dir=&session=`. Copy-command + focus refresh unchanged. `TerminalView` honors `?session=` (attaches to that exact tmux session instead of deriving from dir). Nav entry `nav.claude` (Alt+L); i18n `claudeSessions.*` (incl. `claudeSessions.meta.*`, `claudeSessions.group.*`, `resume_here_hint`, `resume_open`, `expand_all`, `collapse_all`).
- **Configurable resume command:** the resume command is a template with an `{id}` placeholder (default `claude --resume {id}`), so users can inject their own launcher/flags without anything being hardcoded. Config lives client-side in localStorage at two levels — global (`sshler:claude:resumeTemplate`) + per-session overrides (`sshler:claude:resumeOverrides`); resolution is override → global → default (`store.templateFor(id)`). The resolved template is POSTed to `open` as `command_template`; the server (`_resolve_resume_command`) requires the `{id}` placeholder, rejects control chars, substitutes the **validated** UUID, and types the rest into the user's own shell. UI: a global popover in the header + a per-row override modal (`custom` badge when set).
- **When extending:** remote-box sessions, live updates, exact turn-count, and a fork-on-resume toggle are all out of scope for M1.

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

### ADR-002: Dual UI Strategy (superseded)
Originally sshler shipped a legacy HTMX UI at `/` alongside the Vue SPA at `/app` while the SPA reached feature parity. The SPA has since reached parity and the HTMX UI was removed — `/` now redirects to `/app`. The Vue SPA is the only UI.

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
