"""Client commands must start without loading the server."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_PROGRAM = """
import json
import sys

argv = json.loads(sys.argv[1])
import sshler.cli

code = None
if argv is not None:
    sys.argv = ["sshler", *argv]
    try:
        sshler.cli.main()
    except SystemExit as stop:
        code = stop.code
names = ["sshler.cli.server", "sshler.cli.password", "sshler.webapp", "sshler.state",
         "sshler.config", "sshler.auth", "uvicorn", "fastapi", "starlette", "asyncssh",
         "sqler", "pydantic"]
print(json.dumps([code, sorted(n for n in names if n in sys.modules)]))
"""

# Nothing listens on port 1: a command that reaches its request is refused and exits 1.
# A command that stopped at its arguments would exit 2 and never run its handler.
_CONNECT = ["--url", "http://127.0.0.1:1", "--token", "x"]


@pytest.mark.parametrize(
    ("argv", "exit_code"),
    [
        pytest.param(None, None, id="bare-import"),
        pytest.param(["progress", "list", *_CONNECT], 1, id="progress-list"),
        pytest.param(["ping", "--title", "hello", *_CONNECT], 1, id="ping"),
        pytest.param(["artifact", "list", *_CONNECT], 1, id="artifact-list"),
    ],
)
def test_client_command_loads_no_server_module(
    argv: list[str] | None, exit_code: int | None
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROGRAM, json.dumps(argv)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [exit_code, []]
