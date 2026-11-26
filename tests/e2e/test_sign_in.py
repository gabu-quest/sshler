"""Playwright E2E test of signing in and out through the page, with a password set.

The server of this module is its own: the shared one runs with no password. It is
started as the shared one is, on a free port, in a private HOME, config folder and
TMUX_TMPDIR, with a made-up password from the environment.
"""

from __future__ import annotations

import base64
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


# --- a socket without a session -------------------------------------------------------


def _raw_upgrade(base_url: str, path: str, headers: dict[str, str]) -> tuple[str, int, str]:
    """One WebSocket upgrade sent by hand: the status line, and the close frame's code
    and reason when the server upgraded and closed at once (0 and "" when it did not)."""
    parts = urlsplit(base_url)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = [
        f"GET {path} HTTP/1.1",
        f"Host: {parts.netloc}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        *(f"{name}: {value}" for name, value in headers.items()),
        "",
        "",
    ]
    with socket.create_connection((parts.hostname, parts.port), timeout=10) as conn:
        conn.sendall("\r\n".join(request).encode("ascii"))
        reader = conn.makefile("rb")
        status = reader.readline().decode("ascii").strip()
        while reader.readline() not in (b"\r\n", b""):
            pass
        if not status.startswith("HTTP/1.1 101"):
            return status, 0, ""
        opcode, length = reader.read(2)
        assert (opcode, length < 126) == (0x88, True), (opcode, length)
        payload = reader.read(length)
    return status, int.from_bytes(payload[:2], "big"), payload[2:].decode("utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("socket_name", ["progress", "ping"])
async def test_a_socket_opened_signed_out_is_told_4401(password_server: str, socket_name: str):
    """A page with the token but no session opens the progress and ping sockets: the
    browser sees 4401, the code the page sends to the sign-in form on.

    Mutation: the refusal closed before the accept -> the browser sees 1006, and the
    page would retry for ever instead of signing in.
    """
    playwright = await playwright_async.async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        context = await browser.new_context(base_url=password_server, viewport=VIEWPORT)
        page = await context.new_page()
        await page.goto("/app/login", wait_until="load")
        result = await page.evaluate(
            """(path) => new Promise((resolve) => {
                const ws = new WebSocket(location.origin.replace(/^http/, 'ws') + path);
                let opened = false;
                const timer = setTimeout(() => resolve({ error: 'timeout' }), 10000);
                ws.onopen = () => { opened = true; };
                ws.onclose = (event) => {
                    clearTimeout(timer);
                    resolve({ opened, code: event.code, reason: event.reason });
                };
            })""",
            f"/ws/{socket_name}?token={TOKEN}",
        )
        assert result == {"opened": True, "code": 4401, "reason": "Unauthorized"}
    finally:
        await browser.close()
        await playwright.stop()


@pytest.mark.parametrize("origin", ["http://evil.example", "null"])
@pytest.mark.parametrize("socket_name", ["term", "progress", "ping"])
def test_a_foreign_origin_without_a_session_is_answered_403(
    password_server: str, origin: str, socket_name: str
):
    """No session and a foreign Origin: HTTP 403, never upgraded to be told 4401; the same
    request from the server's own origin is upgraded and told 4401.

    Mutation: the credential refusal accepted before the Origin is judged -> a 101 and
    a 4401 close frame for the foreign page.
    """
    query = f"token={TOKEN}"
    if socket_name == "term":
        query = f"host=local&dir=/tmp&session=refused&{query}"
    path = f"/ws/{socket_name}?{query}"
    assert _raw_upgrade(password_server, path, {"Origin": origin}) == (
        "HTTP/1.1 403 Forbidden",
        0,
        "",
    )
    assert _raw_upgrade(password_server, path, {"Origin": password_server}) == (
        "HTTP/1.1 101 Switching Protocols",
        4401,
        "Unauthorized",
    )
