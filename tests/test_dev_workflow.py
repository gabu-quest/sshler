"""Tests for the development workflow (``sshler serve --dev``).

``serve_dev`` runs against a ``tmp_path`` project layout, never the repo tree.
Only the two process boundaries are replaced: the Vite subprocess starter and
``uvicorn.run``. Everything between them (origin merging, the reload payload,
signal handling, cleanup) is the real code.

The tests do not measure "auto-reload within a reasonable time": uvicorn owns
file watching, and no test here starts it.
"""

import json
import os
import signal
import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

import sshler
from sshler import cli
from sshler.cli import _cleanup_processes, _start_vite_dev_server, serve_dev

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@pytest.fixture(autouse=True)
def reload_env_key_restored(monkeypatch):
    """Fail any test that leaves ``_RELOAD_ENV_KEY`` in a different state than it found it.

    ``serve_dev()`` exports the reload payload into ``os.environ``; if a test does not
    arrange for its restoration the value leaks into every later test in the process.
    It requests ``monkeypatch`` and undoes it explicitly before checking, because the
    conftest's autouse fixtures hold the same monkeypatch and tear down later.
    """
    before = os.environ.get(cli._RELOAD_ENV_KEY)
    yield
    monkeypatch.undo()
    assert os.environ.get(cli._RELOAD_ENV_KEY) == before, "test leaked _RELOAD_ENV_KEY"


@pytest.fixture
def dev_project(tmp_path, monkeypatch):
    """A project root with an empty ``frontend/`` as cwd; signals and PID file isolated."""
    project = tmp_path / "project"
    (project / "frontend").mkdir(parents=True)
    monkeypatch.chdir(project)
    # serve() writes and removes the PID file; keep it off the real /tmp/sshler.pid.
    monkeypatch.setattr(cli, "_PID_FILE", tmp_path / "sshler.pid")
    # setenv first so monkeypatch records the pre-test state (absent) and deletes the
    # key again at teardown; delenv(raising=False) alone records nothing when absent.
    monkeypatch.setenv(cli._RELOAD_ENV_KEY, "")
    monkeypatch.delenv(cli._RELOAD_ENV_KEY)
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield project
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.fixture
def vite():
    """Replace only the Vite subprocess starter; returns the fake running process."""
    process = Mock()
    process.poll.return_value = None
    process.stdout = None
    # Replace the ``time`` name inside ``sshler.cli`` only (serve_dev needs just
    # ``sleep``); the global ``time`` module is untouched.
    with patch("sshler.cli._start_vite_dev_server", return_value=process) as starter, patch.object(
        cli, "time", types.SimpleNamespace(sleep=Mock())
    ):
        process.starter = starter
        yield process


class TestServeDev:
    def test_reload_app_factory_trusts_vite_origins(self, dev_project, vite):
        """serve_dev hands uvicorn a reloading factory whose app accepts the Vite origins.

        Mutations killed: dev origins not appended (Vite POSTs get 403), reload
        disabled, reload payload not exported for the factory, target no longer
        the ``_reload_app`` factory, ``reload_dirs`` omitted (uvicorn then watches
        the cwd, here ``dev_project``, so backend edits never reload).
        """
        assert Path.cwd() != Path(sshler.__file__).resolve().parent
        with patch("sshler.cli.uvicorn.run") as run:
            serve_dev(port=8123, token="dev-token", open_browser=False)

        run.assert_called_once_with(
            "sshler.cli:_reload_app",
            host="127.0.0.1",
            port=8123,
            reload=True,
            log_level="info",
            factory=True,
            reload_dirs=[str(Path(sshler.__file__).resolve().parent)],
        )
        # uvicorn would now import the factory in its reloader child; do the same.
        client = TestClient(cli._reload_app())
        headers = {"X-SSHLER-TOKEN": "dev-token"}
        try:
            allowed = client.post(
                "/api/v1/boxes/local/pin", headers={**headers, "Origin": "http://localhost:5173"}
            )
            denied = client.post(
                "/api/v1/boxes/local/pin", headers={**headers, "Origin": "http://evil.example:5173"}
            )
        finally:
            client.close()
        assert allowed.status_code == 200
        assert denied.status_code == 403

    @pytest.mark.parametrize("user_origins", [[], ["https://dev.local:4443"]])
    def test_dev_origins_are_appended_after_user_origins(self, dev_project, vite, user_origins):
        """User origins keep their place; Vite origins and the listen address follow.

        Mutations killed: user origins dropped, Vite origins omitted, Vite origins
        placed before the user's.
        """
        with patch("sshler.cli.uvicorn.run"):
            serve_dev(port=9001, allow_origins=user_origins, open_browser=False)

        origins = json.loads(cli.os.environ[cli._RELOAD_ENV_KEY])["allow_origins"]
        count = len(user_origins)
        assert origins[:count] == user_origins
        # The tail is a deduplicated set (order not specified): Vite's two origins
        # plus the listen address as 127.0.0.1 and localhost, nothing repeated.
        assert len(origins) == count + 4
        assert set(origins[count:]) == set(DEV_ORIGINS) | {"http://127.0.0.1:9001", "http://localhost:9001"}

    def test_vite_is_started_in_frontend_dir_and_cleaned_up(self, dev_project, vite):
        """Vite runs in <cwd>/frontend and is terminated when the backend exits.

        Mutations killed: wrong frontend dir, cleanup missing from ``finally``.
        """
        vite.wait.return_value = None
        with patch("sshler.cli.uvicorn.run", side_effect=KeyboardInterrupt()):
            serve_dev(open_browser=False)

        vite.starter.assert_called_once_with(dev_project / "frontend")
        vite.terminate.assert_called_once_with()

    def test_missing_frontend_dir_exits_1_without_starting_anything(self, tmp_path, monkeypatch):
        """No ``frontend/`` under the cwd: exit status 1, no Vite, no backend.

        Mutations killed: the check removed (Vite started in a bad dir), exit code changed.
        """
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        with patch("sshler.cli._start_vite_dev_server") as starter:
            with patch("sshler.cli.uvicorn.run") as run:
                with pytest.raises(SystemExit) as excinfo:
                    serve_dev(open_browser=False)

        assert excinfo.value.code == 1
        starter.assert_not_called()
        run.assert_not_called()

    def test_vite_start_failure_exits_1_before_backend(self, dev_project):
        """A Vite start error ends in exit status 1 and never starts the backend.

        Mutations killed: RuntimeError not caught, backend started anyway.
        """
        with patch("sshler.cli._start_vite_dev_server", side_effect=RuntimeError("no pnpm")), patch(
            "sshler.cli.uvicorn.run"
        ) as run:
            with pytest.raises(SystemExit) as excinfo:
                serve_dev(open_browser=False)

        assert excinfo.value.code == 1
        run.assert_not_called()

    @pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
    def test_signal_handler_stops_vite_and_exits_0(self, dev_project, vite, sig):
        """SIGINT and SIGTERM terminate Vite and exit with status 0.

        Mutations killed: handler not installed for the signal, Vite left running,
        non-zero exit.
        """
        vite.wait.return_value = None
        with patch("sshler.cli.uvicorn.run"):
            serve_dev(open_browser=False)
        vite.terminate.reset_mock()  # the normal ``finally`` cleanup already ran once

        handler = signal.getsignal(sig)
        with pytest.raises(SystemExit) as excinfo:
            handler(sig, None)

        assert excinfo.value.code == 0
        vite.terminate.assert_called_once_with()

class TestViteAndCleanup:
    def test_vite_dev_server_startup(self):
        """Test that Vite dev server can be started with proper configuration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend_dir = Path(temp_dir)

            # Create mock package.json
            package_json = frontend_dir / "package.json"
            package_json.write_text('{"name": "test", "scripts": {"dev": "vite"}}')

            # Mock subprocess to avoid actually starting Vite
            with patch('subprocess.Popen') as mock_popen:
                mock_process = Mock()
                mock_process.poll.return_value = None
                mock_process.stdout = None
                mock_popen.return_value = mock_process

                # pnpm is resolved to an absolute path via shutil.which (required on
                # Windows, where `pnpm` is a `.cmd` shim Popen can't launch by bare
                # name). Mock the resolution + the `--version` availability check.
                fake_pnpm = str(frontend_dir / "pnpm")
                with patch('shutil.which', return_value=fake_pnpm) as mock_which, \
                     patch('subprocess.run') as mock_run:
                    mock_run.return_value = Mock(returncode=0)  # pnpm --version succeeds

                    result = _start_vite_dev_server(frontend_dir)

                    # Verify Popen was called with the resolved pnpm path
                    mock_popen.assert_called_once()
                    call_args = mock_popen.call_args

                    mock_which.assert_called_once_with("pnpm")
                    assert call_args[0][0] == [fake_pnpm, "dev"]
                    assert call_args[1]['cwd'] == frontend_dir
                    assert result == mock_process

    def test_process_cleanup(self):
        """Test that processes are properly cleaned up."""
        # Create mock processes
        mock_process1 = Mock()
        mock_process1.poll.return_value = None  # Still running
        mock_process1.terminate.return_value = None
        mock_process1.wait.return_value = None

        mock_process2 = Mock()
        mock_process2.poll.return_value = 0  # Already terminated

        _cleanup_processes(mock_process1, mock_process2)

        # Verify terminate was called on running process
        mock_process1.terminate.assert_called_once()
        mock_process1.wait.assert_called_once()

        # Verify terminate was not called on already terminated process
        mock_process2.terminate.assert_not_called()



class TestDevWorkflowIntegration:
    def test_package_json_validation(self):
        """Test that missing package.json is handled properly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend_dir = Path(temp_dir)

            # Don't create package.json
            with pytest.raises(RuntimeError, match="package.json not found"):
                _start_vite_dev_server(frontend_dir)
