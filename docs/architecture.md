# Architecture notes

What you need to know before changing one part of sshler. `CLAUDE.md` names the part and
points here; each section stands alone.

## Terminal socket

**WebSocket terminal flow:**
1. Client calls `/api/v1/terminal/handshake` for connection info
2. Client connects to `/ws/term?host=...&dir=...&session=...&shell=...&token=...`. On a POSIX local box and on a remote box `shell` is `""` or `tmux` (tmux, the default) or `plain` (a plain shell, no tmux); a Windows local box takes its own shell ids and opens its default shell for one it does not know
3. `terminal_socket` checks access (`access.websocket_refusal`, see "Refusing a socket" below), the session name (also for a plain shell: the door has one rule) and `shell` (`_shell_refused`), then `webapp.py` opens one transport from `sshler/terminal/`: `LocalPtyTransport` (a tmux client, or the user's login shell, on a local PTY started by `spawn_on_pty`), `SshTransport` (tmux, or the login shell, on a PTY over its own SSH connection) or `ConPtyTransport` (a tab of a persisted native Windows shell). A plain shell is the same transport with `tmux` None: no window list, key bindings or ts history, and no session row (it ends with its socket, so the session routes have nothing to list, sync, rename or kill). Each keeps the contract in `terminal/transport.py` (read, write, resize, close) and knows nothing of the WebSocket. The local plain shell is the one tmux would start (`terminal/local.py` `login_shell`: `$SHELL`, else the password database's, else `/bin/sh`), as a login shell in the folder; the remote one is `$SHELL -l` (else `/bin/sh`) in the folder (`remote.login_shell_in`), left in the login folder when the folder cannot be entered
4. `terminal/pump.py` `run_pump` first sends `{"op": "shell", "kind": "tmux" | "plain", "fallback": bool}` once, before any output (`kind` is `plain` when the transport has no tmux, a Windows native shell too; `fallback` is true only when tmux was wanted and the box has none). It then carries binary output and input, JSON control messages, the tmux window list and pings, and knows nothing of which transport it drives; when the terminal ends first it closes the socket (1000 `shell exited`, or 1011 `terminal connection lost` when the transport failed, so the client reconnects). The server writes no notice into the terminal: the page says what a fallback means: `Terminal.vue` reads the frame (`isNativeShell` follows it), prints one line in the page's language on `fallback: true`, and one line on a reconnect that opened a new plain shell
5. A terminal that cannot open is a `TerminalOpenError(message, code, reason)`, answered only in `_serve_terminal`: the message is sent as text, then the socket closes with the code. The close codes: 4400 a refused session name (reason `Invalid session name`: empty once cleaned, or longer than 255 characters, a folder name's limit) or `shell` value (reason `Invalid shell`), 4401 credentials, 4403 the token or the Origin, 4404 a box the server does not know, 4502 WSL is not installed, 4503 pywinpty is missing, 4504 too many terminals. A box is asked for tmux before anything is started, unless a plain shell was asked for: the local box by `require_local_tmux` (`api/sessions.py`), a remote box by one command (`remote.require`, sent as `sh -c` so any login shell can read it) on the terminal's own connection. A box without tmux (the local check fails, or the remote check ends with status 127) gets a plain shell in its place; a remote check that cannot run opens tmux. A terminal that cannot be started answers "Connection failed" and an ordinary close on both kinds. The page's words for each code are in `frontend/src/utils/terminalCloseCodes.ts`, and the page reconnects after any code not named there: a new code is named there in the same change
6. `_serve_terminal` has the one teardown: `transport.close()` runs however the session ends, and leaves no task, child, channel or descriptor behind (`tests/test_terminal_endings.py`; the contract is proved for every transport by `tests/test_terminal_transport_contract.py`)

**Refusing a socket** (`/ws/term`, `/ws/progress`, `/ws/ping`): `access.websocket_refusal` is the one owner of who may connect and how a refusal is sent; each handler passes its answer to `access.refuse`. The code follows the order credentials (4401 `Unauthorized`), token (4403 `Invalid token`), Origin (4403 `Invalid origin`). How it is sent follows the Origin alone: a present Origin that is not allowed (`null` too) is refused before the handshake, whatever else is wrong, so uvicorn answers HTTP 403 and a cross-site page never gets an upgraded socket. Every other refusal, and the session-name and `shell` refusals of `/ws/term`, is accepted and closed at once with its code and reason, with nothing read and nothing started, because a close before the accept reaches a browser as 1006 with no reason (and the page would retry instead of showing the refusal or going to sign-in). Starlette's test client shows a close before the accept with its code, so only a real server shows the difference: `tests/e2e/test_terminal_websocket.py` and `tests/e2e/test_sign_in.py` read the code in a browser and the status line of a raw upgrade.

**What a session row's `active` means:** a tmux session's row is active exactly while tmux has that session, whether or not a tab is open on it. One function applies the rule, `session_activity.match_tmux`, and every writer that learns what tmux has calls it: the socket's end, the sync route (both ways), the startup reconcile (every local row), the snapshot loop and a dismiss of a lost session. What tmux has is one answer, `tmux.LiveSessions`: the sessions seen, and whether every server asked answered (a socket with no server behind it answers "none"; a server past its limit, or a tmux that could not be run, is not asked). A session seen is running; a session not seen while a server could not be asked is left as its row says, never written inactive. The local box asks in `tmux.py` (`discover_local_sessions` for every server, `ask_local_session` for one session's own `ts-<name>` server and the default server); a remote box runs `REMOTE_LIST_SESSIONS` (`api/sessions.py`), which says by its exit status that a server could not be asked. The socket's end asks about its own session only (`live_sessions_on`, two tmux commands locally, one command on the terminal's own connection remotely); when the box cannot be asked at all the row is left as it was. **Sessions started outside sshler:** every listing, local and remote, reads one format (`tmux.LIST_FORMAT`, read by `tmux.parse_listing`, one tmux command per server), so `LiveSessions.folders` names the folder of each session NAME that sits on its own server `ts-NAME` (what `ts` makes and what a terminal can open; a session of the default server, or of a `ts-` server of another name, is in the names only; it needs `#{socket_path}`, and a tmux without it gives no folders). After `match_tmux` the sync route makes a row for each such session that has none (`session_activity.track_live_sessions`, through `track_session`, the one maker of a row that a terminal's open uses too): active, in that folder, with the box's `transport`; a row that exists keeps its folder. From then on it is an ordinary row. A session whose folder holds a newline is seen (its row stays active) but gets no folder, so no row is made for it. The sync matches every row of the box and answers every active one, with the most recent inactive ones while fewer than 50 are active (`_sync_answer`). No row is made for the local box of a Windows server. A persisted Windows shell's row is its registry's to mark; a plain shell has no row. Every list of a box's sessions on the page loads by the sync route (the overview's card through `stores/sessions.ts`, the session switcher on its own); when the sync fails the switcher lists the stored rows with the failure's line (`tests/test_session_active_flag.py`)

**Recovery: what "lost" means.** A session is lost when it ended while sshler was not running to see it end (a reboot, a crash, an end while sshler was stopped): the start's `snapshot.reconcile_on_startup` offers it in the recovery dialog. An end the running server sees (the snapshot loop, a terminal socket's end, the sync route) is recorded on the row by `match_tmux` alone (`state.END_SEEN_AT`, with the time; the start's own check records nothing, and a row active again drops it), and such a session is never in the dialog. Its row keeps its last layout for `state.SNAPSHOT_KEEP_DAYS` and answers `restorable` (`snapshot.saved_layout`); the box card's session list and the session switcher offer "Restore layout" on it, which is the dialog's recreate (`POST /recovery/{id}/recreate`, `recovery._recreate`): the windows and splits in their folders and a line naming each pane's last command, never the command run again. Kill and Force kill delete the row, so neither offers anything.

## A command on a box, and the files of a box

No route in `sshler/api/` asks which kind of box it holds. It asks one of two interfaces, and
each has a local and a remote implementation:

- **Files:** `BoxFiles` (`api/file_contract.py`), opened with `box_files(deps, box, config)`
  (`api/box_files.py`), kept by `LocalFiles` and `RemoteFiles`. Listing, reading, writing,
  placing under the existing-name rule, batches, archives, the folder zip.
  A write, a new file, an upload, a copy, a move and a transfer make the missing folders of
  their destination on both kinds of box (a remote box through `_remote_make_missing`).
  **A taken name** (`api/placement.py`): without a word from the request it is refused;
  `overwrite` lets a regular file replace a regular file; `keep_both` (rename, move, copy,
  upload, batch move and copy, transfer, compress, extract) neither replaces nor refuses: the new thing lands
  under the first free name of one series, `free_name` (`report (2).pdf`, `site (2).tar.gz`,
  `photos (2)`, at most 255 bytes, up to `(999)`, then `exists`). Every name is taken by
  the exclusive call that writes (an exclusive create, a hard link, a new folder), tried on
  each name `candidate_paths` gives while it answers that the name exists, so two requests
  at once never get one name. Both words together are `invalid`
  (`refuse_replace_and_keep_both`). The answer's `path` is where the thing landed; a batch
  answer with `keep_both` adds `placed` (`{"path", "placed"}` for each item that landed
  under another name), and a transfer's `progress` events for such a file carry `placed`.
  An extract with `keep_both` checks no entry against a taken name (the guard against
  unsafe entries runs as always): its unpacked temp folder is placed whole as
  a new folder named by `archive_folder_name` (`backup.zip` gives `backup/`, then
  `backup (2)/`), never merged into an existing one; its `path` is that folder.
- **Programs:** `BoxRunner` (`api/box_runner.py`), opened with `box_runner(deps, box, config,
  deadline=...)`. `run(words, folder=, timeout=)` for git and grep, `tmux(session, args)`,
  `force_kill(session)` (the session's own `ts-<name>` server stopped and every pane process
  it listed sent SIGKILL, on both kinds),
  `folder`/`file` for paths, `reach` for the status check. Its module docstring is the
  contract: every word reaches the program as one word, what a time limit does to the
  program and to a pooled connection, and where the two kinds differ.

Work the two kinds really do differently sits in one function that decides once and returns
the same shape for both: `box_stats` (`api/boxes.py`), `live_sessions`
(`api/sessions.py`), `search_box` (`api/search.py`), and the in-process
grep of a Windows local box (`api/grep.py`). `tests/test_box_files_contract.py` counts the
tests of the kind per route module, so a new one outside such a function fails there.

A remote path with `~`, a remote command line built from words, and an SFTP session each
have one owner: `remote_path` and `command` in `api/remote.py`, `sftp_session` in `ssh.py`.

**A failed request** answers a failure status and `{"detail": text, "reason": word}`, written
by one owner, `api/failure.py`; a refusal about many names of one request (an extract onto
files that exist) adds `"taken": {"count": n, "names": [the first five]}` (`Failure(...,
taken=Taken(...))`). `STATUS_OF_REASON` there is the list of reasons, each with its
one status. Code that refuses a request raises `Failure(reason, text)`; `failure_of` names the
reason of any other error by its type (errno, SFTP type), never by its words, the same for
both kinds of box; `failure_entry` gives an item of a batch the reason a single request would
answer; `failure_body` is the body itself, which the `error` event of a cross-box transfer
carries too; `answer` writes the body for a check that runs before any route. No route
picks a status: no `raise HTTPException(` is left under `sshler/`. Every failure
answer the suite's test client receives is checked by `tests/failure_check.py`.

Each group of routes has its table of failures, every row an exact status, reason and
text: the file routes (`tests/test_failure_reasons.py`, run on both kinds), the routes with
no box behind them such as tunnels, artifacts, PDF export and the login
(`tests/test_route_failure_reasons.py`), the terminal socket's closes
(`tests/test_terminal_closes.py`), and the routes that run a program on a box: git, the git badge, grep, the
session sync, rename and capture, a box's stats and directory search
(`tests/test_box_program_failure_reasons.py`). For those the runner raises one type per
cause on both kinds: `ProgramNotFoundError` for a program that is not installed (a remote
shell's status 127), `ProbeTimeoutError` for a connect that timed out (`unreachable`),
`CommandTimeoutError` and `TmuxTimeoutError` for work past its limit on a reached box
(`timeout`); `box_runner` turns every failure in its block into its `Failure`. A failure is
never a 200 with an empty result: "not a repository", an empty repository, "no matches" and
"no sessions" are the ordinary answers, and a box that cannot be asked is a failure. Three
answers stay a 200 and carry `error` and `reason` in the body, because they are about a
probe or about several things: a box's status (`offline`), an event of the stats stream for
a box that failed, and a directory search whose walk of the box did not run (it still returns
the folders visited before). An answer about several things has one shape: `status` (`ok` or
`partial`), `succeeded`, and `failed` as `failure_entry` items; the file batch routes and the
batch recreate of lost sessions answer it, and the batch recreate shares the single route's
failures through `recovery._recreate`. A route outside these that raises a plain
`HTTPException` gets its reason from its status.

## Signing in

With a password set (`--auth`, or `SSHLER_USERNAME` with `SSHLER_PASSWORD_HASH` or
`SSHLER_PASSWORD`), the page's own sign-in form is the way in. Who passes the server's door is
decided by one function, `door_of` in `webapp.py`, by exact method and the decoded path the
router matches; `_security_middleware` only asks it. Open without credentials: OPTIONS and
`/health` (untouched); GET and HEAD of `/`, `/app` and the paths below `/app/` (the page's
files, no secret in them); GET `/api/v1/bootstrap`, GET `/api/v1/auth/me` and POST
`/api/v1/auth/logout`; POST `/api/v1/auth/login`, which a lockout still stops. A path with a
`.`, `..` or empty segment is never open, so `/app/../api/v1/boxes` and `/appx` need
credentials. Everything else needs a live session cookie or a good Basic header. A refusal is
401 `auth` with no `WWW-Authenticate` header, so no browser shows its own prompt or keeps
the password. A Basic header passes that one request and makes no session: only the sign-in
route makes a session and its cookie (8 hours, `SSHLER_SESSION_TTL_SECONDS`). A wrong Basic header
counts toward the lockout (five failures, five minutes) as a failed sign-in does; a lockout
answers `rate_limited` with `Retry-After` on every closed path and on the sign-in route, and
the other open routes keep loading so the form can say how long to wait. Sockets decide the
same credentials in `access.websocket_refusal` (close 4401).

A socket let in on a session cookie belongs to that session until it closes: after accepting
it, each socket handler calls `access.hold_socket`, which records it in `SessionStore` (the
store judges the session live and records the socket in one step, so a sign-out between the
check and the record refuses the socket with 4401). Every end of a session, a sign-out from
any tab, an expiry a request finds, or the timer each held socket sets for its session's
deadline (no request needed; it reads the store's clock again at least every 30 seconds, so
time that passed while the machine slept counts), closes each of its sockets with 4401; a terminal then ends as
on any close, its attach process (or SSH channel) closed and the tmux session left running.
A socket let in by a Basic header, or with no password set, belongs to no session. In the
page, a 4401 close of a terminal, progress or ping socket goes to `handleAuthErrors`, as a
401 does, and the progress and ping stores stop reconnecting.

The bootstrap says who is asking: `auth_required` (a password is set) and `authenticated`
(this request carried a live session or a good Basic header, always true with no password);
a request that is not authenticated gets `token: null` and only what the sign-in page needs (the version, token header, page base, platform and the two auth flags; no allowed origins, shell, PDF or artifact settings). The router's guard asks the bootstrap
when the page has none (on load, and after sign-out forgets it) and sends a signed-out page to
the sign-in route with `redirect`. The header's polls and the app's sockets start only while the bootstrap store's `signedIn` is true, and a refusal reads the route only after the router's first navigation has ended, so neither can replace the guard's `redirect`. A good sign-in loads the page afresh at `redirect`
(`utils/pageLoad.ts`), so the bootstrap gives the token and every socket opens signed in. The
settings view's sign-out row calls the sign-out route, forgets the user, the bootstrap and the
token, and loads the sign-in page. A session that ended answers 401 `auth` on the next
request, and `handleAuthErrors` forgets the user and the bootstrap and goes to the sign-in route. The page starts its sockets, progress bars and recovery poll only when it is signed in. One function, `live_session` in `session.py`, decides whether a session id is live (the configured idle timeout included) for the door, the sockets and the auth routes.

## The state store

`sshler/state.py` keeps favourites, sessions, directory visits, snippets, layouts, progress bars, notebooks and artifact projects and registrations in one SQLite file through sqler. The models are not bound to a database: every query is `Model.using(db)` with the database `_require_db()` returns, a new row is saved with `save(db=db)`, and a row read through `using(db)` knows its own database. To add a model: write the class beside the others (with `__tablename__`); add one `_Table` row to `_TABLES` naming its single-field indexes (and any composite index as raw SQL), which is all `initialize()` needs; write each operation once as a sync function under `_DB_LOCK`, and give it an async form on the next line, `<name>_async = _in_thread(<name>)`.

## The event loop

The server runs on the stdlib event loop, named in one place (`_EVENT_LOOP` in `sshler/cli/server.py`), never uvloop: uvloop runs Python in the forked child of every subprocess it starts, and a thread-end hook there can block the child for good and the whole server with it. Start a local command with `run_bounded` (`sshler/proc.py`) or `spawn_on_pty`; never fork from Python.

## Frontend details

**A request and its failure**: every request of the page goes through `request` in
`src/api/http.ts` (the JSON calls, the `http` object's verbs, downloads, the PDF export, a
transfer's first answer, the terminal handshake, the start-up token check); the upload keeps
`XMLHttpRequest` for its progress and runs under the same policy. A failed request is an
`ApiError` (`src/api/apiError.ts`): `status` (0 when no answer came), `reason` (one of the
server's words, `network` when no answer came, null when an answer had none) and `detail` (the
server's text), `retryAfter` (the answer's `Retry-After` in seconds, or null; the sign-in
page shows a lockout's wait from it) and `taken` (the server's count and first names of a
refusal about many names, or null; an extract asks about them once with `askReplaceMany`). Its `message` is the translated line of the reason (`error.reason.<word>`,
`error.reason.network`, `error.reason.none`, in both locales), then " — " and the detail; an
answer with no reason and no text shows its status in brackets instead. The page acts on the
reason only, never on the status or the words: `token` refreshes the token once and sends once
more, never a second time and never for the bootstrap call; `auth` goes to the sign-in route
through the router (`handleAuthErrors`), and does nothing on the sign-in page itself; anything
else is thrown at once. "Replace?" is asked on `exists`, the diff view's missing side is
`not_found`. One rule reads the status on purpose: the upload retries a 5xx or no answer, which
is about retrying, not about what failed. `FAILURE_REASONS` there is the server's list
(`tests/test_page_failure_reasons.py` fails when the two differ).

**A reader of a request shows its failure.** A catch around a request shows the `ApiError`'s
`message` in the element where the empty result would show (the git history panel, the two
searches, the session switcher) or as an error message, so "nothing found" is only ever an
answer that came. A catch may stay silent only with a one-line comment that says why: a poll
that asks again in a moment (it must never stack messages) or a decoration such as the git
badge. An `{error, reason}` pair inside a 200 (a box's status, a stats event, a search whose
walk did not run, a failed item of a batch) is read by one function, `failureText` in
`apiError.ts`, which gives the words an `ApiError` of that reason and text has. A search writes
only the newest answer; one replaced by typing on shows nothing. The terminal's close codes
and their lines are in `utils/terminalCloseCodes.ts`; the page reconnects after any code not
named there, so a close that would repeat (4400, a refused session name) is named.

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
- `sshler/pdf.py` `PDF_RENDERER` launches Chromium in the lifespan; soft-fails to `available=False` without the `[pdf]` extra. `POST /api/v1/pdf/render` `{html, filename}` → PDF, 501 `program_missing` when unavailable.
- `bootstrap.pdfAvailable` gates every UI entry: hide when false, no disabled placeholders.
- HTML pipeline is `composables/usePrintableHtml.ts` (marked → DOMPurify → mermaid → image inlining); new entry points call `usePdfExport().exportOne/exportMany`, never re-roll it.

### Progress bars
- `ProgressBar` model in `state.py`; REST `/api/v1/progress[/:name]`, name `^[A-Za-z0-9._:-]{1,64}$`, status `{running, done, failed, cancelled}`. WS sends `snapshot` on connect, then `upsert`/`delete`.
- CLI token discovery: `--token` → `$SSHLER_TOKEN` → `<config_dir>/runtime-token` (0600, written by `serve()` in `sshler/cli/server.py`; path, write and read in `sshler/cli/runtime_token.py`; discovery in `sshler/cli/client.py`); URL: `--url` → `$SSHLER_PROGRESS_URL` → `http://127.0.0.1:8822`. That is why `httpx` is a core dep.
- The client commands (`progress`, `ping`, `artifact`) load no server: `sshler/cli/` builds every parser without importing `server.py` (uvicorn, the web app) or `password.py`, and `_main.py` imports one of them only when its command runs. `tests/test_cli_imports.py` pins the list of server modules a client command may load: none. `get_config_dir` lives in `sshler/config_dir.py` so the CLI does not import `sshler.config`.
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
