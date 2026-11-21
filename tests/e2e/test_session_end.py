"""Playwright E2E test of a session that ends while a terminal is open.

A sign-out sent from a second page of the same browser ends the session; the first
page, on a terminal, must be on the sign-in form within three seconds, and after
signing in there the terminal is back on the same tmux session with what was typed.

The server of this module is its own, with a made-up password from the environment,
started as the shared one is: a free port, a private HOME, config folder and
TMUX_TMPDIR.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

USER = "e2e-user"
PASSWORD = "Made-Up-Pass-2026!"
TOKEN = "e2e-session-end-token"
VIEWPORT = {"width": 1280, "height": 800}
FRAME_TIMEOUT = 15.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_listening(port: int, process: subprocess.Popen, timeout: float = 10.0) -> None:
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


@pytest.fixture(scope="module")
def password_server(tmp_path_factory, e2e_tmux_dir: Path) -> Iterator[str]:
    port = _free_port()
    config_dir: Path = tmp_path_factory.mktemp("session_end_config")
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    home: Path = tmp_path_factory.mktemp("session_end_home")
    # An empty HOME makes a fresh zsh show its first-run menu, which swallows keys.
    (home / ".zshrc").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    for name in ("SSHLER_PASSWORD_HASH", "SSHLER_SESSION_TTL_SECONDS"):
        env.pop(name, None)
    env.update(
        {
            "TMUX_TMPDIR": str(e2e_tmux_dir),
            "HOME": str(home),
            "SSHLER_CONFIG_DIR": str(config_dir),
            "SSHLER_PUBLIC_URL": f"http://127.0.0.1:{port}",
            "SSHLER_USERNAME": USER,
            "SSHLER_PASSWORD": PASSWORD,
            # Plain http: a Secure cookie would never be sent back.
            "SSHLER_COOKIE_SECURE": "false",
        }
    )
    code = (
        "from sshler.cli.server import serve; "
        f"serve(host='127.0.0.1', port={port}, reload=False, "
        "allow_origins=[], basic_auth=None, max_upload_mb=50, "
        f"allow_ssh_alias=True, log_level='warning', open_browser=False, token='{TOKEN}')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env
    )
    try:
        _wait_until_listening(port, process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


class TermFrames:
    """The frames a page receives on its ``/ws/term`` sockets, and each socket's close."""

    def __init__(self, page) -> None:  # type: ignore[no-untyped-def]
        self.text = ""
        self.urls: list[str] = []
        self._changed = asyncio.Event()
        page.on("websocket", self._on_websocket)

    def _on_websocket(self, ws) -> None:  # type: ignore[no-untyped-def]
        if "/ws/term" not in ws.url:
            return
        self.urls.append(ws.url)
        ws.on("framereceived", self._on_frame)

    def _on_frame(self, payload) -> None:  # type: ignore[no-untyped-def]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        self.text += payload
        self._changed.set()

    async def wait_for(self, needle: str, since: int = 0) -> None:
        """Block until *needle* is in the output after offset *since*."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FRAME_TIMEOUT
        while needle not in self.text[since:]:
            self._changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(
                    f"{needle!r} never arrived on /ws/term; last output: {self.text[-300:]!r}"
                )
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                continue


async def _sign_in_on_the_form(page) -> None:  # type: ignore[no-untyped-def]
    await page.get_by_placeholder("Enter username").fill(USER)
    await page.get_by_placeholder("Enter password").fill(PASSWORD)
    await page.get_by_role("button", name="Sign In").click()


@pytest.mark.asyncio
async def test_a_sign_out_in_another_page_puts_the_terminal_on_the_form_and_back(
    password_server: str, tmp_path: Path
):
    """Mutation: a socket left open after its session ends (the first page stays on
    the terminal), a 4401 that only toasts, or a sign-in that does not reattach the
    same tmux session (the typed line never comes back)."""
    session = f"session-end-{uuid.uuid4().hex[:6]}"
    marker = f"END_{uuid.uuid4().hex[:8]}"
    terminal_path = f"/terminal?box=local&dir={tmp_path}&session={session}"
    playwright = await playwright_async.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        context = await browser.new_context(base_url=password_server, viewport=VIEWPORT)
        page = await context.new_page()
        frames = TermFrames(page)

        await page.goto(f"/app{terminal_path}", wait_until="load")
        await _sign_in_on_the_form(page)
        await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)
        await page.locator(".xterm").click()
        await page.keyboard.type(f"printf '%s_%s\\n' {marker} 42")
        await page.keyboard.press("Enter")
        await frames.wait_for(f"{marker}_42")
        assert len(frames.urls) == 1
        assert parse_qs(urlsplit(frames.urls[0]).query)["session"] == [session]

        other = await context.new_page()
        await other.goto("/app/settings", wait_until="load")
        signed_out = await other.evaluate(
            """async (token) => {
                const r = await fetch('/api/v1/auth/logout', {
                    method: 'POST', headers: { 'X-SSHLER-TOKEN': token },
                });
                return r.status;
            }""",
            TOKEN,
        )
        assert signed_out == 200

        await expect(page.get_by_placeholder("Enter username")).to_be_visible(timeout=3000)
        landed = urlsplit(page.url)
        assert landed.path == "/app/login"
        assert parse_qs(landed.query)["redirect"] == [terminal_path]

        since = len(frames.text)
        await _sign_in_on_the_form(page)
        await expect(page).to_have_url(f"{password_server}/app{terminal_path}")
        # tmux redraws the pane on attach: the line printed before the end is on it.
        await frames.wait_for(f"{marker}_42", since=since)
        assert len(frames.urls) == 2
        assert parse_qs(urlsplit(frames.urls[1]).query)["session"] == [session]
    finally:
        await browser.close()
        await playwright.stop()
