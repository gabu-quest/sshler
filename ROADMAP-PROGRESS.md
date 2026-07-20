# Roadmap: Global Progress Bars

External scripts push progress updates to sshler; the UI subscribes to bars by name and renders them as a dock. Global by design — anyone holding the sshler token can push or read any bar. No per-box gating. Subscription/filtering happens entirely client-side.

## Design summary

- **Push protocol:** `POST /api/v1/progress/:name` with `{current, total, color?, label?, status?}`. Upsert. No auth beyond the existing `X-SSHLER-TOKEN` header.
- **Delete:** `DELETE /api/v1/progress/:name` (manual cleanup; also nukes stale entries).
- **List:** `GET /api/v1/progress` → all bars with `created_at`, `updated_at`, staleness flag.
- **Live updates:** `WS /ws/progress` broadcasts every upsert/delete event to all connected clients.
- **Storage:** new SQLite table `progress_bars` in the existing state DB.
- **Pusher CLI:** `sshler-progress push <name> <current> <total> [--color X] [--label Y] [--status running|done|failed]`.
- **UI:**
  - Pinia store subscribed to `/ws/progress`.
  - `<ProgressDock>` component — variable-count bars at the bottom (or top, configurable) of any view, gated by user subscription list (client-side only).
  - `/app/progress` page — full list with created/updated times, stale highlighting, drop button.
  - Subscription model: user picks which bars to "watch" (persisted in localStorage). Unsubscribed bars still exist server-side; the user just doesn't see them in the dock.

## Milestones

### M1: Backend foundation ✅
- [x] `ProgressBar` SQLerModel in `sshler/state.py` (name, current, total, color, label, status, created_at, updated_at) with sync + async CRUD
- [x] `sshler/api/progress.py` — POST upsert, GET list, GET by name, DELETE; Pydantic validation; name regex `^[A-Za-z0-9._:-]{1,64}$`; closed-enum status
- [x] `ProgressBroadcaster` in `sshler/webapp.py` — per-app asyncio lock + connection set, dead-socket reaping
- [x] `/ws/progress` WebSocket endpoint with snapshot-on-connect, disconnect sentinel
- [x] Auth via existing `X-SSHLER-TOKEN` header / query param (REST gated by `Depends(deps.require_token)`; WS mirrors `/ws/term`)
- [x] `tests/test_api_progress.py` — 12 tests: CRUD, idempotency, auth, name/status/total validation
- [x] `tests/test_progress_websocket.py` — 6 tests: snapshot, upsert event, delete event, two-client fan-out, bad-token close

**Result:** 18 new tests passing. Full backend suite 189 passed / 5 skipped / 0 failures. No new mypy errors.

### M2: Pusher CLI ✅
- [x] `sshler progress push <name> <current> <total> [--color --label --status]` in `sshler/cli.py`
- [x] `sshler progress list [--json]`
- [x] `sshler progress delete <name>`
- [x] Token discovery chain: `--token` > `$SSHLER_TOKEN` > `<config_dir>/runtime-token` cache > exit 2
- [x] URL discovery chain: `--url` > `$SSHLER_PROGRESS_URL` > `http://127.0.0.1:8822`
- [x] `serve()` writes the active CSRF token to `<config_dir>/runtime-token` (mode 0600) so local CLI calls work without env vars
- [x] `httpx` promoted from `dev` to core `dependencies` in `pyproject.toml`
- [x] Client-side name + status validation (fails fast before HTTP)
- [x] `tests/test_cli_progress.py` (18 tests) and `tests/test_serve_token_cache.py` (6 tests)

**Result:** 24 new tests passing. Full backend suite 213 passed / 5 skipped / 0 failures.

### M3: Frontend store + subscription model ✅
- [x] `frontend/src/stores/progress.ts` — Pinia store with reactive bars map, snapshot/upsert/delete reducer, WS connect/disconnect, exponential-backoff reconnect (1s→2s→4s→8s→16s→30s)
- [x] REST helpers added to `frontend/src/api/http.ts`: `fetchProgress(token)` and `deleteProgress(name, token)`. ProgressBar/ProgressEvent/ProgressList/ProgressDeleteResult/ProgressStatus types added to `api/types.ts`.
- [x] Subscription list persisted in localStorage at `sshler:progress:subscribed`; hydrated on store init; deleted bars do NOT auto-unsubscribe so re-pushed names reappear in the dock
- [x] `isStale(bar, thresholdSec = 300)` helper (configurable per-call)
- [x] `frontend/src/stores/progress.spec.ts` — 15 tests covering event reducer, subscription persistence, isStale, refresh/remove, WS URL/auth/lifecycle, reconnect timing, disconnect cleanup

**Result:** 15 new Vitest tests passing. Full frontend suite 80 passed / 0 failed (the prior 26 i18n failures noted in memory are no longer present — see `feedback_…` notes). No new TypeScript errors from M3 files; pre-existing errors in unrelated components.

### M4: Progress dock component ✅
- [x] `frontend/src/components/ProgressDock.vue` — fixed-bottom dock that renders subscribed bars only (hidden when nothing is subscribed)
- [x] Mounted in `App.vue` (peer to `<RecoveryModal>`) so it appears on every route
- [x] Per-bar layout: color stripe + label/name + NProgress + numbers + animated status pip (Phosphor duotone icons — running/done/failed/cancelled)
- [x] Stale rows dimmed (opacity 0.5) when `updated_at` > 5 minutes ago
- [x] Connection-state dot (green/amber/grey) at the dock's left edge
- [x] `<Transition>` + `<TransitionGroup>` for smooth show/hide and add/remove
- [x] Mobile: bars wrap to full width below 768 px
- [x] Owns WS lifecycle: `connect` on mount, `disconnect` on unmount; idempotent so M5 can also call connect without conflict
- [x] `frontend/src/components/ProgressDock.spec.ts` — 6 Vitest tests (visibility, label fallback, percentage, status icon, stale styling, lifecycle hooks)

Configurable position (top/bottom) deferred to M6 — default bottom is fine for v1.

**Result:** 6 new tests passing. Full frontend suite 86 passed / 0 failed. No new TypeScript errors.

### M5: Progress management page ✅
- [x] `frontend/src/views/ProgressView.vue` mounted at `/app/progress`
- [x] Lists ALL bars (subscribed or not) — name, color stripe, label fallback, status tag, current/total bar, created/updated relative times, stale flag
- [x] Per-row `<NSwitch>` for subscribe/unsubscribe (instant, localStorage-backed)
- [x] Per-row delete via `<NPopconfirm>` → `store.remove(name, token)` → success toast
- [x] Toolbar: refresh button, bar count, connection state pill
- [x] Empty state with CLI push hint
- [x] Nav entry added to `AppHeader` (`PhChartBar` icon, `Alt+P` shortcut, between Multi-Terminal and Settings)
- [x] Route registered in `frontend/src/router/index.ts` (before `/settings`)
- [x] i18n keys added to `locales/en.ts` and `locales/ja.ts` (`nav.progress`, `progress.*` namespace)
- [x] `frontend/src/views/ProgressView.spec.ts` — 6 Vitest tests (empty state, row rendering, subscribe toggle, delete confirm, refresh, connect-on-mount)

**Result:** 6 new tests passing. Full frontend suite 92 passed / 0 failed. No new TypeScript errors.

### M6: Polish ✅
- [x] Reconnect logic on WS drop (exponential backoff) — shipped in M3
- [x] Naive UI theming for bar colors (light + dark mode) — replaced hard-coded rgba fallbacks with `var(--n-action-color, color-mix(...))` so tiles flip correctly between themes
- [x] Mobile: dock collapses to single-line summary, expand on tap — driven by `useResponsive` + local `expanded` ref
- [x] Empty state on `/app/progress` — shipped in M5
- [x] Auto-dismiss for `done` bars (`DONE_AUTO_DISMISS_SEC = 10`, hides from dock view only — server state and management page untouched; `failed`/`cancelled` never auto-hide)
- [x] Dock position toggle (top/bottom) with localStorage persistence
- [x] New tests: 7 store + 3 dock = 10 new Vitest tests (auto-dismiss, position toggle, mobile collapse)

**Result:** 10 new tests passing. Full frontend suite 102 passed / 0 failed. No new TypeScript errors. End-to-end manual smoke test verified the full pipeline (CLI push → REST → SQLite → `/ws/progress` snapshot/upsert/delete fan-out).

### M7: Docs ✅
- [x] `README.md` — feature blurb section + "Pushing progress bars" usage subsection + token discovery paragraph
- [x] `README.ja.md` — Japanese mirror of the same content
- [x] `CLAUDE.md` — new "Progress Bars Pipeline" section parallel to the PDF section + project-tree entry for `api/progress.py`
- [x] `docs/skills/progress-bars/SKILL.md` — new skill doc covering pipeline, REST/WS contracts, auto-dismiss semantics, file map, "do not refactor away" notes
- [x] `examples/progress-bar-build-watcher.sh` — executable demo loop with `trap` → `--status failed` fallback. Verified end-to-end against the running daemon (build bar climbs 0→100 then done).

**Result:** Feature is fully discoverable. Future Claude sessions have a skill doc to load instead of re-deriving the architecture. All milestones (M1–M7) shipped on `feat/progress-bars`.
