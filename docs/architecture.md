# Architecture notes

What you need to know before changing one part of sshler. `CLAUDE.md` names the part and
points here; each section stands alone.

## Terminal socket

**WebSocket terminal flow:**
1. Client calls `/api/v1/terminal/handshake` for connection info
2. Client connects to `/ws/term?host=...&dir=...&session=...&token=...`
3. `terminal_socket` checks access and the session name, then `webapp.py` opens one transport from `sshler/terminal/`: `LocalPtyTransport` (tmux client on a local PTY), `SshTransport` (tmux over its own SSH connection) or `ConPtyTransport` (a tab of a persisted native Windows shell). Each keeps the contract in `terminal/transport.py` (read, write, resize, close) and knows nothing of the WebSocket
4. `terminal/pump.py` `run_pump` carries binary output and input, JSON control messages, the tmux window list and pings, and knows nothing of which transport it drives; when the terminal ends first it closes the socket (1000 `shell exited`, or 1011 `terminal connection lost` when the transport failed, so the client reconnects)
5. `_serve_terminal` has the one teardown: `transport.close()` runs however the session ends, and leaves no task, child, channel or descriptor behind (`tests/test_terminal_endings.py`; the contract is proved for every transport by `tests/test_terminal_transport_contract.py`)

## Frontend details

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

### Mobile terminal UX

`components/MobileInputBar.vue` (quick keys + `?` legend; each key has a fixed colour meaning, red = Ctrl+C), `components/Terminal.vue` (xterm wrapper, mobile viewport), `components/AppHeader.vue` (14px mobile header with CPU/MEM stats), `composables/useResponsive.ts`.

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
- `ts_session_name(dir)` in `sshler/tmux.py` matches the `ts` CLI byte-for-byte (basename, `.`/`:`→`_`, no hash); frontend `generateSessionName` must match it for every box. Remote boxes use the same per-session `tmux -L ts-<name>` servers (`remote_tmux_command`), so `ts` after a plain `ssh host` attaches the session sshler opened. tmux helpers live in `tmux.py` to avoid circular imports.
- Resume command is a client-side template with `{id}` (global + per-session overrides in localStorage); the server (`_resolve_resume_command`) requires `{id}`, rejects control chars, substitutes the validated UUID.
