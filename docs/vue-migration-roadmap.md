# Vue 3 migration roadmap (FastAPI + bundled dist)

Target: replace the current Jinja/HTMX UI with a Vue 3 SPA (composition API) built with Vite, Naive UI, Phosphor Icons, Pinia, and Vue Router. The FastAPI app should serve the built assets (no Node at runtime) and expose a clean JSON/WebSocket API that matches existing features (file browser, terminal/tmux, config management, PWA behaviors).

## Scope and constraints
- [ ] keep the existing CLI surface (`sshler serve`, flags, token/basic auth) and FastAPI lifespan hooks that initialize the SSH pool
- [ ] preserve security model: `X-SSHLER-TOKEN` header on mutating requests, optional basic auth, same CORS story
- [ ] ship prebuilt frontend assets inside the Python wheel/sdist; no Node dependency at runtime
- [ ] maintain mobile optimizations Claude just shipped (keyboard/viewport handling, passive touch listeners, resize throttling)
- [ ] keep bilingual support (en/ja) or document a transition plan for i18n before cutting over

## Backend API and FastAPI app
- [x] carve out an `/api/v1` namespace with JSON responses (bootstrap, boxes list/detail, directory listing, touch/upload/delete/rename/move/copy, file preview/download, terminal handshake, pins/favorites, box status, sessions listing)
- [x] add typed request/response models (pydantic v2) and error envelopes for the new endpoints; expand coverage as routes arrive
- [ ] keep `/ws/term` (or `/api/v1/ws/term`) for xterm bridge; ensure resize, clipboard, notifications still flow
- [x] add a SPA index route that serves `index.html` from the built dist and a fallback for client-side routing (404 -> index for known app paths)
- [ ] move `webapp.py` routing into modules (`api/boxes.py`, `api/files.py`, `api/terminal.py`, `api/sessions.py`, `api/config.py`) while keeping the existing `lifespan` handler for pool init/shutdown
- [x] centralize auth/token bootstrap so the SPA can fetch a payload (token, allow origins, feature flags) via one call
- [ ] ensure streaming/file responses (download, previews) stay efficient and reuse the SSH/SFTP pooling
- [ ] keep service worker/manifest endpoints working; decide whether to serve them from dist or a small `/pwa` mount

## Frontend (Vue 3 + Vite)
- [x] scaffold `frontend/` with Vite + TypeScript; set up aliasing to `@/` and ESLint/Prettier that match repo style
- [x] install core deps: `vue`, `vue-router`, `pinia`, `naive-ui`, `@phosphor-icons/vue`, `@vitejs/plugin-vue`
- [x] define router views: Boxes list, Box detail (file browser + preview/edit), Terminal (multi-session/tmux), Settings/about, fallback 404
- [x] build Pinia stores: auth/config bootstrap, boxes + favorites, file browser state (cwd, selections), sessions/terminal, user prefs (theme, language)
- [ ] port key UI features: command palette, keyboard shortcuts overlay, global search, theme toggle, upload progress, bookmarks/recent files, terminal toolbar and resize behaviors, mobile/fullscreen affordances
- [ ] create shared components with Naive UI (app shell, topbar, toasts/notifications, modals, drawers, breadcrumb, data tables, context menus)
- [ ] wire Phosphor icons for nav/actions; standardize icon mapping for commands
- [ ] handle i18n early (vue-i18n or lightweight dictionary) so existing en/ja strings survive the migration
- [ ] replicate PWA bits (manifest, SW update prompt) via `vite-plugin-pwa` or manual service worker integration
- [ ] keep accessibility: focus management, aria labels, reduced-motion handling, high-contrast theme variables

## State and data contracts
- [ ] document API contracts for boxes, files, sessions, and terminal messages (JSON schema-like notes) so frontend and backend agree
- [ ] map existing localStorage uses (theme, terminal prefs, recents) into Pinia + `useStorage` helpers with versioned keys
- [ ] define websocket message shapes for terminal control (`resize`, `select-window`, `bell`, notifications) and ensure throttling rules match Claude’s fixes
- [ ] decide how to represent uploads (multipart) and downloads (stream) in the SPA; keep progress reporting behavior

## Build, packaging, and CI
- [x] choose package manager (pnpm) and lockfile; add it to `.gitignore` and CI cache
- [x] configure `pnpm build` to output to `sshler/static/dist` with hashed assets and `index.html`
- [x] update `MANIFEST.in` and `tool.setuptools.package-data` to include `static/**/*` (dist already covered)
- [x] add a small build helper (`scripts/check_frontend_dist.py`) invoked by release tooling to fail fast if the dist is missing
- [x] update `.github/workflows/ci.yml` to install Node, run `pnpm run build` and `pnpm run test` (Vitest) before `uv run pytest`/Playwright
- [x] add developer docs: `uv sync --group dev`, `pnpm install`, `pnpm run dev` (with proxy to FastAPI), `uv run sshler serve --no-browser`

## Dev workflow (pnpm)
- [x] pnpm install inside `frontend/` and use `pnpm dev -- --host --base /app/` (visit `http://localhost:5173/app/`)
- [x] pnpm build outputs to `../sshler/static/dist` with hashed assets and `/app/` base paths
- [x] run `uv run sshler serve` to serve the SPA at `/app` when the dist exists; legacy UI stays at `/`
- [x] before packaging or release, ensure `pnpm build` has been executed so the wheel/sdist contains the built assets
- [x] add a make/uv task alias for `pnpm build` + dist check (`make frontend-build` runs pnpm build then scripts/check_frontend_dist.py)

## Testing and QA
- [x] backend: FastAPI route tests with `httpx`/TestClient, websocket connect, file ops, pins/favs/status, session CRUD, download
- [x] frontend: Vitest + Vue Testing Library for stores/components; contract tests for API clients; snapshots optional
- [x] e2e: Playwright against built assets + FastAPI server (auth token, file ops, download, terminal attach/resizes, boxes list, favorites/pin flow)
- [ ] mobile manual checks: virtual keyboard, orientation change, fullscreen, touch gestures; confirm resize throttling parity
- [ ] performance sanity: bundle size budgets, lazy-load heavy views (terminal, code editor), ensure no blocking inline scripts

## Rollout and migration strategy
- [x] create a temporary toggle to serve either legacy templates or the SPA (CLI `--ui=legacy|vue|both`)
- [ ] keep old endpoints functioning while the SPA uses `/api/v1`; deprecate HTMX routes with warnings once parity is achieved
- [x] import existing favorites/pins into the new stores (state DB + boxes payload); define one-time migration for localStorage keys if names change
- [ ] double-check service worker update flow to avoid the auto-refresh regressions Claude fixed; keep user-confirmed reload prompt
- [ ] document breaking changes (if any) and update README with SPA instructions and screenshots

## Open questions / decisions to make early
- [ ] pick the package manager (npm vs pnpm) and whether to commit the built dist or generate it in release CI
- [ ] choose xterm integration approach (reuse current JS, or adopt `@xterm/xterm` + addons) and how to share theme settings
- [ ] confirm i18n strategy and translation source of truth (JSON catalogs vs existing template strings)
- [ ] decide on analytics/logging (likely none) and error reporting for the SPA
