import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sshler import (  # noqa: E402
    artifacts,
    claude_sessions,
    config_cache,
    rate_limit,
    snapshot,
    ssh_pool,
)
from sshler.api import dependencies as api_dependencies  # noqa: E402
from sshler.api import tunnels as api_tunnels  # noqa: E402
from sshler.session import reset_session_store  # noqa: E402
from sshler.settings import reset_settings  # noqa: E402
from sshler.state import reset_state  # noqa: E402

# POSIX PTY and tmux teardown tests import `pty`/`termios`, which Windows lacks.
collect_ignore = ["test_terminal_teardown.py"] if sys.platform == "win32" else []

E2E_DIR = Path(__file__).resolve().parent / "e2e"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark everything under tests/e2e so `-m "not e2e"` selects the unit suite."""
    for item in items:
        if E2E_DIR in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def tmux_tmpdir() -> Iterator[Path]:
    """A private ``TMUX_TMPDIR`` for the whole run, so no test reaches the user's tmux.

    Kept short because tmux socket paths must fit in ``sun_path`` (~104 bytes).
    Every tmux server left in it is killed at the end of the session.
    """
    path = Path(tempfile.mkdtemp(prefix="sshler-tmux-", dir="/tmp"))
    yield path
    socket_dir = path / f"tmux-{os.getuid()}"
    if socket_dir.is_dir() and shutil.which("tmux"):
        for socket in socket_dir.iterdir():
            subprocess.run(
                ["tmux", "-S", str(socket), "kill-server"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    shutil.rmtree(path, ignore_errors=True)


def _reset_process_globals() -> None:
    reset_state()
    reset_session_store()
    reset_settings()
    rate_limit._rate_limiters.clear()
    config_cache._global_cache = None
    artifacts.clear_page_cache()
    claude_sessions._CACHE.clear()
    claude_sessions._GIT_ROOT_CACHE.clear()
    snapshot._recovery_sessions.clear()
    api_tunnels._active_tunnels.clear()
    api_dependencies._CONNECT_FAIL_CACHE.clear()
    ssh_pool._global_pool = None


@pytest.fixture(autouse=True)
def _hermetic_env(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmux_tmpdir: Path,
) -> Iterator[None]:
    """Isolate every test from the developer's machine and from other tests.

    Config and Claude dirs point at the test's tmp dir, ``$TMUX`` is unset (so a
    developer running pytest inside tmux sees the same behaviour as CI), tmux
    sockets live in a private dir, and process-global caches start empty.
    Unit tests also get a throwaway ``HOME`` so a spawned tmux never loads the
    user's ``~/.tmux.conf`` and nothing can write into the real home. E2E tests
    keep the real ``HOME`` because Playwright finds its browsers there; their
    server subprocess is isolated in ``tests/e2e/conftest.py``.
    """
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path / "sshler-config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmpdir))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    if request.node.get_closest_marker("e2e") is None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    _reset_process_globals()
    yield
    _reset_process_globals()


FAKE_TMUX_SCRIPT = r"""
log="$1"; shift
# Drop the "-L <socket>" prefix that local_tmux_command() adds.
if [ "$1" = "-L" ]; then shift 2; fi
printf '%s\n' "$*" >> "$log"
case "$1" in
  new) echo $$ > "$log.pid"; exec cat ;;
  *) exit 0 ;;
esac
"""


class FakeTmux:
    """Stands in for the tmux binary: ``new`` runs ``cat`` on the PTY, all else exits 0.

    The real PTY spawn, websocket bridge and teardown run unchanged; only the
    program at the end of the PTY differs. Every invocation's arguments are
    appended to ``log`` so tests can assert the exact argv sshler built.
    """

    def __init__(self, log: Path) -> None:
        self.log = log

    def command(self, session: str) -> list[str]:
        return ["sh", "-c", FAKE_TMUX_SCRIPT, "fake-tmux", str(self.log), "-L", f"ts-{session}"]

    def default_command(self) -> list[str]:
        """Stand-in for ``default_tmux_command``: the user's default server, faked."""
        return ["sh", "-c", FAKE_TMUX_SCRIPT, "fake-tmux", str(self.log)]

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeTmux:
    fake = FakeTmux(tmp_path / "fake-tmux.log")
    # raising=True (the default): a renamed or removed name fails loudly here.
    # Every module that builds tmux argv must be listed; the tripwire test in
    # tests/test_fake_tmux.py proves the list is complete for the modules below.
    monkeypatch.setattr("sshler.tmux.local_tmux_command", fake.command)
    monkeypatch.setattr("sshler.tmux.default_tmux_command", fake.default_command)
    monkeypatch.setattr("sshler.webapp.local_tmux_command", fake.command)
    monkeypatch.setattr("sshler.snapshot.local_tmux_command", fake.command)
    return fake


@pytest.fixture
def tmux_tripwire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Put a ``tmux`` first on PATH that only records its argv, so a real tmux can never run."""
    bin_dir = tmp_path / "tripwire-bin"
    bin_dir.mkdir()
    log = tmp_path / "tripwire.log"
    script = bin_dir / "tmux"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 1\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log
