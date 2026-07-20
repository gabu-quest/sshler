"""Tests for the native Windows shell terminal path.

These run cross-platform: the shell catalog and the spawn path are exercised
with mocked ``shutil.which`` / WSL probe / ConPTY spawn, so the assertions hold
on Linux CI as well as on a real Windows host.
"""

from __future__ import annotations

import os
import sys
import pytest

from sshler.api import helpers
from sshler import webapp
from sshler.winpty_proc import WinPTYProcess

# Native Windows ConPTY/shell-spawn coverage. Although these use mocks, the
# whole feature targets Windows only; gate the file to Windows so it can never
# destabilise Linux CI. The suite runs in full on the real target platform.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Native Windows shell/ConPTY tests — Windows-only target platform.",
)


# A fake which-table: every native shell + wsl resolves to a stable path.
_WHICH_TABLE = {
    "pwsh": r"C:\PF\pwsh.exe",
    "powershell": r"C:\Win\powershell.exe",
    "cmd": r"C:\Win\cmd.exe",
    "wsl": r"C:\Win\wsl.exe",
}


def _fake_which(table):
    return lambda name: table.get(name)


# --------------------------------------------------------------------------
# Shell catalog
# --------------------------------------------------------------------------


def test_available_windows_shells_with_distro(monkeypatch):
    monkeypatch.setattr(helpers.shutil, "which", _fake_which(_WHICH_TABLE))
    monkeypatch.setattr(helpers, "_wsl_distros", lambda: ["Ubuntu", "Debian"])
    monkeypatch.setenv("ComSpec", r"C:\Win\cmd.exe")

    shells = helpers.available_windows_shells()

    assert [s["id"] for s in shells] == ["pwsh", "powershell", "cmd", "wsl"]

    by_id = {s["id"]: s for s in shells}
    assert by_id["pwsh"]["label"] == "PowerShell 7"
    assert by_id["pwsh"]["argv"] == [r"C:\PF\pwsh.exe", "-NoLogo"]
    assert by_id["pwsh"]["available"] is True

    assert by_id["powershell"]["argv"] == [r"C:\Win\powershell.exe", "-NoLogo"]
    assert by_id["cmd"]["argv"] == [r"C:\Win\cmd.exe"]
    assert by_id["cmd"]["available"] is True

    # WSL: available because a distro exists, label names the first distro.
    assert by_id["wsl"]["available"] is True
    assert by_id["wsl"]["label"] == "WSL (Ubuntu)"
    assert by_id["wsl"]["argv"] == [r"C:\Win\wsl.exe"]


def test_available_windows_shells_wsl_no_distro(monkeypatch):
    monkeypatch.setattr(helpers.shutil, "which", _fake_which(_WHICH_TABLE))
    monkeypatch.setattr(helpers, "_wsl_distros", lambda: [])
    monkeypatch.setenv("ComSpec", r"C:\Win\cmd.exe")

    shells = helpers.available_windows_shells()
    by_id = {s["id"]: s for s in shells}

    # WSL is still listed (so the UI can offer it) but flagged unavailable.
    assert by_id["wsl"]["available"] is False
    assert by_id["wsl"]["label"] == "WSL"


def test_available_windows_shells_native_missing(monkeypatch):
    # Only cmd + wsl resolve; pwsh and powershell are absent.
    table = {"cmd": r"C:\Win\cmd.exe", "wsl": r"C:\Win\wsl.exe"}
    monkeypatch.setattr(helpers.shutil, "which", _fake_which(table))
    monkeypatch.setattr(helpers, "_wsl_distros", lambda: [])
    monkeypatch.setenv("ComSpec", r"C:\Win\cmd.exe")

    shells = helpers.available_windows_shells()
    assert [s["id"] for s in shells] == ["cmd", "wsl"]


def test_default_windows_shell_prefers_pwsh(monkeypatch):
    monkeypatch.setattr(helpers.shutil, "which", _fake_which(_WHICH_TABLE))
    monkeypatch.setattr(helpers, "_wsl_distros", lambda: [])
    monkeypatch.setenv("ComSpec", r"C:\Win\cmd.exe")

    assert helpers.default_windows_shell() == "pwsh"


def test_default_windows_shell_falls_back_to_cmd(monkeypatch):
    table = {"cmd": r"C:\Win\cmd.exe"}
    monkeypatch.setattr(helpers.shutil, "which", _fake_which(table))
    monkeypatch.setattr(helpers, "_wsl_distros", lambda: [])
    monkeypatch.setenv("ComSpec", r"C:\Win\cmd.exe")

    assert helpers.default_windows_shell() == "cmd"


# --------------------------------------------------------------------------
# Shell resolution + spawn
# --------------------------------------------------------------------------


_CATALOG = [
    {"id": "pwsh", "label": "PowerShell 7", "argv": [r"C:\PF\pwsh.exe", "-NoLogo"], "available": True},
    {"id": "cmd", "label": "Command Prompt", "argv": [r"C:\Win\cmd.exe"], "available": True},
    {"id": "wsl", "label": "WSL", "argv": [r"C:\Win\wsl.exe"], "available": False},
]


def test_resolve_windows_shell_exact(monkeypatch):
    monkeypatch.setattr(webapp, "available_windows_shells", lambda: _CATALOG)
    chosen = webapp._resolve_windows_shell("cmd")
    assert chosen["id"] == "cmd"
    assert chosen["argv"] == [r"C:\Win\cmd.exe"]


def test_resolve_windows_shell_unknown_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(webapp, "available_windows_shells", lambda: _CATALOG)
    monkeypatch.setattr(webapp, "default_windows_shell", lambda: "pwsh")
    chosen = webapp._resolve_windows_shell("does-not-exist")
    assert chosen["id"] == "pwsh"


def test_resolve_windows_shell_wsl_unavailable_raises(monkeypatch):
    monkeypatch.setattr(webapp, "available_windows_shells", lambda: _CATALOG)
    with pytest.raises(webapp.WSLNotAvailableError, match="wsl --install"):
        webapp._resolve_windows_shell("wsl")


@pytest.mark.asyncio
async def test_open_windows_shell_spawns_chosen_argv(monkeypatch):
    captured = {}

    class FakeWinPTY:
        @staticmethod
        def spawn(argv, cwd=None, cols=80, rows=24):
            captured["argv"] = argv
            captured["cwd"] = cwd
            captured["cols"] = cols
            captured["rows"] = rows
            return "fake-process"

    monkeypatch.setattr(webapp, "available_windows_shells", lambda: _CATALOG)
    monkeypatch.setattr(webapp, "WinPTYProcess", FakeWinPTY)

    result = await webapp._open_windows_shell("pwsh", "C:/proj", cols=100, rows=40)

    assert result == "fake-process"
    assert captured["argv"] == [r"C:\PF\pwsh.exe", "-NoLogo"]
    assert captured["cwd"] == "C:/proj"
    assert captured["cols"] == 100
    assert captured["rows"] == 40


@pytest.mark.asyncio
async def test_open_windows_shell_wsl_unavailable_raises(monkeypatch):
    monkeypatch.setattr(webapp, "available_windows_shells", lambda: _CATALOG)
    with pytest.raises(webapp.WSLNotAvailableError):
        await webapp._open_windows_shell("wsl", "C:/proj")


# --------------------------------------------------------------------------
# WinPTYProcess shims (fake pty, runs cross-platform)
# --------------------------------------------------------------------------


class _FakePty:
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.written: list[str] = []
        self.size: tuple[int, int] | None = None
        self.terminated = False
        self.closed = False
        self.exitstatus = 0

    def read(self, size):
        if not self.chunks:
            raise EOFError("closed")
        return self.chunks.pop(0)

    def write(self, text):
        self.written.append(text)
        return len(text)

    def setwinsize(self, rows, cols):
        self.size = (rows, cols)

    def wait(self):
        return 0

    def terminate(self, force=False):
        self.terminated = True

    def close(self):
        self.closed = True


def test_winpty_stdin_decodes_bytes_to_str():
    pty = _FakePty()
    proc = WinPTYProcess(pty)
    n = proc.stdin.write("café".encode("utf-8"))
    assert pty.written == ["café"]
    assert n == len("café")


def test_winpty_stdout_returns_str_then_empty_on_eof():
    pty = _FakePty(chunks=["hello", "world"])
    proc = WinPTYProcess(pty)
    assert proc.stdout.read(1024) == "hello"
    assert proc.stdout.read(1024) == "world"
    # EOFError from the child collapses to "" so the reader loop ends.
    assert proc.stdout.read(1024) == ""


def test_winpty_resize_uses_rows_cols_order():
    pty = _FakePty()
    proc = WinPTYProcess(pty)
    proc.resize(120, 40)  # (cols, rows)
    assert pty.size == (40, 120)  # winpty wants (rows, cols)


def test_winpty_terminate_and_close():
    pty = _FakePty()
    proc = WinPTYProcess(pty)
    proc.terminate()
    proc.close()
    assert pty.terminated is True
    assert pty.closed is True


# --------------------------------------------------------------------------
# WSL distro probe
# --------------------------------------------------------------------------


def test_wsl_distros_parses_utf16(monkeypatch):
    monkeypatch.setattr(helpers, "LOCAL_IS_WINDOWS", True)
    monkeypatch.setattr(helpers.shutil, "which", lambda name: r"C:\Win\wsl.exe")

    class FakeCompleted:
        returncode = 0
        # wsl.exe -l -q emits UTF-16-LE with CRLF line endings.
        stdout = "Ubuntu\r\nDebian\r\n".encode("utf-16-le")

    monkeypatch.setattr(helpers.subprocess, "run", lambda *a, **k: FakeCompleted())
    assert helpers._wsl_distros() == ["Ubuntu", "Debian"]


def test_wsl_distros_empty_on_nonzero(monkeypatch):
    monkeypatch.setattr(helpers, "LOCAL_IS_WINDOWS", True)
    monkeypatch.setattr(helpers.shutil, "which", lambda name: r"C:\Win\wsl.exe")

    class FakeCompleted:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(helpers.subprocess, "run", lambda *a, **k: FakeCompleted())
    assert helpers._wsl_distros() == []


# --------------------------------------------------------------------------
# Bootstrap exposure (per-platform, deterministic)
# --------------------------------------------------------------------------


def test_bootstrap_exposes_platform_and_shells(tmp_path):
    import yaml
    from fastapi.testclient import TestClient
    from sshler.webapp import ServerSettings, make_app

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)

    client = TestClient(make_app(ServerSettings(csrf_token="t")))
    try:
        data = client.get("/api/v1/bootstrap").json()
    finally:
        client.close()

    assert "platform" in data
    assert "windows_shells" in data
    assert "default_shell" in data

    if helpers.LOCAL_IS_WINDOWS:
        assert data["platform"] == "windows"
        assert len(data["windows_shells"]) >= 1  # guard
        ids = {s["id"] for s in data["windows_shells"]}
        # At least one native shell is always present on Windows.
        assert ids & {"pwsh", "powershell", "cmd"}
        for shell in data["windows_shells"]:
            assert set(shell) == {"id", "label", "available"}
    else:
        assert data["platform"] == "posix"
        assert data["windows_shells"] == []
        assert data["default_shell"] is None


# --------------------------------------------------------------------------
# Windows WS handler: persistence wiring (registry attach/detach, no terminate)
# --------------------------------------------------------------------------


import asyncio
import threading


class _FakeWinSession:
    """Stand-in for win_terminal_registry.TerminalSession.

    ``process`` is a REAL WinPTYProcess so the handler's ``isinstance`` checks
    take the Windows branch; everything else is inert.
    """

    def __init__(self, process):
        self.process = process
        self.session_id = None
        self.exited_event = asyncio.Event()
        self.detached = threading.Event()  # test sync point


class _FakeRegistry:
    """Records handler→registry calls; never spawns or terminates anything."""

    def __init__(self, session):
        self._session = session
        self.calls: list[tuple] = []

    async def get_or_create(self, key, spawn, cols, rows):
        self.calls.append(("get_or_create", key))
        return self._session, True

    async def attach(self, session, sink, cols, rows):
        self.calls.append(("attach", cols, rows))
        return b"REPLAY"  # sent to the client; doubles as a test sync point

    async def detach(self, session, sink):
        self.calls.append(("detach",))
        session.detached.set()

    async def write(self, session, data):
        self.calls.append(("write", data))


def test_windows_ws_attaches_and_detaches_without_terminating(tmp_path, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient
    from sshler import state
    from sshler.webapp import ServerSettings, make_app
    from sshler.winpty_proc import WinPTYProcess

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))

    # Force the native-Windows branch even on Linux CI.
    monkeypatch.setattr(webapp, "LOCAL_IS_WINDOWS", True)

    # Real WinPTYProcess wrapping a fake pty so isinstance(process, WinPTYProcess)
    # holds; its read/terminate are never exercised on this path.
    fake_pty = _FakePty(chunks=[])
    process = WinPTYProcess(fake_pty)
    fake_session = _FakeWinSession(process)
    fake_registry = _FakeRegistry(fake_session)

    # Spy on session-activity updates: a persisted Windows shell must NOT be
    # flipped inactive on disconnect.
    activity_calls: list[tuple] = []

    async def _spy_activity(session_id, active=None, **kwargs):
        activity_calls.append((session_id, active))

    monkeypatch.setattr(state, "update_session_activity_async", _spy_activity)

    app = make_app(ServerSettings(csrf_token="t"))
    app.state.win_terminal_registry = fake_registry

    client = TestClient(app)
    try:
        url = "/ws/term?host=local&dir=.&session=proj&cols=80&rows=24&token=t"
        with client.websocket_connect(url) as ws:
            # Receiving the replay frame proves the handler reached attach(),
            # so the subsequent disconnect exercises the detach path (not the
            # pre-attach early-return).
            assert ws.receive_bytes() == b"REPLAY"
        # Handler runs its finally on the server loop; detach signals completion.
        assert fake_session.detached.wait(timeout=5) is True
    finally:
        client.close()

    ops = [c[0] for c in fake_registry.calls]

    # Re-used/created the persisted shell keyed by (box, session).
    assert ("get_or_create", ("local", "proj")) in fake_registry.calls
    # Attached a sink, then detached on disconnect.
    assert "attach" in ops
    assert "detach" in ops
    # The ConPTY was NOT terminated — that's the whole point of persistence.
    assert fake_pty.terminated is False
    assert fake_pty.closed is False
    # The DB row was never flipped inactive for the Windows shell.
    assert all(active is not False for (_sid, active) in activity_calls)
