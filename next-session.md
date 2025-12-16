# Next Session Game Plan

## North Star
- Finish Vue3 parity so the SPA fully replaces legacy HTMX views without regressions.
- Keep `/ws/term` fidelity: resize throttling, mobile viewport handling, bell/OSC, multi-pane awareness.
- Match legacy file UX: edit/save, previews, downloads, favorites/recents, bulk/context actions.

## Current Status
- Frontend: `pnpm test` and `pnpm build` passing; dist emitted to `sshler/static/dist`.
- Backend: `uv run pytest tests/test_api_v1.py tests/test_routes.py` + `tests/e2e/test_vue_app.py` passing (one existing asyncio warning remains).
- SPA served via `sshler/spa.py` at `/app`; legacy templates remain for reference under `sshler/templates/`.

## Parity Targets & Files
- Files view (`frontend/src/views/FilesView.vue`)
  - Already: list/filter, favorites/recents, upload with progress, preview (text/image), per-row edit/save/download, selection + bulk delete.
  - Missing: CodeMirror-grade editor (syntax highlight, word wrap toggle), richer preview types (pdf/media/archives), context menu/bulk operations beyond delete, toasts/recents/favorites polish.
  - API paths mirrored from legacy: `/api/v1/boxes/{name}/ls|touch|delete|rename|move|copy|upload|download|file|write`.
  - Backend write endpoint: `sshler/api/files.py` (`/api/v1/boxes/{name}/write`).
- Terminal view (`frontend/src/views/TerminalView.vue`)
  - Already: real `/ws/term` connect, session/dir selection, resize throttling (window + visualViewport), window list chips, bell notice.
  - Missing: fit-to-container sizing, mobile keyboard/scroll helpers, bell/OSC 777 notifications surfaced as toasts, multi-pane/session layout parity with legacy `sshler/static/multi-session.js` and `sshler/static/term.js`.
  - Backend socket: `sshler/webapp.py` at `/ws/term`; handshake endpoint `sshler/api/terminal.py`.
- Command palette & header: tests green, but extend actions to new file/term flows.
- i18n: minimal (`frontend/src/i18n.ts`), needs legacy strings ported.

## High-Value Next Tasks
1) Drop in CodeMirror-based editor in FilesView, use `/api/v1/boxes/{name}/write`, add save toasts and dirty-state guard.
2) Add context menu + bulk actions (download, move/copy, favorite) leveraging `filesStore.selectedFiles`.
3) Terminal fit + mobile helpers: import behavior from `sshler/static/term.js` (resize/bell) and `sshler/static/multi-session.js` (panes/windows), surface OSC/bell toasts.
4) Port legacy strings/shortcuts into SPA (see `sshler/templates/base.html`, `static/command-palette.js`).

## Reference Paths
- Legacy UI for parity: `sshler/templates/*`, `sshler/static/file-browser.js`, `sshler/static/term.js`, `sshler/static/multi-session.js`.
- SPA stores/components: `frontend/src/stores/*`, `frontend/src/components/CommandPalette.vue`, `frontend/src/components/AppHeader.vue`.
- API wiring: `frontend/src/api/http.ts`, `frontend/src/api/types.ts`; backend routers under `sshler/api/`.

## Quick Commands
- Frontend tests/build: `cd frontend && pnpm test`, `pnpm build`.
- Backend smoke: `uv run pytest tests/test_api_v1.py tests/test_routes.py` and `uv run pytest tests/e2e/test_vue_app.py -q`.
