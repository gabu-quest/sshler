# Claude Context: sshler

**Type:** Full-stack web application (FastAPI + Vue 3). The version is in `pyproject.toml`.

## What This Is

**sshler** is a local-only web UI for browsing remote files over SFTP and using tmux sessions in a browser terminal (WebSocket + xterm.js). Nothing is installed on the remote.

- Single-user localhost tool (not a multi-tenant service)
- One UI: the Vue 3 SPA at `/app` (root `/` redirects there)

## Repository Structure

```
sshler/                 ← Python backend (FastAPI)
├── webapp.py           ← Main app, routes, WebSocket handlers; opens each terminal
├── terminal/           ← /ws/term transports (local PTY, SSH, Windows ConPTY) and the pump
├── api/                ← API v1 endpoints, one module per group of routes
│   ├── file_contract.py, box_files.py   ← the files of a box: one interface (`BoxFiles`)
│   ├── local_files.py, remote_files.py  ← its two implementations and their helpers
│   ├── placement.py    ← the rule for a name that already exists (rename, move, copy, upload)
│   ├── box_runner.py ← a command on a box (`BoxRunner`): git, grep, tmux, stats
│   └── remote.py       ← remote paths (`~` resolved once per connection), remote command lines (quoted words)
├── ssh.py              ← SSH/SFTP (asyncssh); `sftp_session` opens and closes every SFTP session
├── ssh_pool.py         ← Connection pooling
├── config.py           ← Config loading (boxes.yaml + SSH config)
├── state.py            ← SQLite state (sessions, favorites)
├── session.py, auth.py ← Session store, auth middleware, rate limiting
├── validation.py       ← Path validation
├── artifacts.py, pdf.py, claude_sessions.py ← Features (see docs/architecture.md)
└── static/dist/        ← Built Vue SPA (served at /app)
frontend/src/           ← Vue 3 SPA: views/, components/, stores/ (Pinia), composables/, api/, router/
tests/                  ← pytest (`test_*.py`), Playwright in `tests/e2e/`
```

## Tech Stack

FastAPI + uvicorn (Python 3.12+, async) · asyncssh · SQLite via sqler · Vue 3 + Pinia (`<script setup>`) · xterm.js · Playwright for the optional `[pdf]` extra · pytest / Vitest · uv.

## Key Patterns

### Backend

**webapp.py is the monolith**: routes, WebSocket handlers, middleware. API endpoints are modular in `sshler/api/`.

**Local vs remote boxes (ADR-003):** `box.name == "local"` → subprocess tmux and the local file system, no SSH; otherwise an asyncssh connection and SFTP.

**Files of a box:** a file route never asks which kind of box it has. It opens `box_files(deps, box, application_config)` and calls the operations of `BoxFiles`; `LocalFiles` and `RemoteFiles` each keep their side's checks and answers. A failure that no operation answered becomes an answer in one place, `Answers` in `api/box_files.py`. `tests/test_box_files_contract.py` proves the interface for both kinds; a new operation joins its table.

**Terminal:** `/ws/term` opens one transport from `sshler/terminal/` behind the contract in `terminal/transport.py`; `terminal/pump.py` carries the bytes and knows nothing of the transport. Detail: `docs/architecture.md` "Terminal socket".

**Security layers:**
- `X-SSHLER-TOKEN` header or `token` query param, compared in constant time by `access.token_matches` (REST and sockets share it)
- Origin validation (CSRF): `access.allowed_origins` is the one list; the HTTP middleware applies it to POST/PUT/PATCH/DELETE and every socket to its upgrade (a present, unlisted Origin, including `null`, is refused; no Origin is allowed; `SSHLER_ORIGIN_CHECK_ENABLED=false` turns both off)
- `/ws/term`, `/ws/progress` and `/ws/ping` call `access.websocket_refusal` before accepting: credentials (close 4401), token (4403), Origin (4403); `/ws/term` then validates the session name (4400)
- Rate limiting on auth endpoints

### Frontend

**Pinia stores** (`stores/`) hold all state: `app` (theme, terminal settings, `activeBox`), `bootstrap` (config from `/api/v1/bootstrap`), `boxes`, `files`, `favorites`. The API client in `src/api/http.ts` adds auth headers. The terminal component wraps xterm.js and its WebSocket. Emoji favicons, active-box tracking, directory search and the mobile terminal: `docs/architecture.md` "Frontend details".

## Project CLI (just)

`just` with no arguments lists every recipe: `just test` (backend + frontend), `test-backend`, `test-frontend`, `test-e2e`, `build`, `typecheck`, `dev` (backend + Vite HMR), `ci` (build + test + typecheck).

Direct forms: `uv run pytest`, `uv run pytest tests/e2e/` (after `uv run playwright install chromium`), `pnpm --prefix frontend test -- --run`.

Key suites: `test_websocket.py`/`test_terminal_websocket.py` (WS), `test_api_v1.py`, `test_box_files_contract.py`/`test_existing_name_rule.py` (files), `test_command_injection.py`/`test_path_validation.py` (security), `test_session_auth.py`.

## Security Considerations

**MUST validate:** all file paths (symlink escape, traversal), session names (shell injection), Origin headers on state-changing requests, token presence on all authenticated endpoints.

**MUST NOT:** execute uploaded content, disable auth in configs, or trust client paths without normalization.

**Auth:** httpOnly session cookies (Secure in production); optional basic auth for exposed deployments.

## Development Workflow

`uv run sshler serve --dev` runs FastAPI on :8822 and Vite on :5173 (HMR at `/app/`). **`--dev` is required with the Vite dev server:** it adds `http://localhost:5173` to the allowed origins and reloads the backend; without it, POSTs from Vite fail with 403. More: `docs/dev-workflow.md`.

## Common Tasks

- **API endpoint:** route in `sshler/api/<module>.py` (or `webapp.py`), tests in `tests/test_api_v1.py`, update the frontend client if needed.
- **File operation:** a method on `BoxFiles` kept by both implementations, and a row in the contract test.
- **Vue component:** `frontend/src/components/`, `*.spec.ts` alongside, `<script setup>`.
- **WebSocket protocol:** `webapp.py` handler + `sshler/terminal/pump.py` + `TerminalView.vue` + `test_websocket.py`/`test_terminal_websocket.py`. A new transport implements `terminal/transport.py` `Transport` and joins the contract test.

## Before changing a feature, read its section

`docs/architecture.md` holds the architecture and invariants of: the terminal socket; the shared broadcaster pattern (progress, ping); PDF export; progress bars; local HTML artifacts (the deletion invariant); the diff notebook (immutable notebooks, versioned URL state); ping; the Claude session dashboard (`ts_session_name` must match the `ts` CLI). CLI usage of these features is in `docs/skills/<feature>/SKILL.md`.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SSHLER_HOST` | 127.0.0.1 | Bind address |
| `SSHLER_PORT` | 8822 | Port |
| `SSHLER_CONFIG_DIR` | Platform default | Config location |
| `SSHLER_PUBLIC_URL` | - | For origin validation |
| `SSHLER_COOKIE_SECURE` | true | Secure cookie flag |

## ADRs

- **ADR-001, cookie sessions over JWTs:** sessions are revocable and simpler, and right for a single-backend browser app.

## Known Gotchas

- **403 on POST, or a socket that will not open:** the backend validates `Origin` on every state-changing request and WebSocket upgrade (the log says `WebSocket refused: origin ... is not allowed`). Run with `--dev` when using the Vite dev server; set `SSHLER_PUBLIC_URL` behind a proxy.
- **Favorites live in two places:** the SQLite state database (`state.replace_favorites_async`) and the YAML config (`save_config`). `refresh_box` resets connection overrides and must NOT touch favorites.
- **Asset paths in the frontend are relative** in `index.html` and `manifest.webmanifest` (`favicon.png`, not `/app/favicon.png`): Vite resolves the base path at build, and absolute paths break the dev server.

## Before Committing

- [ ] Tests pass (`uv run pytest && npm --prefix frontend test -- --run`)
- [ ] Type checks pass (`uv run mypy sshler/`)
- [ ] No security regressions (path validation, auth)
