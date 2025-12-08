from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("sshler server exited before becoming ready")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError("Timed out waiting for sshler server to start")


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """Start a real sshler server for Playwright e2e tests."""

    port = _find_free_port()
    token = "e2e-token"
    config_dir: Path = tmp_path_factory.mktemp("sshler_config")

    # Minimal config file
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    env = os.environ.copy()
    env["SSHLER_CONFIG_DIR"] = str(config_dir)

    cmd = [
        sys.executable,
        "-c",
        (
            "from sshler.cli import serve; "
            "serve(host='127.0.0.1', port=%d, reload=False, "
            "allow_origins=[], basic_auth=None, max_upload_mb=50, "
            "allow_ssh_alias=True, log_level='warning', open_browser=False, token='%s')"
        )
        % (port, token)
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=env,
    )

    try:
        _wait_for_server(port, process)
        yield f"http://127.0.0.1:{port}", token
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
