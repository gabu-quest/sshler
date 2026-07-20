---
name: sshler-progress-bars
description: Reference for sshler's global progress bars pipeline — push protocol, WebSocket fan-out, per-box subscription model. Load when working on stores/progress.ts, ProgressStrip.vue, sshler/api/progress.py, or extending the WS event protocol.
---

# sshler — Global Progress Bars

External scripts push progress updates via REST; every connected browser tab receives the live event over a single WebSocket; a thin header strip renders the current box's subscribed bars on every page with a box context; a `/app/progress` management page lists all bars and lets users subscribe/unsubscribe/delete. Bars are **global server-side**; subscriptions are **per-box and client-side only**.

## Pipeline

```
script (any host)
  → sshler progress push       (CLI — sshler/cli.py)
  → POST /api/v1/progress/:name (REST — sshler/api/progress.py)
  → state.upsert_progress       (SQLite — sshler/state.py)
  → broadcaster.broadcast       (per-app — sshler/webapp.py)
  → /ws/progress connected clients
  → progress store reducer      (frontend/src/stores/progress.ts)
  → <ProgressStrip> + <ProgressView>
```

REST and WS are decoupled: a client can still GET `/api/v1/progress` if it never connects to the WS. The WS is for live updates only.

## REST contract

| Method | Path | Body / Response |
|---|---|---|
| POST | `/api/v1/progress/:name` | `{current, total, color?, label?, status?, metadata?, merge?}` → `ProgressBar` |
| GET | `/api/v1/progress` | `{bars: ProgressBar[]}` |
| GET | `/api/v1/progress/:name` | `{bar: ProgressBar}` or 404 |
| DELETE | `/api/v1/progress/:name` | `{ok:true, removed:bool}` |

Name regex: `^[A-Za-z0-9._:-]{1,64}$`. Status closed-enum: `{running, done, failed, cancelled}` (default `running`). Total must be `> 0`. All routes gated by `Depends(deps.require_token)` — same `X-SSHLER-TOKEN` header as the rest of the API.

`ProgressBar` also carries `metadata: dict` and `metadata_error: str | None` (see **Metadata**).

## WebSocket protocol

`/ws/progress` — auth mirrors `/ws/term` (`?token=…` query param).

On connect, the broadcaster sends one **snapshot** event:

```json
{"type": "snapshot", "bars": [ProgressBar, ProgressBar, ...]}
```

Subsequently, the broadcaster fans out one event per state-changing REST call:

```json
{"type": "upsert", "name": "build", "bar": ProgressBar}
{"type": "delete", "name": "build", "bar": null}
```

Reducer rules in `_handleEvent` (`stores/progress.ts`):
- **snapshot** — replaces `bars` wholesale
- **upsert** — merges (`{...bars.value, [name]: bar}`)
- **delete** — drops the key from `bars.value`

## No auto-dismiss

Bars are **never auto-hidden**. A subscribed bar stays on the strip/dock until the user unsubscribes (client-side) or deletes it (REST). This applies to every status, including `done` — the green-check completed state is the payoff of subscribing, so it persists. `failed`/`cancelled`/`running` likewise stay. (Earlier versions auto-hid `done` bars after 10s; that surprised users who subscribed specifically to watch a task finish, so it was removed.)

## Metadata (fault-tolerant)

Scripts may attach an arbitrary JSON-object `metadata` bag to a bar. The contract is built so a script that botches its metadata **never** breaks the bar — progress (`current/total/status`) always applies; bad metadata only surfaces as `metadata_error`.

Combine rules (resolved atomically in `state.upsert_progress` under `_DB_LOCK`):

| Push contains | Effect on the bag |
|---|---|
| no `metadata` field | **untouched** — a 3300-tick loop sending only `current/total` never wipes earlier metadata |
| `metadata: {...}` | **replaces** the bag (default), clears `metadata_error` |
| `metadata: {...}` + `merge: true` | shallow-**merges** into the existing bag |
| `metadata: {}` | clears the bag |
| malformed metadata | keeps the last-good bag, sets `metadata_error`, **still applies progress** |

`_validate_metadata` (in `api/progress.py`) is lenient on purpose — `APIProgressPush.metadata` is typed `Any` so junk never 400s. Rejection reasons (→ `metadata_error`, HTTP still 200): not a JSON object, `> 4096` serialized bytes, `> 32` keys. `apply_metadata = "metadata" in body.model_fields_set` distinguishes "omitted" from "explicit `{}`".

The tooltip on each strip bar renders the metadata key/value rows and, when set, a red `metadata error: <reason>` line.

## Display: floor, never round up

Displayed percent is **floored** everywhere (`Math.floor` in `ProgressStrip.vue`/`ProgressView.vue`, `int(...)` in `cli.py`). A 3300-step build at 3299/3300 reads **99%**, not 100% — 100% appears only when `current >= total`. The bar-fill width still uses the exact fraction; only the number is floored.

## Finish blink

When a subscribed bar transitions **into** `done` (prev status was something else), `ProgressStrip.vue` flashes it bright white once via the `strip-bar--flash` class + `@keyframes strip-flash`, cleared on `animationend`. The status watcher is `immediate: true` so a bar already running at mount records its status and the very first running→done transition still flashes. Bars already `done` at mount do not flash.

## Reconnect backoff

`RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]` ms. `backoffIndex` advances on each unintentional close, resets on `onopen`. `disconnect()` sets `intentionalDisconnect = true` so the reconnect doesn't fire after explicit teardown.

## Subscription model (per-box scoped)

Subscriptions are **client-side only** and **scoped per box**. Storage is `localStorage["sshler:progress:subscribed"]` as a JSON object: `{ "<boxName>": ["name1", "name2"], ... }`. The scope key is `appStore.activeBox`. The server has no concept of subscriptions at all — bars are global server-side; only the client's *view* of them is scoped.

Implications:
- `sshler` and `maintenance` (boxes) show different subscribed bars. Switching the active box swaps the rendered set.
- `subscribe(name)` / `unsubscribe(name)` / `isSubscribed(name)` operate on the **current scope** (`currentScope = appStore.activeBox`). When `currentScope === null` (Settings, /app/progress, any route with no box), they are **no-ops** and `subscribedBars` is empty.
- A re-pushed bar with a name subscribed in the current box **reappears automatically** — the subscription survives server-side deletes.
- Unsubscribing is instant (no server roundtrip).
- **Old format migration:** the previous global shape was a flat `string[]`. On hydration, if a flat array is found it is silently discarded (single-user tool — re-subscribing once is acceptable). No back-compat path.

### Rendering surfaces

| Surface | Component | What it shows |
|---|---|---|
| Header strip | `<ProgressStrip>` | Very-thin strip under `<AppHeader>`. Renders only when a box is active. Shows the current box's subscribed bars + a `+` button that opens the picker. This is the **only** live display surface. |
| Management page | `<ProgressView>` at `/app/progress` | Lists `allBars` (global pool). Per-row subscribe switch toggles within the *current* box scope; switches are disabled when no box is active. Shows a "Subscriptions for {box}" banner. |
| Picker modal | `<ProgressPicker>` | Opened from the strip's `+`. Lists `allBars` with checkboxes; toggling subscribes/unsubscribes in the current box scope. |

(There was previously a floating bottom dock; it was removed in favor of the strip alone.)

## CLI

`sshler progress push/list/delete` in `sshler/cli.py`.

`push` metadata flags: `--meta KEY=VALUE` (repeatable, string values), `--meta-json '{...}'` (raw JSON object; a parse failure warns to stderr and pushes **without** metadata rather than blocking the bar), `--merge` (merge instead of replace), `--clear-meta` (send `metadata: {}`, wins over `--meta*`). Metadata is only added to the request body when one of these flags is supplied, so plain ticks don't wipe the bag.

Token discovery chain:
1. `--token TOKEN`
2. `$SSHLER_TOKEN`
3. `<config_dir>/runtime-token` (written by `serve()` at startup, mode 0600)
4. Exit 2

URL discovery: `--url` → `$SSHLER_PROGRESS_URL` → `http://127.0.0.1:8822`.

`httpx` is in **core** deps (not `dev`) because the CLI uses it as a sync client. `pyproject.toml` reflects this.

## Frontend file map

| File | Purpose |
|---|---|
| `frontend/src/api/types.ts` | `ProgressBar`, `ProgressEvent`, `ProgressStatus`, list/delete result types |
| `frontend/src/api/http.ts` | `fetchProgress(token)`, `deleteProgress(name, token)` |
| `frontend/src/stores/progress.ts` | Reducer, WS lifecycle, **per-box `subscriptionsByScope` + `currentScope`** |
| `frontend/src/components/ProgressStrip.vue` | Thin per-box strip under AppHeader; `+` opens the picker |
| `frontend/src/components/ProgressPicker.vue` | Modal: all bars with checkboxes, scoped subscribe |
| `frontend/src/views/ProgressView.vue` | `/app/progress` management page (scope banner + scope-disabled switches) |
| `frontend/src/locales/{en,ja}.ts` | `nav.progress`, `progress.*` keys (incl. `progress.picker.*`, `progress.strip.*`, `progress.scope.*`) |
| `frontend/src/App.vue` | Mounts `<ProgressStrip />` (under header) and **owns the WS lifecycle** (connect + refresh on mount, disconnect on unmount) |
| `frontend/src/router/index.ts` | `/progress` lazy route |
| `frontend/src/components/AppHeader.vue` | Nav entry with `PhChartBar` icon, `Alt+P` shortcut |

## Backend file map

| File | Purpose |
|---|---|
| `sshler/state.py` | `ProgressBar` SQLerModel + sync/async CRUD wrappers |
| `sshler/api/progress.py` | REST router, Pydantic validation, broadcaster wiring |
| `sshler/api/dependencies.py` | `APIDependencies.broadcast_progress` callable slot |
| `sshler/webapp.py` | `ProgressBroadcaster` class, `/ws/progress` endpoint, lifespan wiring |
| `sshler/cli.py` | `progress` subparser, runtime-token cache write in `serve()` |

## Do not refactor away

These are load-bearing — they look like over-engineering, they aren't:

- **Broadcaster is per-app, not module-global.** Tests instantiate multiple apps; a global broadcaster would leak WS connections between them and cause spurious test failures.
- **`broadcast_progress` is plumbed via `APIDependencies`**, not imported directly. The alternative is a circular import between `webapp.py` and `api/progress.py`.
- **Snapshot-on-connect is mandatory.** Without it, a tab that opens after a push misses every bar that existed before it connected; `refresh()` on mount is a belt-and-suspenders fallback for the case where the snapshot WS message races the page load.
- **`connect()` is idempotent.** `App.vue` owns the WS lifecycle (connect + refresh on mount, disconnect on unmount). `ProgressView` also calls `connect()` on mount; it does not disconnect on unmount because `App.vue` is the stable owner. The strip is `v-if`-gated on box context, so it is NOT a safe lifecycle host — it would tear the socket down on every box→no-box navigation.

## Testing patterns

Backend (`tests/test_api_progress.py`, `tests/test_progress_websocket.py`): TestClient does NOT auto-run FastAPI lifespan, so the `build_client()` helper calls `state.reset_state(); state.initialize(config_dir)` before creating the client. WS tests use `client.websocket_connect()`.

CLI (`tests/test_cli_progress.py`): monkeypatches the `httpx.Client` factory with `httpx.MockTransport` for stub responses. `tests/test_serve_token_cache.py` covers the `<config_dir>/runtime-token` write.

Frontend store (`frontend/src/stores/progress.spec.ts`): `MockWebSocket` class with `fireOpen/fireMessage/fireClose` hooks; reconnect-backoff tests use `vi.useFakeTimers`.
