"""Integration tests for force_kill_local_session.

Uses REAL per-session tmux sockets (tmux is required). The tests reproduce the
exact gap the feature closes: a pane process that ignores SIGHUP survives a
plain kill-session, but force_kill_local_session SIGKILLs it — while an
unrelated session on its own socket is left completely untouched.
"""

from __future__ import annotations

import asyncio
import os
import select
import shlex
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from sshler.tmux import (
    _list_local_pane_pids,
    force_kill_local_session,
    local_tmux_command,
    remote_force_kill_words,
)


def real_tmux(test):
    """Marks a test that drives a real tmux on its own ``-L`` server (or the route's
    real force kill, which asks one); the tests that stand in for tmux run everywhere."""
    test = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")(test)
    return pytest.mark.posix_only("a real tmux server on its own -L socket")(test)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _run(session: str, *args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *(local_tmux_command(session) + list(args)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _socket_exists(session: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        *(local_tmux_command(session) + ["list-sessions"]),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0


def _socket_path(session: str) -> Path:
    """Where tmux puts the ``-L ts-<session>`` socket under the private TMUX_TMPDIR."""
    return Path(os.environ["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}" / f"ts-{session}"


def _session_fixture(role: str):
    name = f"sshlertest-{role}-{uuid.uuid4().hex[:12]}"
    yield name
    # Cleanup regardless of test outcome: stop the server, then drop its socket.
    asyncio.run(_run(name, "kill-server"))
    _socket_path(name).unlink(missing_ok=True)


@pytest.fixture
def victim_session():
    yield from _session_fixture("victim")


@pytest.fixture
def bystander_session():
    yield from _session_fixture("bystander")


@real_tmux
@pytest.mark.asyncio
async def test_sessions_live_in_isolated_socket_dir(victim_session, tmux_tmpdir):
    """The test tmux server's socket is in the private dir, never the user's."""
    await _run(victim_session, "new-session", "-d", "-s", victim_session)
    expected = tmux_tmpdir / f"tmux-{os.getuid()}" / f"ts-{victim_session}"
    assert expected.is_socket() is True
    assert await _socket_exists(victim_session) is True


@real_tmux
@pytest.mark.asyncio
async def test_force_kill_terminates_sighup_ignoring_process(victim_session):
    await _run(victim_session, "new-session", "-d", "-s", victim_session)
    # Make the pane shell ignore SIGHUP so a plain kill-session wouldn't stop it.
    await _run(victim_session, "send-keys", "-t", victim_session, "-l", "trap '' HUP")
    await _run(victim_session, "send-keys", "-t", victim_session, "Enter")
    await asyncio.sleep(0.4)

    pids = await _list_local_pane_pids(victim_session)
    assert len(pids) == 1  # exactly one pane => one shell PID
    shell_pid = pids[0]
    assert _pid_alive(shell_pid) is True  # guard: it's really running

    await force_kill_local_session(victim_session)

    # The socket/server is gone...
    assert await _socket_exists(victim_session) is False
    # ...and the SIGHUP-ignoring shell was SIGKILLed (poll briefly for reaping).
    deadline = time.monotonic() + 3.0
    while _pid_alive(shell_pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert _pid_alive(shell_pid) is False


@real_tmux
@pytest.mark.asyncio
async def test_force_kill_leaves_other_sessions_untouched(victim_session, bystander_session):
    await _run(victim_session, "new-session", "-d", "-s", victim_session)
    await _run(bystander_session, "new-session", "-d", "-s", bystander_session)
    await asyncio.sleep(0.3)

    bystander_pids = await _list_local_pane_pids(bystander_session)
    assert len(bystander_pids) == 1
    bystander_pid = bystander_pids[0]

    await force_kill_local_session(victim_session)

    # Victim gone, bystander (its own socket) fully intact.
    assert await _socket_exists(victim_session) is False
    assert await _socket_exists(bystander_session) is True
    assert _pid_alive(bystander_pid) is True


@real_tmux
def test_delete_endpoint_accepts_force_and_removes_row(tmp_path, monkeypatch):
    """The DELETE endpoint accepts ?force=true and deletes the session row.

    (force_kill on a session with no live socket is a safe no-op — this asserts
    the param is wired through the route and the DB row is gone afterward.)
    """
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        create = client.post(
            "/api/v1/boxes/local/sessions",
            json={"session_name": "forceme", "working_directory": "/"},
            headers=auth_headers(),
        )
        assert create.status_code == 200
        session_id = create.json()["id"]

        resp = client.delete(
            f"/api/v1/boxes/local/sessions/{session_id}",
            params={"kill_tmux": "true", "force": "true"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "deleted"

        listing = client.get("/api/v1/boxes/local/sessions", headers=auth_headers())
        assert listing.status_code == 200
        assert all(item["id"] != session_id for item in listing.json())
    finally:
        client.close()
        state.reset_state()


@real_tmux
def test_force_kill_by_name_route_terminates_real_session(victim_session, tmp_path, monkeypatch):
    """POST by-name/force-kill kills a live local session through the ROUTE.

    Mutation killed: the route no longer calling ``force_kill_local_session``
    (or calling it with another name) leaves the SIGHUP-ignoring shell alive.
    """
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    # The pane runs `sh` (not the user's interactive shell, whose HUP handling
    # varies: zsh ignores `trap '' HUP`) with SIGHUP ignored. It writes to a FIFO
    # only after the trap is installed, so the read below is an event, not a guess
    # at shell latency. The loop never touches the tty, so the process cannot exit
    # on its own when the pty closes: only SIGKILL ends it.
    ready_fifo = tmp_path / "trap-installed"
    os.mkfifo(ready_fifo)
    reader = os.open(ready_fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        asyncio.run(
            _run(
                victim_session,
                "new-session",
                "-d",
                "-s",
                victim_session,
                f"exec sh -c \"trap '' HUP; echo trap-installed > {ready_fifo}; "
                'while :; do sleep 1; done"',
            )
        )
        readable, _, _ = select.select([reader], [], [], 10.0)
        assert readable == [reader], "shell never reported the HUP trap as installed"
        assert os.read(reader, 64) == b"trap-installed\n"
    finally:
        os.close(reader)
    pids = asyncio.run(_list_local_pane_pids(victim_session))
    assert len(pids) == 1
    shell_pid = pids[0]
    assert _pid_alive(shell_pid) is True

    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        resp = client.post(
            f"/api/v1/boxes/local/sessions/by-name/{victim_session}/force-kill",
            headers=auth_headers(),
        )
    finally:
        client.close()
        state.reset_state()
    assert resp.status_code == 200
    assert resp.json()["path"] == victim_session

    assert asyncio.run(_socket_exists(victim_session)) is False
    deadline = time.monotonic() + 3.0
    while _pid_alive(shell_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _pid_alive(shell_pid) is False


@real_tmux
def test_force_kill_by_name_rejects_blank_and_remote(tmp_path, monkeypatch):
    """Blank names are 400; local box accepts a (no-op) unknown name with 200."""
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        blank = client.post(
            "/api/v1/boxes/local/sessions/by-name/%20/force-kill",
            headers=auth_headers(),
        )
        assert blank.status_code == 400

        # Unknown-but-valid name on the local box: safe no-op, 200 ok.
        ok = client.post(
            "/api/v1/boxes/local/sessions/by-name/nonexistent-xyz/force-kill",
            headers=auth_headers(),
        )
        assert ok.status_code == 200
        assert ok.json()["message"] == "force-killed"
    finally:
        client.close()
        state.reset_state()


def _write_boxes(tmp_path, boxes):
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": boxes}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


@pytest.fixture
def kill_recorders(monkeypatch):
    """Replace the tmux kill helpers used by the sessions routes with recorders."""
    force_calls: list[str] = []
    plain_calls: list[tuple[str, list[str]]] = []

    async def fake_force(session_name):
        force_calls.append(session_name)

    async def fake_run_local_tmux(session_name, args, timeout=None):
        plain_calls.append((session_name, list(args)))
        return 0, b"", b""

    # The runner of the local box force-kills through this one helper.
    monkeypatch.setattr("sshler.tmux.force_kill_local_session", fake_force)
    # The runner of the local box runs every session's tmux through this one helper.
    monkeypatch.setattr("sshler.tmux.run_local_tmux", fake_run_local_tmux)
    return {"force": force_calls, "plain": plain_calls}


def _create_local_session(client, name):
    from tests.test_api_v1 import auth_headers

    create = client.post(
        "/api/v1/boxes/local/sessions",
        json={"session_name": name, "working_directory": "/"},
        headers=auth_headers(),
    )
    assert create.status_code == 200
    return create.json()["id"]


def test_delete_route_calls_force_helper_only_when_force_true(
    tmp_path, monkeypatch, kill_recorders
):
    """Mutations killed: route ignores ``force`` (always plain kill-session) or
    always force-kills (helper called for ``force=false``).
    """
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        forced_id = _create_local_session(client, "forced-one")
        plain_id = _create_local_session(client, "plain-one")

        resp = client.delete(
            f"/api/v1/boxes/local/sessions/{forced_id}",
            params={"kill_tmux": "true", "force": "true"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert kill_recorders["force"] == ["forced-one"]
        assert kill_recorders["plain"] == []

        resp = client.delete(
            f"/api/v1/boxes/local/sessions/{plain_id}",
            params={"kill_tmux": "true", "force": "false"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        # Still exactly the one forced call; the plain delete used kill-session.
        assert kill_recorders["force"] == ["forced-one"]
        assert kill_recorders["plain"] == [("plain-one", ["kill-session", "-t", "plain-one"])]
    finally:
        client.close()
        state.reset_state()


def test_delete_route_force_without_kill_tmux_calls_nothing(tmp_path, monkeypatch, kill_recorders):
    """Mutation killed: ``force`` alone (no ``kill_tmux``) must not kill tmux."""
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        session_id = _create_local_session(client, "keepalive")
        resp = client.delete(
            f"/api/v1/boxes/local/sessions/{session_id}",
            params={"force": "true"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert kill_recorders["force"] == []
        assert kill_recorders["plain"] == []
    finally:
        client.close()
        state.reset_state()


@pytest.fixture
def remote_commands(monkeypatch):
    """A remote box ``remote1`` whose connection records each command line and answers
    status 0 with no output."""
    commands: list[str] = []

    class FakeConnection:
        async def run(self, command, check=False, encoding="utf-8"):
            commands.append(command)
            return SimpleNamespace(exit_status=0, stdout=b"", stderr=b"")

        def close(self):
            pass

    async def fake_connect(self, box, application_config):
        return FakeConnection()

    monkeypatch.setattr("sshler.api.dependencies.APIDependencies.connect_for_box", fake_connect)
    return commands


def _force_kill_line(session: str) -> list[str]:
    """The words of the remote force-kill line of ``session``, as a shell reads them."""
    return [*remote_force_kill_words(session), "2>/dev/null"]


def test_delete_route_remote_force_runs_the_remote_force_kill_of_its_own_server(
    tmp_path, monkeypatch, kill_recorders, remote_commands
):
    """A forced remote delete stops the session's own ts server and SIGKILLs its panes
    on the remote box (never the local helper, never a plain kill-session)."""
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client

    config_dir = _write_boxes(tmp_path, [{"name": "remote1", "host": "example.invalid"}])
    client = build_client(config_dir, monkeypatch)
    try:
        state.initialize(config_dir)
        record = state.create_or_update_session("remote1", "rsess", "/")
        resp = client.delete(
            f"/api/v1/boxes/remote1/sessions/{record.id}",
            params={"kill_tmux": "true", "force": "true"},
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert kill_recorders["force"] == []
        assert kill_recorders["plain"] == []
        # The pool health-checks the connection it gets back with ``echo test``.
        sent = [shlex.split(c) for c in remote_commands if c != "echo test"]
        assert sent == [_force_kill_line("rsess")]
        assert state.get_session_by_id(record.id) is None
    finally:
        client.close()
        state.reset_state()


def test_force_kill_by_name_route_calls_helper_once_with_the_name(
    tmp_path, monkeypatch, kill_recorders
):
    """Mutation killed: route skipping the helper, calling it twice, or with a
    different name."""
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        resp = client.post(
            "/api/v1/boxes/local/sessions/by-name/target-sess/force-kill",
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        assert kill_recorders["force"] == ["target-sess"]
        assert kill_recorders["plain"] == []
    finally:
        client.close()
        state.reset_state()


@pytest.mark.parametrize("name", ["target-sess", "a b", "it's", 'a"b', "$HOME;id"], ids=repr)
def test_force_kill_by_name_on_a_remote_box_kills_its_own_server_and_drops_its_rows(
    tmp_path, monkeypatch, kill_recorders, remote_commands, name
):
    """A remote box is force-killed as the local one: one remote line whose last word is
    the session's own server, ``ok``, and the box's rows of that name gone."""
    from urllib.parse import quote

    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client

    config_dir = _write_boxes(tmp_path, [{"name": "remote1", "host": "example.invalid"}])
    client = build_client(config_dir, monkeypatch)
    try:
        state.initialize(config_dir)
        kept = state.create_or_update_session("remote1", "other", "/")
        state.create_or_update_session("remote1", name, "/")
        resp = client.post(
            f"/api/v1/boxes/remote1/sessions/by-name/{quote(name, safe='')}/force-kill",
            headers=auth_headers(),
        )
        assert (resp.status_code, resp.json()["message"]) == (200, "force-killed")
        sent = [shlex.split(c) for c in remote_commands if c != "echo test"]
        assert sent == [_force_kill_line(name)]
        assert sent[0][4] == f"ts-{name}"
        assert kill_recorders["force"] == []
        assert [r.session_name for r in state.list_sessions("remote1")] == [kept.session_name]
    finally:
        client.close()
        state.reset_state()


@pytest.mark.parametrize("name", ["%20", "a%0Db", "a%0Ab", "a%00b"])
def test_force_kill_by_name_on_a_remote_box_refuses_a_bad_name_before_it_connects(
    tmp_path, monkeypatch, kill_recorders, remote_commands, name
):
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client

    config_dir = _write_boxes(tmp_path, [{"name": "remote1", "host": "example.invalid"}])
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            f"/api/v1/boxes/remote1/sessions/by-name/{name}/force-kill",
            headers=auth_headers(),
        )
        assert (resp.status_code, resp.json()["detail"]) == (400, "Invalid session name")
        assert remote_commands == []
        assert kill_recorders["force"] == []
    finally:
        client.close()
        state.reset_state()


# --- a Windows local box: its shells live in the terminal registry, not in tmux ------


class _ConPtyShell:
    """Stands in for a ConPTY shell the registry holds; records its end."""

    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def close(self) -> None:
        pass


def _held_shell(registry, session: str) -> _ConPtyShell:
    """Puts a shell for ``("local", session)`` into *registry* as the terminal socket
    would, without a ConPTY to read from."""
    from sshler.win_terminal_registry import TerminalSession

    shell = _ConPtyShell()
    key = ("local", session)
    registry._sessions[key] = TerminalSession(key, shell, 80, 24, 1024)
    return shell


def test_force_kill_by_name_ends_the_windows_shell_through_the_registry_then_drops_the_row(
    tmp_path, monkeypatch, kill_recorders
):
    """The toolbar's force kill of a native Windows shell ends it in the registry, as a
    forced delete does, and drops the row; a shell under another name is left alone."""
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    client = build_client(setup_config(tmp_path), monkeypatch)
    registry = client.app.state.win_terminal_registry
    try:
        _create_local_session(client, "proj")
        victim = _held_shell(registry, "proj")
        bystander = _held_shell(registry, "other")
        resp = client.post(
            "/api/v1/boxes/local/sessions/by-name/proj/force-kill", headers=auth_headers()
        )
        assert (resp.status_code, resp.json()["message"]) == (200, "force-killed")
        assert registry.live_keys() == [("local", "other")]
        assert (victim.terminated, bystander.terminated) == (True, False)
        assert kill_recorders["force"] == ["proj"]
        assert [r.session_name for r in state.list_sessions("local")] == []
    finally:
        client.close()
        state.reset_state()


def test_force_kill_by_name_of_a_shell_the_registry_does_not_hold_drops_the_row(
    tmp_path, monkeypatch, kill_recorders
):
    from sshler import state
    from tests.test_api_v1 import auth_headers, build_client, setup_config

    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        _create_local_session(client, "proj")
        resp = client.post(
            "/api/v1/boxes/local/sessions/by-name/proj/force-kill", headers=auth_headers()
        )
        assert (resp.status_code, resp.json()["message"]) == (200, "force-killed")
        assert client.app.state.win_terminal_registry.live_keys() == []
        assert [r.session_name for r in state.list_sessions("local")] == []
    finally:
        client.close()
        state.reset_state()


@pytest.mark.asyncio
async def test_a_local_force_kill_on_windows_ends_the_wsl_server_and_signals_no_process(
    monkeypatch,
):
    """On Windows the local tmux runs inside WSL: its pane ids are WSL's, and Windows has
    no ``killpg``, ``getpgid`` or ``SIGKILL``. The force kill stops the session's server
    there and sends no signal from Windows."""
    import signal

    from sshler import tmux as tmux_module

    ran: list[tuple[str, list[str]]] = []
    signalled: list[tuple[int, int]] = []

    async def fake_run_local_tmux(session_name, args, capture_output=True, timeout=None):
        ran.append((session_name, list(args)))
        return 0, b"", b""

    async def wsl_pane_pids(session_name):
        return [4242]

    monkeypatch.setattr(tmux_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(tmux_module, "run_local_tmux", fake_run_local_tmux)
    monkeypatch.setattr(tmux_module, "_list_local_pane_pids", wsl_pane_pids)
    monkeypatch.delattr(os, "killpg")
    monkeypatch.delattr(os, "getpgid")
    monkeypatch.delattr(signal, "SIGKILL")
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    await tmux_module.force_kill_local_session("proj")

    assert ran == [("proj", ["kill-server"])]
    assert signalled == []
