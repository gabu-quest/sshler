"""A plain shell: a terminal with no tmux, on the local box and on a remote box.

``/ws/term`` takes ``shell``: ``""`` or ``"tmux"`` (tmux, the default) or ``"plain"``
on a POSIX local box and on a remote box; any other value is refused before anything
opens. A box without tmux gets a plain shell in its place. The server tells the page
what it opened with one text frame before any output.

The local tests drive the real app over raw ASGI on the test's event loop, as
``tests/test_terminal_endings.py`` does, and count what is left after the handler
returns. The remote tests run the app against an asyncssh server on the loopback
interface that does what sshd does: it runs each command line with ``/bin/sh -c``, on
a PTY when the client asked for one, and hangs the PTY up when the channel closes.
Every shell started here is ``/bin/sh`` with a throwaway ``HOME``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import asyncssh
import psutil
import pytest
import pytest_asyncio
import yaml
from terminal_fakes import TMUX_CHECK_LINE, FakeSshChannel, FakeSshConnection
from test_terminal_endings import AsgiSocket, _left_since, _snapshot

from sshler import state, webapp
from sshler.api import remote
from sshler.ptyspawn import spawn_on_pty
from sshler.terminal.local import LocalPtyTransport
from sshler.webapp import LocalPTYProcess, ServerSettings, make_app
from sshler.win_terminal_registry import TooManyTerminalsError

pytestmark = pytest.mark.posix_only("runs /bin/sh on PTYs and counts its processes")

TOKEN = "plain-token"
REMOTE_BOX = "lb"
FAKE_REMOTE_BOX = "demo-box"
PLAIN = json.dumps({"op": "shell", "kind": "plain", "fallback": False})
FALLBACK = json.dumps({"op": "shell", "kind": "plain", "fallback": True})
TMUX = json.dumps({"op": "shell", "kind": "tmux", "fallback": False})
# A folder name holding a quote, a space and a newline.
HOSTILE_FOLDER = "it's a\nfolder"


# --- the app and its sockets ----------------------------------------------------------


def _query(folder: Path | str, *, host: str = "local", shell: str = "plain", session: str = "s1"):
    return urlencode(
        {
            "host": host,
            "dir": str(folder),
            "session": session,
            "cols": 80,
            "rows": 24,
            "token": TOKEN,
            "shell": shell,
        }
    )


def _sends(sock: AsgiSocket) -> list[dict[str, Any]]:
    return [m for m in sock.sent if m["type"] == "websocket.send"]


def _shell_frames(sock: AsgiSocket) -> list[str]:
    found = []
    for text in sock.texts():
        with contextlib.suppress(ValueError):
            if json.loads(text).get("op") == "shell":
                found.append(text)
    return found


def _ops(sock: AsgiSocket) -> list[str]:
    ops = []
    for text in sock.texts():
        with contextlib.suppress(ValueError):
            ops.append(json.loads(text).get("op"))
    return ops


async def _wait_match(sock: AsgiSocket, pattern: bytes, seconds: float = 10.0) -> re.Match[bytes]:
    async with asyncio.timeout(seconds):
        while (found := re.search(pattern, sock.output())) is None:
            sock.changed.clear()
            await sock.changed.wait()
    return found


async def _run(sock: AsgiSocket, line: str, word: str) -> None:
    """Send *line*, then wait until the shell has run it: ``word-2`` is printed only by
    the shell's arithmetic, never by the echo of the line typed."""
    sock.send_bytes(f"{line}; echo {word}-$((1+1))\n".encode())
    await sock.wait_output(f"{word}-2".encode())


def _session_members(sid: int) -> int:
    """How many processes live in session *sid*."""
    count = 0
    for process in psutil.process_iter():
        with contextlib.suppress(ProcessLookupError, PermissionError, psutil.Error):
            if os.getsid(process.pid) == sid:
                count += 1
    return count


async def _start_sleeper(sock: AsgiSocket) -> tuple[int, psutil.Process]:
    """The shell's pid, and a ``sleep 1000`` it runs in the foreground."""
    sock.send_bytes(b"echo shell-$$-up\n")
    shell = int((await _wait_match(sock, rb"shell-(\d+)-up")).group(1))
    sock.send_bytes(b"sh -c 'echo sleeper-$$-up; exec sleep 1000'\n")
    sleeper = int((await _wait_match(sock, rb"sleeper-(\d+)-up")).group(1))
    return shell, psutil.Process(sleeper)


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def record(session: str, directory: str) -> None:
        calls.append((session, directory))

    monkeypatch.setattr("sshler.webapp.record_ts_history", record)
    return calls


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history: list[tuple[str, str]]
) -> Iterator[Any]:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {FAKE_REMOTE_BOX}\n  HostName demo.internal\n  User demo\n")
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setenv("SHELL", "/bin/sh")
    state.reset_state()
    state.initialize(config_dir)
    yield make_app(ServerSettings(csrf_token=TOKEN, serve_spa=False, snapshot_enabled=False))


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


async def _warm(app: Any, workdir: Path) -> None:
    """One whole plain session first, so whatever opens once per process predates a count."""
    sock = AsgiSocket(app, _query(workdir, session="warm-up"))
    sock.start()
    await _run(sock, "true", "warm")
    sock.disconnect()
    await sock.finished()


# --- the door -------------------------------------------------------------------------


REFUSED = {
    "unknown-word": "bash",
    "upper-case": "PLAIN",
    "very-long": "p" * 5000,
    "newline": "plain\n",
    "escape": "tmux\x1b]52;c;QUJD\x07",
    "nul": "plain\x00",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["local", FAKE_REMOTE_BOX])
@pytest.mark.parametrize("shell", list(REFUSED.values()), ids=list(REFUSED))
async def test_a_shell_value_that_is_not_known_is_refused_before_anything_opens(
    app, fake_tmux, workdir: Path, monkeypatch: pytest.MonkeyPatch, host: str, shell: str
) -> None:
    connects: list[str] = []

    async def connect(*args: Any, **_kwargs: Any) -> Any:
        connects.append(str(args[0]))
        raise AssertionError("a refused shell value must open no connection")

    monkeypatch.setattr("sshler.webapp.connect", connect)
    sock = AsgiSocket(app, _query(workdir, host=host, shell=shell))
    sock.start()

    await sock.finished()

    assert sock.sent == [{"type": "websocket.close", "code": 4400, "reason": "Invalid shell"}]
    assert (fake_tmux.calls(), connects) == ([], [])


@pytest.mark.asyncio
async def test_a_plain_shell_still_needs_a_session_name_that_passes(
    app, fake_tmux, workdir: Path
) -> None:
    sock = AsgiSocket(app, _query(workdir, session=""))
    sock.start()

    await sock.finished()

    assert sock.sent == [
        {"type": "websocket.close", "code": 4400, "reason": "Invalid session name"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["pwsh", "no-such-shell-id", "plain"])
async def test_a_windows_local_box_keeps_its_own_shell_ids(
    app, workdir: Path, monkeypatch: pytest.MonkeyPatch, shell: str
) -> None:
    asked: list[tuple[str, str]] = []

    class FullRegistry:
        async def get_or_create(self, key, spawn, cols, rows):
            asked.append(key)
            raise TooManyTerminalsError("cap reached")

    monkeypatch.setattr(webapp, "LOCAL_IS_WINDOWS", True)
    app.state.win_terminal_registry = FullRegistry()
    sock = AsgiSocket(app, _query(workdir, shell=shell))
    sock.start()

    await sock.finished()

    assert (asked, sock.closes()) == ([("local", "s1")], [(4504, "Too many terminals")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "closed"),
    [(FAKE_REMOTE_BOX, (4400, "Invalid shell")), ("no-such-box", (4404, "Unknown box"))],
    ids=["remote-box", "unknown-box"],
)
async def test_on_a_windows_server_a_windows_shell_id_is_for_the_local_box_only(
    app, workdir: Path, monkeypatch: pytest.MonkeyPatch, host: str, closed: tuple[int, str]
) -> None:
    monkeypatch.setattr(webapp, "LOCAL_IS_WINDOWS", True)
    sock = AsgiSocket(app, _query(workdir, host=host, shell="pwsh"))
    sock.start()

    await sock.finished()

    assert sock.closes() == [closed]


# --- a plain shell on the local box ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_local_plain_shell_is_a_login_shell_in_the_folder_and_asks_tmux_nothing(
    app, fake_tmux, workdir: Path, tmp_path: Path, history: list[tuple[str, str]]
) -> None:
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(workdir))
    sock.start()
    await _run(sock, f'pwd > {seen}; echo "$0" >> {seen}', "where")

    sock.send_text(json.dumps({"op": "resize", "cols": 100, "rows": 40}))
    await _run(sock, "stty size", "sized")
    sock.disconnect()
    await sock.finished()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": PLAIN}
    assert seen.read_text() == f"{workdir}\n-sh\n"
    assert sock.output().count(b"\r\n40 100\r\n") == 1
    assert (_shell_frames(sock), _ops(sock)) == ([PLAIN], ["shell"])
    assert (fake_tmux.calls(), history) == ([], [])


@pytest.mark.asyncio
async def test_closing_the_socket_hangs_up_the_shell_and_what_it_started(
    app, fake_tmux, workdir: Path
) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _query(workdir))
    sock.start()
    shell, sleeper = await _start_sleeper(sock)

    sock.disconnect()
    await sock.finished()
    _, alive = psutil.wait_procs([sleeper], timeout=10)

    assert alive == []
    assert _session_members(shell) == 0
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_shell_that_exits_at_once_closes_the_socket_as_shell_exited(
    app, fake_tmux, workdir: Path
) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _query(workdir))
    sock.start()
    sock.send_bytes(b"exit\n")

    await sock.finished()

    assert sock.closes() == [(1000, "shell exited")]
    assert _sends(sock)[0] == {"type": "websocket.send", "text": PLAIN}
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_the_socket_closing_while_the_shell_floods_leaves_nothing(
    app, fake_tmux, workdir: Path
) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _query(workdir))
    sock.start()
    sock.send_bytes(b"yes flood-line\n")
    await sock.wait_output(b"flood-line\r\n", count=500)

    sock.disconnect()
    await sock.finished()

    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_local_plain_shell_opens_in_a_folder_with_a_quote_a_space_and_a_newline(
    app, fake_tmux, tmp_path: Path
) -> None:
    folder = tmp_path / HOSTILE_FOLDER
    folder.mkdir()
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(folder))
    sock.start()
    await _run(sock, f"pwd > {seen}", "where")
    sock.disconnect()
    await sock.finished()

    assert seen.read_text() == f"{folder}\n"


@pytest.mark.asyncio
async def test_a_shell_program_that_is_missing_gives_the_fallback_shell(
    app, fake_tmux, workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", str(tmp_path / "no-such-shell"))
    monkeypatch.setattr(
        "sshler.terminal.local._passwd_shell", lambda: str(tmp_path / "no-passwd-shell")
    )
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(workdir))
    sock.start()
    await _run(sock, f'echo "$0" > {seen}', "which")
    sock.disconnect()
    await sock.finished()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": PLAIN}
    assert seen.read_text() == "-sh\n"


@pytest.mark.asyncio
async def test_two_plain_shells_on_one_box_are_each_their_own(
    app, fake_tmux, workdir: Path
) -> None:
    first = AsgiSocket(app, _query(workdir))
    second = AsgiSocket(app, _query(workdir))
    first.start()
    second.start()
    first.send_bytes(b"echo one-$$-up\n")
    second.send_bytes(b"echo two-$$-up\n")
    one = int((await _wait_match(first, rb"one-(\d+)-up")).group(1))
    two = int((await _wait_match(second, rb"two-(\d+)-up")).group(1))
    first.disconnect()
    second.disconnect()
    await first.finished()
    await second.finished()

    assert one != two
    assert (first.output().count(b"two-"), second.output().count(b"one-")) == (0, 0)


def _session_rows(box: str) -> list[tuple[str, bool]]:
    return [(row.session_name, row.active) for row in state.list_sessions(box)]


@pytest.mark.asyncio
async def test_a_plain_shell_is_not_a_session_and_leaves_the_tmux_session_of_its_name_alone(
    app, fake_tmux, workdir: Path
) -> None:
    tmux = AsgiSocket(app, _query(workdir, shell="tmux"))
    tmux.start()
    tmux.send_bytes(b"typed\n")
    await tmux.wait_output(b"typed", count=2)
    plain = AsgiSocket(app, _query(workdir))
    plain.start()
    await _run(plain, "true", "ran")
    while_open = _session_rows("local")
    plain.disconnect()
    await plain.finished()
    after_close = _session_rows("local")
    tmux.disconnect()
    await tmux.finished()

    assert (while_open, after_close) == ([("s1", True)], [("s1", True)])


@pytest.mark.asyncio
async def test_a_local_folder_that_cannot_be_entered_opens_the_shell_in_the_default_folder(
    app, fake_tmux, tmp_path: Path
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root enters any folder")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(locked))
    sock.start()
    try:
        await _run(sock, f"pwd > {seen}", "where")
    finally:
        locked.chmod(0o700)
    sock.disconnect()
    await sock.finished()

    assert seen.read_text() == f"{Path.home()}\n"
    assert sock.closes() == []


# --- a local box without tmux ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["", "tmux"], ids=["nothing-asked", "tmux-asked"])
async def test_a_local_box_without_tmux_opens_a_plain_shell_in_its_place(
    app, fake_tmux, workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: str
) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # no tmux, and no program at all, on PATH
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _query(workdir, shell=shell))
    sock.start()
    await _run(sock, "true", "fell-back")
    sock.disconnect()
    await sock.finished()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": FALLBACK}
    assert (_shell_frames(sock), sock.closes(), fake_tmux.calls()) == ([FALLBACK], [], [])
    assert state.list_sessions("local") == []
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_local_tmux_terminal_says_so_once_before_its_output(
    app, fake_tmux, workdir: Path
) -> None:
    sock = AsgiSocket(app, _query(workdir, shell=""))
    sock.start()
    sock.send_bytes(b"typed\n")
    await sock.wait_output(b"typed", count=2)
    sock.disconnect()
    await sock.finished()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": TMUX}
    assert _shell_frames(sock) == [TMUX]


@pytest.mark.asyncio
async def test_a_remote_tmux_terminal_says_so_once_before_its_output(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, channel = FakeSshConnection(), FakeSshChannel()

    async def connect(*_args: Any, **_kwargs: Any) -> FakeSshConnection:
        return connection

    async def is_directory(_connection: Any, _path: str) -> bool:
        return True

    async def open_tmux(_connection: Any, **_kwargs: Any) -> FakeSshChannel:
        return channel

    monkeypatch.setattr("sshler.webapp.connect", connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux)
    sock = AsgiSocket(app, _query("/srv", host=FAKE_REMOTE_BOX, shell="tmux"))
    sock.start()
    sock.send_bytes(b"echo ready\n")
    await sock.wait_output(b"ready\r\n", count=2)
    sock.disconnect()
    await sock.finished()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": TMUX}
    assert _shell_frames(sock) == [TMUX]
    assert connection.commands[0] == TMUX_CHECK_LINE


# --- the loopback SSH server ----------------------------------------------------------


class Sshd:
    """What sshd does with a session, on the loopback interface.

    Each command line runs as ``/bin/sh -c LINE`` with ``SHELL=/bin/sh``, this server's
    ``HOME`` and a ``PATH`` holding only ``sh``, ``sleep``, ``stty`` and ``yes`` (no
    tmux). A session starts in ``HOME``. One that asked for a PTY runs on one; when its
    channel closes, the PTY is hung up and the child reaped, as sshd does.
    """

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.home.mkdir(parents=True)
        self.default = root / "default"
        self.default.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in ("sh", "sleep", "stty", "yes"):
            found = shutil.which(name)
            assert found is not None, name
            (self.bin / name).symlink_to(found)
        self.commands: list[str] = []
        self._live = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def _env(self, term: str | None) -> dict[str, str]:
        env = {"PATH": str(self.bin), "HOME": str(self.home), "SHELL": "/bin/sh"}
        if term:
            env["TERM"] = term
        return env

    async def settled(self, seconds: float = 10.0) -> None:
        """Wait until no session or connection of this server is left running."""
        await asyncio.wait_for(self._idle.wait(), seconds)

    def opened(self) -> None:
        self._live += 1
        self._idle.clear()

    def ended(self) -> None:
        self._live -= 1
        if self._live == 0:
            self._idle.set()

    async def session(self, process: asyncssh.SSHServerProcess) -> None:
        self.opened()
        try:
            await self._serve(process)
        finally:
            self.ended()

    async def _serve(self, process: asyncssh.SSHServerProcess) -> None:
        line = str(process.command or "")
        self.commands.append(line)
        if process.term_type is None:
            child = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                line,
                env=self._env(None),
                cwd=self.home,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await child.communicate()
            process.stdout.write(out)
            process.stderr.write(err)
            process.exit(child.returncode or 0)
            return
        cols, rows, _, _ = process.term_size
        master, pid = await asyncio.to_thread(
            spawn_on_pty,
            ["/bin/sh", "-c", line],
            env=self._env(process.term_type),
            rows=rows,
            cols=cols,
            cwd=str(self.home),
        )
        stdin = os.fdopen(master, "wb", buffering=0, closefd=False)
        stdout = os.fdopen(master, "rb", buffering=0, closefd=False)
        child_pty = LocalPTYProcess(master, pid, stdin, stdout)
        transport = LocalPtyTransport(child_pty, None)

        async def output() -> None:
            while data := await transport.read():
                process.stdout.write(data)

        async def input_() -> None:
            while True:
                try:
                    data = await process.stdin.read(4096)
                except asyncssh.TerminalSizeChanged as change:
                    await transport.resize(change.width, change.height)
                    continue
                except (asyncssh.Error, OSError):
                    return
                if not data:
                    return
                await transport.write(data)

        reading = asyncio.create_task(output())
        writing = asyncio.create_task(input_())
        try:
            await asyncio.wait([reading, writing], return_when=asyncio.FIRST_COMPLETED)
        finally:
            writing.cancel()
            # The hangup ends the read in flight, so the output task ends by itself.
            await transport.close()
            await asyncio.gather(reading, writing, return_exceptions=True)
        with contextlib.suppress(Exception):
            process.exit(child_pty.returncode or 0)


@pytest_asyncio.fixture
async def sshd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: Any) -> AsyncIterator[Sshd]:
    server_state = Sshd(tmp_path / "sshd")

    class Tracked(asyncssh.SSHServer):
        def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
            server_state.opened()

        def connection_lost(self, exc: Exception | None) -> None:
            server_state.ended()

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    keys = tmp_path / "keys"
    keys.mkdir()
    client_key_path = keys / "id_client"
    client_key.write_private_key(str(client_key_path))
    client_key_path.chmod(0o600)
    authorized = keys / "authorized_keys"
    client_key.write_public_key(str(authorized))
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=Tracked,
        server_host_keys=[host_key],
        authorized_client_keys=str(authorized),
        process_factory=server_state.session,
        sftp_factory=True,
        line_editor=False,
        encoding=None,
    )
    port = server.get_port()
    known_hosts = keys / "known_hosts"
    public = host_key.export_public_key("openssh").decode().strip()
    known_hosts.write_text(f"[127.0.0.1]:{port} {public}\n", encoding="utf-8")
    box = {
        "name": REMOTE_BOX,
        "host": "127.0.0.1",
        "user": "tester",
        "port": port,
        "keyfile": str(client_key_path),
        "known_hosts": str(known_hosts),
        "default_dir": str(server_state.default),
    }
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": [box]}), encoding="utf-8")
    try:
        yield server_state
    finally:
        server.close()
        await server.wait_closed()


async def _warm_remote(app: Any, sshd: Sshd) -> None:
    sock = AsgiSocket(app, _query(sshd.home, host=REMOTE_BOX, session="warm-up"))
    sock.start()
    await _run(sock, "true", "warm")
    sock.disconnect()
    await sock.finished()
    await sshd.settled()
    sshd.commands.clear()


# --- a plain shell on a remote box ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_remote_plain_shell_opens_in_the_folder_and_sends_no_tmux_command(
    app, sshd: Sshd, tmp_path: Path
) -> None:
    folder = tmp_path / HOSTILE_FOLDER
    folder.mkdir()
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(folder, host=REMOTE_BOX))
    sock.start()
    await _run(sock, f"pwd > {seen}", "where")

    sock.send_text(json.dumps({"op": "resize", "cols": 100, "rows": 40}))
    await _run(sock, "stty size", "sized")
    sock.disconnect()
    await sock.finished()
    await sshd.settled()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": PLAIN}
    assert seen.read_text() == f"{folder}\n"
    assert sock.output().count(b"\r\n40 100\r\n") == 1
    assert sshd.commands == [remote.login_shell_in(str(folder))]
    assert (_shell_frames(sock), _ops(sock)) == ([PLAIN], ["shell"])
    assert state.list_sessions(REMOTE_BOX) == []


@pytest.mark.asyncio
async def test_a_remote_plain_shell_in_a_folder_that_is_missing_opens_in_the_home_folder(
    app, sshd: Sshd, tmp_path: Path
) -> None:
    seen = tmp_path / "seen.txt"
    sock = AsgiSocket(app, _query(tmp_path / "no-such-folder", host=REMOTE_BOX))
    sock.start()
    await _run(sock, f"pwd > {seen}", "where")
    sock.disconnect()
    await sock.finished()
    await sshd.settled()

    assert seen.read_text() == f"{sshd.home}\n"
    assert sock.texts() == [PLAIN]


@pytest.mark.asyncio
async def test_closing_the_socket_hangs_up_the_remote_shell_and_what_it_started(
    app, sshd: Sshd
) -> None:
    await _warm_remote(app, sshd)
    before = _snapshot()
    sock = AsgiSocket(app, _query(sshd.home, host=REMOTE_BOX))
    sock.start()
    shell, sleeper = await _start_sleeper(sock)

    sock.disconnect()
    await sock.finished()
    await sshd.settled()
    _, alive = psutil.wait_procs([sleeper], timeout=10)

    assert alive == []
    assert _session_members(shell) == 0
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_remote_shell_that_exits_at_once_closes_the_socket_as_shell_exited(
    app, sshd: Sshd
) -> None:
    await _warm_remote(app, sshd)
    before = _snapshot()
    sock = AsgiSocket(app, _query(sshd.home, host=REMOTE_BOX))
    sock.start()
    sock.send_bytes(b"exit\n")

    await sock.finished()
    await sshd.settled()

    assert sock.closes() == [(1000, "shell exited")]
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["", "tmux"], ids=["nothing-asked", "tmux-asked"])
async def test_a_remote_box_without_tmux_opens_a_plain_shell_in_its_place(
    app, sshd: Sshd, shell: str
) -> None:
    await _warm_remote(app, sshd)
    before = _snapshot()
    sock = AsgiSocket(app, _query(sshd.home, host=REMOTE_BOX, shell=shell))
    sock.start()
    await _run(sock, "true", "fell-back")
    sock.disconnect()
    await sock.finished()
    await sshd.settled()

    assert _sends(sock)[0] == {"type": "websocket.send", "text": FALLBACK}
    assert (_shell_frames(sock), sock.closes()) == ([FALLBACK], [])
    assert sshd.commands == [TMUX_CHECK_LINE, remote.login_shell_in(str(sshd.home))]
    assert _left_since(before) == (0, 0, 0)
