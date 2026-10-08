from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio
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


def _kill_tmux_servers(tmux_dir: Path) -> None:
    """Kill every tmux server whose socket lives under *tmux_dir*."""
    if shutil.which("tmux") is None:
        return
    for sock in tmux_dir.glob("tmux-*/*"):
        if sock.is_socket():
            subprocess.run(
                ["tmux", "-S", str(sock), "kill-server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )


def server_env(
    base: dict[str, str], config_dir: Path, tmux_dir: Path, home: Path, port: int
) -> dict[str, str]:
    """Environment for the E2E sshler server: no route to the user's tmux.

    ``TMUX``/``TMUX_PANE`` are dropped and ``TMUX_TMPDIR``/``HOME`` point at temp
    dirs, so any tmux the server spawns uses a private socket dir and no user
    tmux config. The pytest process keeps its real ``HOME`` (Playwright browsers).
    """
    env = dict(base)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["TMUX_TMPDIR"] = str(tmux_dir)
    env["HOME"] = str(home)
    env["SSHLER_CONFIG_DIR"] = str(config_dir)
    # Set PUBLIC_URL to match the test server URL for origin validation
    env["SSHLER_PUBLIC_URL"] = f"http://127.0.0.1:{port}"
    return env


def _assert_server_isolated(pid: int, tmux_dir: Path, home: Path) -> None:
    """Fail fast if the running server could reach the user's tmux (Linux /proc)."""
    environ = Path(f"/proc/{pid}/environ")
    if not environ.exists():
        return
    entries = dict(item.split("=", 1) for item in environ.read_text().split("\0") if "=" in item)
    assert entries.get("TMUX_TMPDIR") == str(tmux_dir)
    assert entries.get("HOME") == str(home)
    assert "TMUX" not in entries
    assert "TMUX_PANE" not in entries


@pytest.fixture(scope="session")
def e2e_tmux_dir():
    """Short private TMUX_TMPDIR (sun_path limit); its tmux servers die at teardown."""
    tmux_dir = Path(tempfile.mkdtemp(prefix="sshler-e2e-tmux-"))
    try:
        yield tmux_dir
    finally:
        _kill_tmux_servers(tmux_dir)
        shutil.rmtree(tmux_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def app_server(tmp_path_factory, e2e_tmux_dir):
    """Start a real sshler server for Playwright e2e tests."""

    port = _find_free_port()
    token = "e2e-token"
    config_dir: Path = tmp_path_factory.mktemp("sshler_config")

    # Minimal config file
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    home: Path = tmp_path_factory.mktemp("e2e_home")
    # An empty HOME makes a fresh zsh show its zsh-newuser-install menu, which
    # swallows the first keystrokes typed into the terminal under test.
    (home / ".zshrc").write_text("", encoding="utf-8")
    env = server_env(dict(os.environ), config_dir, e2e_tmux_dir, home, port)

    cmd = [
        sys.executable,
        "-c",
        (
            "from sshler.cli import serve; "
            f"serve(host='127.0.0.1', port={port}, reload=False, "
            "allow_origins=[], basic_auth=None, max_upload_mb=50, "
            f"allow_ssh_alias=True, log_level='warning', open_browser=False, token='{token}')"
        ),
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=env,
    )

    try:
        _wait_for_server(port, process)
        _assert_server_isolated(process.pid, e2e_tmux_dir, home)
        yield f"http://127.0.0.1:{port}", token
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest_asyncio.fixture
async def open_page(app_server):
    """Factory ``await open_page(viewport=None) -> Page``, authenticated with the E2E token.

    Relative URLs resolve against the server.

    Playwright and every browser it launched are closed at teardown, also when the
    test body raised, so a failed assertion cannot leak a Chromium process.
    """
    playwright_async = pytest.importorskip(
        "playwright.async_api",
        reason="Playwright is not installed; run `playwright install chromium`",
    )
    base_url, token = app_server
    playwright = await playwright_async.async_playwright().start()
    browsers = []

    async def _open(viewport: dict[str, int] | None = None):
        browser = await playwright.chromium.launch(headless=True)
        browsers.append(browser)
        options = {"viewport": viewport} if viewport else {}
        context = await browser.new_context(base_url=base_url, **options)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})
        return page

    try:
        yield _open
    finally:
        for browser in browsers:
            await browser.close()
        await playwright.stop()
