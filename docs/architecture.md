# Architecture notes

What you need to know before changing one part of sshler. `CLAUDE.md` names the part and
points here; each section stands alone.

## Terminal socket

**WebSocket terminal flow:**
1. Client calls `/api/v1/terminal/handshake` for connection info
2. Client connects to `/ws/term?host=...&dir=...&session=...&token=...`
3. `terminal_socket` checks access and the session name, then `webapp.py` opens one transport from `sshler/terminal/`: `LocalPtyTransport` (tmux client on a local PTY), `SshTransport` (tmux over its own SSH connection) or `ConPtyTransport` (a tab of a persisted native Windows shell). Each keeps the contract in `terminal/transport.py` (read, write, resize, close) and knows nothing of the WebSocket
4. `terminal/pump.py` `run_pump` carries binary output and input, JSON control messages, the tmux window list and pings, and knows nothing of which transport it drives; when the terminal ends first it closes the socket (1000 `shell exited`, or 1011 `terminal connection lost` when the transport failed, so the client reconnects)
5. A terminal that cannot open is a `TerminalOpenError(message, code, reason)`, answered only in `_serve_terminal`: the message is sent as text, then the socket closes with the code. The close codes: 4400 a refused session name, 4401 credentials, 4403 the token or the Origin, 4404 a box the server does not know, 4501 tmux is not installed on the box, 4502 WSL is not installed, 4503 pywinpty is missing, 4504 too many terminals. A box is asked for tmux before anything is started: the local box by `require_local_tmux` (`api/sessions.py`), a remote box by one command (`remote.require`, sent as `sh -c` so any login shell can read it) on the terminal's own connection, where only status 127 refuses and a check that cannot run opens the terminal. A tmux that cannot be started answers "Connection failed" and an ordinary close on both kinds. The page's words for each code are in `frontend/src/utils/terminalCloseCodes.ts`, and the page reconnects after any code not named there: a new code is named there in the same change
6. `_serve_terminal` has the one teardown: `transport.close()` runs however the session ends, and leaves no task, child, channel or descriptor behind (`tests/test_terminal_endings.py`; the contract is proved for every transport by `tests/test_terminal_transport_contract.py`)

## A command on a box, and the files of a box

No route in `sshler/api/` asks which kind of box it holds. It asks one of two interfaces, and
each has a local and a remote implementation:

- **Files:** `BoxFiles` (`api/file_contract.py`), opened with `box_files(deps, box, config)`
  (`api/box_files.py`), kept by `LocalFiles` and `RemoteFiles`. Listing, reading, writing,
  placing under the existing-name rule, batches, archives, the folder zip.
- **Programs:** `BoxRunner` (`api/box_runner.py`), opened with `box_runner(deps, box, config,
  deadline=...)`. `run(words, folder=, timeout=)` for git and grep, `tmux(session, args)`,
  `folder`/`file` for paths, `reach` for the status check. Its module docstring is the
  contract: every word reaches the program as one word, what a time limit does to the
  program and to a pooled connection, and where the two kinds differ.

Work the two kinds really do differently sits in one function that decides once and returns
the same shape for both: `box_stats` (`api/boxes.py`), `live_sessions` and
`force_kill_supported` (`api/sessions.py`), `search_box` (`api/search.py`), and the in-process
grep of a Windows local box (`api/grep.py`). `tests/test_box_files_contract.py` counts the
tests of the kind per route module, so a new one outside such a function fails there.

A remote path with `~`, a remote command line built from words, and an SFTP session each
have one owner: `remote_path` and `command` in `api/remote.py`, `sftp_session` in `ssh.py`.

**A failed request** answers a failure status and `{"detail": text, "reason": word}`, written
by one owner, `api/failure.py`. `STATUS_OF_REASON` there is the list of reasons, each with its
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

The bootstrap says who is asking: `auth_required` (a password is set) and `authenticated`
(this request carried a live session or a good Basic header, always true with no password);
a request that is not authenticated gets `token: null` and only what the sign-in page needs (the version, token header, page base, platform and the two auth flags; no allowed origins, shell, PDF or artifact settings). The router's guard asks the bootstrap
when the page has none (on load, and after sign-out forgets it) and sends a signed-out page to
the sign-in route with `redirect`. The header's polls and the app's sockets start only while the bootstrap store's `signedIn` is true, and a refusal reads the route only after the router's first navigation has ended, so neither can replace the guard's `redirect`. A good sign-in loads the page afresh at `redirect`
(`utils/pageLoad.ts`), so the bootstrap gives the token and every socket opens signed in. The
settings view's sign-out row calls the sign-out route, forgets the user, the bootstrap and the
token, and loads the sign-in page. A session that ended answers 401 `auth` on the next
request, and `handleAuthErrors` forgets the user and the bootstrap and goes to the sign-in route. The page starts its sockets, progress bars and recovery poll only when it is signed in. One function, `live_session` in `session.py`, decides whether a session id is live (the configured idle timeout included) for the door, the sockets and the auth routes.

## Frontend details

**A request and its failure**: every request of the page goes through `request` in
`src/api/http.ts` (the JSON calls, the `http` object's verbs, downloads, the PDF export, a
transfer's first answer, the terminal handshake, the start-up token check); the upload keeps
`XMLHttpRequest` for its progress and runs under the same policy. A failed request is an
`ApiError` (`src/api/apiError.ts`): `status` (0 when no answer came), `reason` (one of the
server's words, `network` when no answer came, null when an answer had none) and `detail` (the
server's text), and `retryAfter` (the answer's `Retry-After` in seconds, or null; the sign-in
page shows a lockout's wait from it). Its `message` is the translated line of the reason (`error.reason.<word>`,
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
