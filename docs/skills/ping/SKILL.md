---
name: sshler-ping
description: Reference for sshler's ping notification API — fire-and-forget push notifications to all connected browsers via WebSocket. Load when working on sshler/api/ping.py, stores/ping.ts, PingNotificationHandler.vue, or extending the ping event protocol.
---

# sshler — Ping Notifications

External scripts POST a ping; the server fans out to every connected `/ws/ping` client; each browser tab shows a Naive UI toast notification. Pings are **stateless and ephemeral** — no persistence, no snapshot on reconnect.

## Pipeline

```
script (any host)
  → sshler ping                  (CLI — sshler/cli.py)
  → POST /api/v1/ping            (REST — sshler/api/ping.py)
  → PingBroadcaster.broadcast    (per-app — sshler/webapp.py)
  → /ws/ping connected clients
  → ping store _handleEvent      (frontend/src/stores/ping.ts)
  → pendingPings queue
  → PingNotificationHandler.vue  (watches queue, calls useNotification())
  → Naive UI toast
```

## REST contract

| Method | Path | Body |
|---|---|---|
| POST | `/api/v1/ping` | `APIPingPush` → `{ok:true, id:string}` |

All routes gated by `Depends(deps.require_token)`.

### `APIPingPush` fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | `str` | ✅ | Max 200 chars. Notification headline. |
| `body` | `str \| null` | | Max 2000 chars. Notification body text. |
| `color` | `"success"\|"warning"\|"error"\|"info"\|null` | | Maps to Naive UI notification type. Default: `"info"`. |
| `icon` | `str \| null` | | Emoji shown as notification avatar (e.g. `"🚀"`). Max 8 chars. |
| `duration` | `int \| null` | | Auto-dismiss after N ms (1000–300000). `null` defers to the browser's global setting. |
| `source` | `str \| null` | | Label identifying the sender (e.g. `"deploy-bot"`). Shown as `meta` under the notification. |
| `metadata` | `dict \| null` | | Arbitrary key-value bag. Not displayed — for caller use only. |

### Response

```json
{"ok": true, "id": "aB3kR9"}
```

`id` is a `secrets.token_urlsafe(6)` — 8 URL-safe chars.

## WebSocket protocol

`/ws/ping` — auth mirrors `/ws/term` (`?token=…` query param or `X-SSHLER-TOKEN` header).

**No snapshot on connect** — pings are ephemeral. The client just holds the connection open and receives events as they arrive.

### Ping event

```json
{
  "type": "ping",
  "id": "aB3kR9",
  "title": "Deploy done",
  "body": "prod-01 finished in 3m20s",
  "color": "success",
  "icon": "🚀",
  "duration": null,
  "source": "deploy-bot",
  "metadata": {"run_id": 1234},
  "sent_at": 1748921234.123
}
```

## CLI

```bash
# Minimal
sshler ping --title "Hello"

# Full
sshler ping \
  --title "Deploy done" \
  --body "prod-01 finished in 3m20s" \
  --color success \
  --icon "🚀" \
  --duration 8000 \
  --source deploy-bot \
  --metadata '{"run_id": 42}'

# Token/URL from env
SSHLER_TOKEN=xxx SSHLER_PROGRESS_URL=http://myhost:8822 sshler ping --title "Hi"
```

Token discovery (same as `progress push`): `--token` → `$SSHLER_TOKEN` → `<config_dir>/runtime-token`.
URL discovery: `--url` → `$SSHLER_PROGRESS_URL` → `http://127.0.0.1:8822`.

## Frontend

### Store (`stores/ping.ts`)

- `connect(token)` — opens `/ws/ping`, exponential backoff reconnect (`[1s, 2s, 4s, 8s, 16s, 30s]`)
- `disconnect()` — intentional close, cancels reconnect timer
- `pendingPings` — `shallowRef<PingEvent[]>` queue; replaced on each new ping
- `drainPings()` — atomically returns and clears the queue

Lifecycle owned by `App.vue` (connect on mount, disconnect on unmount) — same as the progress store.

### Notification handler (`components/PingNotificationHandler.vue`)

Tiny renderless component inside `<NNotificationProvider>` in App.vue. Watches `pendingPings`, drains the queue, calls `notification.create()` for each ping.

Dismiss duration priority:
1. `ping.duration` (per-ping, set by caller)
2. `appStore.pingDefaultDuration` (global setting, `null` = manual dismiss)
3. `undefined` → Naive UI default (stays until user closes)

### Settings (`views/SettingsView.vue`)

"Ping Notifications" card — `NSwitch` toggles auto-dismiss on/off; `NInputNumber` sets the duration (ms). Stored in `localStorage["sshler:ping:default-duration"]` via `appStore.setPingDefaultDuration(ms | null)`.

## Backend wiring

- `PingBroadcaster` class in `sshler/webapp.py` — identical to `ProgressBroadcaster`, no state.
- Instantiated per-app in `make_app()`: `ping_broadcaster = PingBroadcaster(); deps.broadcast_ping = ping_broadcaster.broadcast`.
- `deps.broadcast_ping: Callable[[dict], Awaitable[None]] | None` in `sshler/api/dependencies.py`.
- Router included via `ping_router(deps)` in `sshler/api/__init__.py`.
- `/ws/ping` WebSocket registered directly on the FastAPI app in `make_app()`.

## TypeScript types

```typescript
// frontend/src/api/types.ts
export interface PingEvent {
  type: "ping";
  id: string;
  title: string;
  body?: string | null;
  color?: "success" | "warning" | "error" | "info" | null;
  icon?: string | null;
  duration?: number | null;
  source?: string | null;
  metadata?: Record<string, unknown> | null;
  sent_at: number;
}
```

## When extending

- **New ping field** → add to `APIPingPush` in `sshler/api/ping.py`, include in event dict, add to `PingEvent` in `types.ts`, handle in `PingNotificationHandler.vue`
- **New WS event type** → add a new `type` discriminant to `PingEvent` union in `types.ts`, dispatch in `ping.ts`'s `_handleEvent`, broadcast from `ping.py`
- **Persistent ping log** → would require adding a `PingRecord` SQLerModel in `state.py` (analogous to `ProgressBar`) and sending a snapshot on connect; current design is intentionally stateless
