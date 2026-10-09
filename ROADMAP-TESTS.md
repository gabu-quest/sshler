# Roadmap: Test suite hardening

## Goal

The test suites must be hermetic (never touch the developer's tmux, config or home), must
never hang, and every test must fail when the behaviour it names is broken. The static gates
(`mypy`, `ruff`, `vue-tsc`) and the `just` recipes must be real gates that pass.

## Milestones

### T1: Hermetic test infra and the websocket hang ✅
- [x] `_run_local_tmux_command` has a timeout and kills/reaps its child on timeout or cancel
- [x] `/ws/term` keeps its background tmux tasks and cancels them when the socket closes
- [x] `LocalPTYProcess.close()` reaps the forked child (no zombie tmux clients)
- [x] `_socket_dir()` honours `$TMUX_TMPDIR`
- [x] Autouse isolation fixture: config dirs, `TMUX`, `TMUX_TMPDIR`, all process-global singletons
- [x] No raw `os.environ[...]` writes in tests
- [x] Websocket tests drive a real PTY running `cat` instead of tmux and assert the bytes round-trip
- [x] Force-kill and E2E tmux use an isolated socket dir and no user tmux config
- [x] pytest config: `testpaths`, `e2e` marker, strict asyncio, 60 s per-test timeout
- [x] `just test` runs unit, frontend and E2E suites and reports every failure
- [x] Review fixes: every tmux subprocess bounded and reaped, server-starting commands never
  stall, large captures time out as 504, PTY fd and reap races closed

Acceptance: the former hang repro passes 20/20; the full backend suite passes 3/3; no new
`ts-*` sockets appear in the user's tmux socket dir during a run; each source fix has a
regression test that fails when the fix is reverted.

Result: met. Hang repro 20/20; backend unit 356 passed / 41 skipped on every
run; frontend 284/284; E2E 13/13; no new sockets; every source fix reverted one at a time
turns its regression test red. Two code reviews added: every tmux subprocess (not only
`run_local_tmux`) is bounded and reaped; server-starting commands run without captured
output; capture timeouts are a 504; the PTY master fd closes only after in-flight I/O;
reaping has one owner and works without `os.waitid`. The running sshler had leaked four
`tmux: client` zombies, which this fixes in production.

### T2: Security coverage ✅
- [x] Origin/CSRF middleware: hostile origin rejected on every state-changing method
- [x] Rate-limit middleware and auth lockout return 429 with `Retry-After`
- [x] Artifact sidecar refuses a symlink that escapes the registered root
- [x] Force-kill route calls the helper only with `force=true`; remote boxes are refused
- [x] Claude session ids reject trailing newlines and whitespace (`fullmatch`)
- [x] `/ws/term` rejects missing or bad tokens and sanitizes session names before building argv
- [x] Auth endpoint placeholders replaced by real tests or removed

Acceptance: each new test names the mutation it kills, and that mutation was shown to fail it.

Results: origin, rate-limit, `/ws/term` auth, artifact symlink and force-kill tests, each
with its mutation shown red, and a fix for `is_valid_session_id` accepting a UUID plus
newline. Two dead code paths found on the way (a WebSocket rate limit that never applied and
an unreachable login lockout) were deleted.

### T3: Weak assertions and timing ✅
- [x] Every or-chain and range assertion replaced by one exact expected value
- [x] Rate limiter and session expiry use an injected clock; no real sleeps
- [x] E2E tests assert the echoed marker and use no fixed sleeps
- [x] Platform-independent Windows terminal logic runs on Linux

### T4: Static gates ✅
- [x] `mypy sshler/` and `ruff check sshler tests` report 0 errors
- [x] `vue-tsc` reports 0 errors; `type-check` script exists; `just build` type-checks
- [x] `just lint` and `just ci` work
- [x] CI runs the static gates and a Windows job; publishing requires the test job (owner approved)

Results: mypy, ruff and vue-tsc are at zero and `just lint` / `just ci` run them; CI has a
static job and a Windows job, and PyPI publishing waits for the tests. The Windows CI job has
not run yet; its first run is on the next GitHub push. The review fixes that closed the round:
an upload onto an existing name now asks to replace it (default No) instead of reporting a
refused file as uploaded; the exists check never writes through a dangling symlink;
`X-Real-IP` is trusted only with `SSHLER_TRUST_PROXY_HEADERS=true`. GitHub's Windows terminal
tabs commit is merged into this history. Final gate: backend 657 passed, Vitest 482/482,
E2E 13/13, soak 5/5, no new tmux sockets.

### T5: Frontend coverage ✅
- [x] `api/http.ts`: `apiFetch`, 403 token retry, error mapping
- [x] `Terminal.vue` close-code and reconnect paths
- [x] `emoji-favicon.ts`, router guards, auth and ping stores
- [x] English/Japanese locale key parity
- [x] Shared golden vectors prove `generateSessionName` matches `ts_session_name`
- [x] `Terminal.spec.ts` passes 50/50 under load (a timed-out dynamic import leaked a second mount)
