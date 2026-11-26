"""The check that begins a remote command line, proved against a real shell.

A shell gives a pipeline the status of its last command, so a line that pipes a program
into ``head`` exits 0 when the program is not there. The lines of a route that need that
program begin by checking for it and exit 127 (the shell's status for a command it
cannot find). The fakes of the other tests answer from the words of a line; this file
runs exactly three lines with ``/bin/sh``, each with a ``PATH`` of one pytest folder: the
discovery line built from a folder of the test and the pattern ``*report*``, and the
fixed texts ``REMOTE_LIST_SESSIONS`` (with a stand-in ``tmux`` and ``id``, and the
socket folder of the test) and ``REMOTE_FORCE_KILL`` (with a stand-in ``tmux`` whose
panes are processes this test starts).
"""

from __future__ import annotations

import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from sshler import tmux
from sshler.api import sessions as sessions_api
from sshler.api.search import remote_discovery_command
from sshler.api.sessions import REMOTE_LIST_SESSIONS
from sshler.tmux import REMOTE_FORCE_KILL, remote_force_kill_words

pytestmark = pytest.mark.posix_only("/bin/sh runs the command lines with a PATH of a temp folder")

PROGRAM_NOT_FOUND = 127


def _sh(
    line: str, programs: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", line, "sh", *args],
        env={"PATH": str(programs), **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


def _link(programs: Path, name: str) -> None:
    found = shutil.which(name)
    assert found is not None, name
    (programs / name).symlink_to(found)


@pytest.fixture
def programs(tmp_path: Path) -> Path:
    folder = tmp_path / "programs"
    folder.mkdir()
    return folder


def test_a_discovery_line_on_a_box_without_find_exits_127(tmp_path: Path, programs: Path):
    (tmp_path / "tree" / "reports").mkdir(parents=True)
    line = remote_discovery_command(str(tmp_path / "tree"), "*report*", 4, 50, False)
    _link(programs, "head")  # head is there: it would give the pipeline status 0
    ran = _sh(line, programs)
    assert (ran.returncode, ran.stdout) == (PROGRAM_NOT_FOUND, "")


def test_a_discovery_line_on_a_box_with_find_gives_the_matching_folder(
    tmp_path: Path, programs: Path
):
    tree = tmp_path / "tree"
    (tree / "reports").mkdir(parents=True)
    (tree / "other").mkdir()
    line = remote_discovery_command(str(tree), "*report*", 4, 50, False)
    for name in ("find", "head"):
        _link(programs, name)
    ran = _sh(line, programs)
    assert (ran.returncode, ran.stdout) == (0, f"{tree}/reports\n")


def test_the_session_listing_on_a_box_without_tmux_exits_127(programs: Path):
    ran = _sh(REMOTE_LIST_SESSIONS, programs)
    assert (ran.returncode, ran.stdout) == (PROGRAM_NOT_FOUND, "")


def _program(programs: Path, name: str, text: str) -> None:
    path = programs / name
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_the_session_listing_on_a_box_with_no_sessions_exits_0_with_no_output(programs: Path):
    _program(programs, "tmux", "#!/bin/sh\necho 'no server running on /x/default' >&2\nexit 1\n")
    ran = _sh(REMOTE_LIST_SESSIONS, programs)
    assert (ran.returncode, ran.stdout) == (0, "")


# The stand-in tmux of a box with three ts servers: ts-fine has the session "fine",
# ts-stale is a socket whose server is gone, ts-slow does not answer; the default server
# is not running. ``id -u`` answers 4242, so the socket folder is the test's own.
BOX_TMUX = """#!/bin/sh
case "$2" in
  ts-fine) echo fine ;;
  ts-slow) exec sleep 30 ;;
  *) echo "no server running on /x/$2" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def box(tmp_path: Path, programs: Path) -> dict[str, str]:
    """The box of ``BOX_TMUX``: the environment the listing runs in."""
    _program(programs, "tmux", BOX_TMUX)
    _program(programs, "id", "#!/bin/sh\necho 4242\n")
    for name in ("timeout", "sleep"):
        _link(programs, name)
    sockets = tmp_path / "tmux" / "tmux-4242"
    sockets.mkdir(parents=True)
    for server in ("ts-fine", "ts-stale", "ts-slow"):
        with socket.socket(socket.AF_UNIX) as bound:
            bound.bind(str(sockets / server))
    return {"TMUX_TMPDIR": str(tmp_path / "tmux")}


def test_the_session_listing_says_by_its_status_that_a_server_could_not_be_asked(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, env=box)
    assert (ran.returncode, ran.stdout) == (sessions_api.REMOTE_LISTING_INCOMPLETE, "fine\n")


def test_the_listing_of_one_session_asks_its_own_server_and_the_default_one(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, "fine", env=box)
    assert (ran.returncode, ran.stdout) == (0, "fine\n")


def test_the_listing_of_a_session_whose_server_does_not_answer_says_it_could_not_ask(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, "slow", env=box)
    assert (ran.returncode, ran.stdout) == (sessions_api.REMOTE_LISTING_INCOMPLETE, "")


# The stand-in tmux of a box where ``ts`` started the session "own" on its own server
# ts-own, in a folder with a space, a tab and a non-ASCII character, and a session
# "stray" on that server too; the default server has "legacy". It answers only when the
# script asks for ``tmux.LIST_FORMAT``, and expands it as tmux 3.4 does.
FOLDER_TMUX = r"""#!/bin/sh
if [ "$1" = "-L" ]; then server=$2; shift 2; else server=default; fi
[ "$1 $2 $3" = "list-sessions -F $LIST_FORMAT" ] || exit 3
socket="$TMUX_TMPDIR/tmux-4242/$server"
case $server in
  ts-own) printf 'S\t%s\town\t/srv/a b\tc\303\251\tE\n' "$socket"
          printf 'S\t%s\tstray\t/srv\tE\n' "$socket" ;;
  default) printf 'S\t%s\tlegacy\t/srv\tE\n' "$socket" ;;
esac
"""


def test_the_session_listing_says_the_folder_of_a_session_on_its_own_server(
    tmp_path: Path, programs: Path
):
    _program(programs, "tmux", FOLDER_TMUX)
    _program(programs, "id", "#!/bin/sh\necho 4242\n")
    sockets = tmp_path / "tmux" / "tmux-4242"
    sockets.mkdir(parents=True)
    with socket.socket(socket.AF_UNIX) as bound:
        bound.bind(str(sockets / "ts-own"))
    env = {"TMUX_TMPDIR": str(tmp_path / "tmux"), "LIST_FORMAT": tmux.LIST_FORMAT}

    ran = _sh(REMOTE_LIST_SESSIONS, programs, env=env)
    seen = tmux.parse_listing(ran.stdout)

    assert (ran.returncode, seen.names, seen.complete) == (
        0,
        frozenset({"own", "stray", "legacy"}),
        True,
    )
    assert dict(seen.folders) == {"own": "/srv/a b\tc\u00e9"}


@pytest.mark.parametrize(
    ("output", "names", "folders"),
    [
        ("fine\nother\n", {"fine", "other"}, {}),
        ("S\t\tfine\t/srv\tE\n", {"fine"}, {}),
        (
            "S\t/s/ts-fine\tfine\t/srv/two\nlines\tE\nS\t/s/ts-ok\tok\t/o\tE\n",
            {"fine", "ok"},
            {"ok": "/o"},
        ),
        ("S\t/s/ts-fine\tfine\t/a\nb\nc\tE\n", {"fine"}, {}),
        ("S\t/s/ts-fi\nne\tfine\t/a\tE\n", set(), {}),
    ],
    ids=[
        "names-only",
        "no-socket-path",
        "newline-folder",
        "folder-of-three-lines",
        "newline-before-the-name",
    ],
)
def test_a_session_whose_listing_line_is_split_is_still_seen_but_has_no_folder(
    output: str, names: set[str], folders: dict[str, str]
):
    """A folder with a newline in its name splits its line: tmux has the session, so
    its name counts (a row of it stays active), but its folder cannot be read, so no row
    is made from it."""
    seen = tmux.parse_listing(output)
    assert (seen.names, dict(seen.folders), seen.complete) == (frozenset(names), folders, True)


@pytest.mark.parametrize(
    ("output", "folders"),
    [
        ("S\t/s/ts-fine\tfine\t/a\u2028b\x0cc\x85d\tE\n", {"fine": "/a\u2028b\x0cc\x85d"}),
        ("S\t/s/ts-fine\tfine\t/a\tE\r\nS\t/s/ts-ok\tok\t/o\tE\r\n", {"fine": "/a", "ok": "/o"}),
    ],
    ids=["other-separators-in-a-folder", "windows-line-ends"],
)
def test_only_a_newline_ends_a_listing_line(output: str, folders: dict[str, str]):
    """tmux ends a session's line with a newline and nothing else: a folder holding a
    form feed or a Unicode line separator is one line, and a carriage return before
    the newline is not part of the line."""
    seen = tmux.parse_listing(output)
    assert (seen.names, dict(seen.folders)) == (frozenset(folders), folders)


# --- the remote force kill, against a stand-in tmux ------------------------------------

# The stand-in tmux of a box whose session "work" lives on its own server ts-work: it
# writes each command it gets to $TMUX_LOG and lists the pane ids in $PANES for
# ts-work. Its kill-server sends no signal, so a pane process that ignores SIGHUP is
# gone afterwards only if the script killed it.
FORCE_TMUX = """#!/bin/sh
echo "$*" >> "$TMUX_LOG"
case "$1 $2 $3" in
  "-L ts-work list-panes") cat "$PANES" ;;
  "-L ts-work kill-server") ;;
  *) echo "no server running on /x/$2" >&2; exit 1 ;;
esac
"""

IGNORES_HUP = (
    "import signal, sys, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); "
    "print('ready', flush=True); time.sleep(60)"
)


def _ignoring_hup() -> subprocess.Popen[str]:
    """A process in a group of its own that ignores SIGHUP, started by this test."""
    process = subprocess.Popen(
        [sys.executable, "-c", IGNORES_HUP],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline() == "ready\n"
    return process


@pytest.fixture
def force_box(tmp_path: Path, programs: Path) -> dict[str, str]:
    _program(programs, "tmux", FORCE_TMUX)
    for name in ("ps", "cat"):  # cat for the stand-in, ps for the script
        _link(programs, name)
    (tmp_path / "panes").write_text("", encoding="utf-8")
    return {"TMUX_LOG": str(tmp_path / "tmux.log"), "PANES": str(tmp_path / "panes")}


@pytest.fixture
def processes() -> Iterator[list[subprocess.Popen[str]]]:
    """The processes a test starts; each still alive at the end is stopped by its id."""
    started: list[subprocess.Popen[str]] = []
    yield started
    for process in started:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def _force_kill(programs: Path, box: dict[str, str], session: str):
    words = remote_force_kill_words(session)
    assert words[:2] == ["sh", "-c"]
    return _sh(words[2], programs, *words[4:], env=box)


def test_the_force_kill_on_a_box_without_tmux_exits_127(programs: Path):
    ran = _sh(REMOTE_FORCE_KILL, programs, "ts-work")
    assert (ran.returncode, ran.stdout) == (PROGRAM_NOT_FOUND, "")


def test_the_force_kill_ends_a_pane_that_ignores_sighup_and_no_other_process(
    tmp_path: Path, programs: Path, force_box: dict[str, str], processes
):
    victim = _ignoring_hup()
    bystander = _ignoring_hup()  # a pane of the default server's session "work"
    processes.extend([victim, bystander])
    (tmp_path / "panes").write_text(f"{victim.pid}\n", encoding="utf-8")
    ran = _force_kill(programs, force_box, "work")
    assert (ran.returncode, ran.stdout) == (0, "")
    assert victim.wait(timeout=10) == -9
    assert bystander.poll() is None
    assert (tmp_path / "tmux.log").read_text(encoding="utf-8").splitlines() == [
        "-L ts-work list-panes -a -F #{pane_pid}",
        "-L ts-work kill-server",
    ]


@pytest.mark.parametrize("panes", ["", "\n", "{pid} trailing\n", "x{pid}\n", "0\n1\n"], ids=repr)
def test_the_force_kill_signals_no_process_tmux_did_not_list_as_a_pane_id(
    tmp_path: Path, programs: Path, force_box: dict[str, str], processes, panes: str
):
    bystander = _ignoring_hup()
    processes.append(bystander)
    (tmp_path / "panes").write_text(panes.format(pid=bystander.pid), encoding="utf-8")
    ran = _force_kill(programs, force_box, "work")
    assert (ran.returncode, ran.stdout) == (0, "")
    assert bystander.poll() is None


def test_the_force_kill_of_a_session_that_is_gone_is_not_an_error(
    tmp_path: Path, programs: Path, force_box: dict[str, str]
):
    ran = _force_kill(programs, force_box, "gone")
    assert (ran.returncode, ran.stdout) == (0, "")
    assert (tmp_path / "tmux.log").read_text(encoding="utf-8").splitlines() == [
        "-L ts-gone list-panes -a -F #{pane_pid}",
        "-L ts-gone kill-server",
    ]


@pytest.mark.parametrize("server", ["", "ts-", "default", "-L"], ids=repr)
def test_the_force_kill_sends_kill_server_only_to_a_ts_server(
    tmp_path: Path, programs: Path, force_box: dict[str, str], server: str
):
    ran = _sh(REMOTE_FORCE_KILL, programs, server, env=force_box)
    assert ran.returncode == 2
    assert not (tmp_path / "tmux.log").exists()


@pytest.mark.parametrize("name", ["a b", "it's", 'a"b', "$(id)", "a;b"], ids=repr)
def test_a_hostile_session_name_reaches_the_force_kill_as_one_word(name: str):
    line = shlex.join(remote_force_kill_words(name))
    assert shlex.split(line) == ["sh", "-c", REMOTE_FORCE_KILL, "sh", f"ts-{name}"]
