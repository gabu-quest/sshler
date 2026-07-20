# Roadmap: Native Windows Terminal

Lets a local-box terminal run a **native Windows shell** in the browser, instead of
requiring WSL+tmux (which silently failed when no WSL distro was installed).

## Milestones

### M1: Native shell via ConPTY ✅
- [x] `pywinpty` dependency (Windows-only marker, prebuilt wheel) — `pyproject.toml`
- [x] `WinPTYProcess` ConPTY wrapper mirroring `LocalPTYProcess` — `sshler/winpty_proc.py`
- [x] Shell catalog (pwsh / Windows PowerShell / cmd / WSL) + WSL distro probe — `sshler/api/helpers.py`
- [x] `/ws/term` `shell` param → `_open_windows_shell`; reader/writer/resize handle `WinPTYProcess` — `sshler/webapp.py`
- [x] WSL chosen but no distro → close code `4502` → frontend toast — `webapp.py`, `Terminal.vue`
- [x] Shells exposed via `/api/v1/bootstrap` (`platform`, `windows_shells`, `default_shell`)
- [x] Shell picker in `TerminalView.vue` (Windows local box only), remembers last choice
- [x] Tests: `tests/test_windows_terminal.py` (17), `bootstrap.spec.ts`; live ConPTY + 4502 verified

### M2: Session persistence for native Windows shells ✅
Native Windows shells are plain ConPTY sessions with **no tmux**. M2 re-implements
tmux's persistence value natively via a per-app registry that owns the ConPTY
lifecycle independently of any websocket.
- [x] Server-side live-process registry keyed by `(box, session)` — `sshler/win_terminal_registry.py`
- [x] Byte-bounded output ring buffer (default 768 KB) + clear-and-replay on (re)attach
- [x] Detach-on-disconnect (keep child + ring alive) instead of terminate — `webapp.py` finally block
- [x] Persist-until-restart: only reap on shell exit / explicit kill / app shutdown (idle TTL off by default, `SSHLER_WIN_TERM_TTL=0`)
- [x] Mirror (tmux-style) multi-attach: N tabs share one shell, all see output + can type
- [x] SessionSwitcher delete reaps the registry (`api/sessions.py` → `registry.kill`)
- [x] Windows session row stays `active` while persisted; flipped inactive only on reap/exit
- [x] Tests: `tests/test_win_terminal_registry.py` (18, blocking-fake drain) + 1 WS handler integration test in `test_windows_terminal.py`

**Decisions:** persist-until-restart (no idle reaping) and tmux-style mirroring (per user).
**Replay:** clear+replay of the ring (no client change). Env knobs: `SSHLER_WIN_TERM_RING_BYTES`,
`SSHLER_WIN_TERM_TTL`, `SSHLER_WIN_TERM_REAP_INTERVAL`.

### M3: Polish (future) ⬚
- [ ] Seq/offset incremental replay (vs full clear+replay) to preserve pre-ring scrollback on reconnect
- [ ] Smallest-attached-client resize constraint (mirrored tabs of different sizes)
- [ ] Recovery after server restart (persisted shells currently die on restart — intended)
- [ ] "Persisted" indicator badge in SessionSwitcher

### M4: WSL → Windows access (deferred) ⬚

The inverse of M1–M3: those cover sshler running **on Windows**. This covers sshler
running **inside WSL** and wanting to reach the Windows side. Analyzed and deferred —
the simplest path is to just run sshler natively on Windows (M1–M3 already make that a
first-class experience), so this stays parked unless there's a concrete need.

Three options were considered, ranked by how genuinely "Windows" they are. The backend
keys all subprocess-vs-SSH behavior on `box.transport == "local"` (not on the name
`"local"`), so a second synthetic local-transport box inherits browse/grep/git/archive/
transfer/tmux for free — that's what makes options 1–2 cheap.

| Option | What | Filesystem | Shell/tools | sshler code |
|---|---|---|---|---|
| **1. Local box → `/mnt/c`** | Auto-inject a `windows` box (`transport=local`) rooted at the C: mount when WSL is detected; overridable via `boxes.yaml` like the local box | Windows files over the **9p** bridge (slow for git/grep) | **Linux** shell cd'd into Windows files | Small: a `_build_windows_box()` mirroring `_build_local_box()` in `config.py` + WSL gate + test |
| **2. Local box + `powershell.exe`** | Same box, but terminal launches PowerShell via WSL interop | Still 9p `/mnt/c` | **Real PowerShell** (Windows process bridged from WSL) | Medium: per-box shell override in the `/ws/term` handler |
| **3. SSH box → Windows OpenSSH** | A plain SSH box; Windows runs its built-in `sshd`. **Most Windows, most robust, zero sshler code** | **Native NTFS** via SFTP, no 9p | **Fully native** Windows (PowerShell/cmd, Windows PATH/tools) | None — rides the mature SSH transport. Cost is one-time Windows admin: enable OpenSSH Server feature, start `sshd`, set default shell, add key; WSL2→host needs the host IP or Win11 mirrored networking |

**WSL detection primitives** (all trivial, confirmed present on the dev box): `WSL_DISTRO_NAME`
env var, `"microsoft"` in `/proc/version`, or `/mnt/c` existing. Note `sshler/api/config.py`
already has `_detect_wsl_distro()` for the *reverse* case (sshler on Windows detecting WSL).

**Claude Session Dashboard implication (important):**
- Options **1 & 2** (local `/mnt/c` box): Claude still runs in **WSL/Linux** — same machine,
  `transport=local`. `/app/claude` only ever scans one config dir (WSL's `~/.claude`), so it
  would operate *on* Windows files but every session it lists is Linux-side.
- Option **3** (native Windows SSH box): Claude would run **natively on Windows** (writing to
  `C:\Users\<you>\.claude`), but those sessions would **not** appear in `/app/claude` — the
  dashboard is local-box-only + pull-based (remote-box sessions are out of M1 scope).
- Windows-native Claude transcripts live at `/mnt/c/Users/<you>/.claude/projects`, which the
  scanner does **not** read. Surfacing them would be its own feature: teach the scanner to
  accept multiple config roots, or extend the dashboard to remote boxes.

**Decision (2026-07-01):** Deferred. Preferred path is running sshler natively on Windows
rather than bridging from WSL. Revisit only if there's a concrete need to drive the Windows
side from a WSL-hosted sshler.

Status markers: ✅ done, 🔄 in progress, ⬚ not started.
