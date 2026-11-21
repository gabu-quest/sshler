"""Playwright E2E test of signing in and out through the page, with a password set.

The server of this module is its own: the shared one runs with no password. It is
started as the shared one is, on a free port, in a private HOME, config folder and
TMUX_TMPDIR, with a made-up password from the environment.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

USER = "e2e-user"
PASSWORD = "Made-Up-Pass-2026!"
TOKEN = "e2e-sign-in-token"
VIEWPORT = {"width": 1280, "height": 800}
REFUSED = {"detail": "Authentication required", "reason": "auth"}


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
    config_dir: Path = tmp_path_factory.mktemp("sign_in_config")
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    home: Path = tmp_path_factory.mktemp("sign_in_home")
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


async def _api_call(page, token: str) -> list:
    """The page's own GET of the boxes, with its token: [status, body]."""
    return await page.evaluate(
        """async (token) => {
            const r = await fetch('/api/v1/boxes', { headers: { 'X-SSHLER-TOKEN': token } });
            return [r.status, await r.json()];
        }""",
        token,
    )


@pytest.mark.asyncio
async def test_sign_in_reload_and_sign_out_through_the_page(password_server: str):
    """The page loads without credentials and shows its form; a wrong password shows its
    line; a right one lands on `redirect`; a reload stays signed in; sign-out returns to
    the form and the old page's session no longer works.

    Mutation: the page's shell behind the door (the document answers 401), the browser's
    prompt (a WWW-Authenticate header), the guard deciding by memory (a reload lands on
    the form), or a sign-out that leaves the session alive (the old cookie still works).
    """
    playwright = await playwright_async.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        context = await browser.new_context(base_url=password_server, viewport=VIEWPORT)
        page = await context.new_page()

        document = await page.goto("/app/settings", wait_until="load")
        assert document is not None
        assert document.status == 200
        assert "www-authenticate" not in document.headers
        await expect(page).to_have_url(f"{password_server}/app/login?redirect=/settings")
        await expect(page.get_by_placeholder("Enter username")).to_be_visible()

        await page.get_by_placeholder("Enter username").fill(USER)
        await page.get_by_placeholder("Enter password").fill("Not-The-Password-1!")
        await page.get_by_role("button", name="Sign In").click()
        await expect(page.locator(".n-alert")).to_have_text("Invalid username or password")
        await expect(page).to_have_url(f"{password_server}/app/login?redirect=/settings")

        await page.get_by_placeholder("Enter password").fill(PASSWORD)
        await page.get_by_role("button", name="Sign In").click()
        await expect(page).to_have_url(f"{password_server}/app/settings")
        row = page.get_by_test_id("sign-out-row")
        await expect(row.get_by_text(f"Signed in as {USER}", exact=True)).to_be_visible()

        await page.reload(wait_until="load")
        await expect(page).to_have_url(f"{password_server}/app/settings")
        await expect(row.get_by_text(f"Signed in as {USER}", exact=True)).to_be_visible()

        token = await page.evaluate("() => localStorage.getItem('sshler:token')")
        assert token == TOKEN
        status, boxes = await _api_call(page, token)
        assert (status, [box["name"] for box in boxes]) == (200, ["local"])
        [old_cookie] = await context.cookies()
        assert old_cookie["name"] == "sshler_session"

        await row.get_by_role("button", name="Sign out").click()
        await expect(page).to_have_url(f"{password_server}/app/login")
        await expect(page.get_by_placeholder("Enter username")).to_be_visible()
        assert await page.evaluate("() => localStorage.getItem('sshler:token')") is None
        assert await context.cookies() == []

        # The old page's next call, with its token, is refused; so is the old session
        # cookie put back, because the server ended the session.
        assert await _api_call(page, token) == [401, REFUSED]
        await context.add_cookies([old_cookie])
        assert await _api_call(page, token) == [401, REFUSED]
    finally:
        await browser.close()
        await playwright.stop()


@pytest.mark.asyncio
async def test_deep_link_signed_out_at_phone_width_keeps_its_address(password_server: str):
    """A deep link opened signed out lands on the form with its own address as `redirect`,
    no request of the page is refused on that load, and a good sign-in returns to it.

    Mutation: the header's polls asked before the page is signed in (a 401 on the stats
    or snapshot status, and the redirect replaced by the start location's `/`).
    """
    playwright = await playwright_async.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        context = await browser.new_context(
            base_url=password_server, viewport={"width": 390, "height": 844}
        )
        page = await context.new_page()
        answers: list[tuple[int, str]] = []
        page.on(
            "response",
            lambda response: answers.append((response.status, urlsplit(response.url).path)),
        )

        await page.goto("/app/settings", wait_until="load")
        await expect(page.get_by_placeholder("Enter username")).to_be_visible()
        await page.wait_for_timeout(1000)
        assert page.url == f"{password_server}/app/login?redirect=/settings"
        assert [path for status, path in answers if status == 401] == []

        await page.get_by_placeholder("Enter username").fill(USER)
        await page.get_by_placeholder("Enter password").fill(PASSWORD)
        await page.get_by_role("button", name="Sign In").click()
        await expect(page).to_have_url(f"{password_server}/app/settings")
    finally:
        await browser.close()
        await playwright.stop()
