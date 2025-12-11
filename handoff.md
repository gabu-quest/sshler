# Handoff notes for next session (aggressive execution)

## Current branch
- `vue-migrate`

## Recent work
- SPA/legacy toggle added: `sshler serve --ui=legacy|vue|both`; bootstrap returns `spa_enabled`/`spa_base`; root redirects to `/app/` only when SPA is on.
- Favorites/pins persist per box (backend state DB) and survive reload; Playwright e2e covers file ops, terminal connect/resize, boxes pin/favorite, download endpoint content, and persistence after reload.
- SPA i18n init fixed; favorites/pins hydrated from backend; local box override deduped.
- Dev docs added (`docs/dev-workflow.md`); README/roadmap updated. Backend httpx/ws test fixed (ASGITransport).
- Tests green locally: pnpm build, Vitest, `.venv/bin/pytest tests/test_api_v1.py tests/test_handshake_status.py tests/test_httpx_ws.py tests/e2e/test_vue_app.py -q` (asyncio unraisable warning still emitted by httpx/TestClient teardown).

## Outstanding MUST-DO items
- Modularize `webapp.py` (3.9k lines, 21 api_router routes, 27 legacy routes) into `sshler/api/{boxes.py,files.py,sessions.py,terminal.py,config.py}` plus shared models/helpers. Keep behavior identical; mount spa via `sshler/spa.py`.
- SPA polish: upload progress, recents/filters, richer command palette/shortcuts consistency, favorites UI icons/toasts alignment.
- PWA/mobile/accessibility: manifest/SW decision, mobile keyboard/resize, aria/focus passes, reduced-motion.
- CI: ensure backend httpx/ws tests + Playwright paths run; keep pnpm build/test.

## How to proceed (aggressive)
- Ignore caution/time limits; deliver maximal changes each turn.
- First: extract API routes from `webapp.py` into modules, wire a central router factory, and re-run pytest + Playwright.
- Then: SPA UX polish (uploads progress UI, recents, keyboard overlay/palette breadth).
- If time: add PWA/manifest hooks and mobile/accessibility sweep.

## Paths/files to touch
- Backend: `sshler/webapp.py` (split), new `sshler/api/*.py`, `sshler/spa.py` (already exists).
- Frontend polish: `frontend/src/views/{FilesView.vue,BoxesView.vue,TerminalView.vue}`, `frontend/src/components/{CommandPalette.vue,ShortcutsOverlay.vue,FavoritesPanel.vue}`, `frontend/src/stores/*`, `frontend/src/api/*`.
- Tests: `tests/e2e/test_vue_app.py` (expand assertions), backend tests unchanged.
- Docs: `README.md`, `docs/vue-migration-roadmap.md`, `docs/dev-workflow.md`.

## Behavior reminders
- Do not revert user changes; keep responses concise.
- Use pnpm for frontend; backend via uv/.venv.
- Network allowed; approval policy: never (work within sandbox).***
