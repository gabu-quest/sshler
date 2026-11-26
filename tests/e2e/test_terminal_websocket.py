"""Playwright e2e test for WebSocket terminal functionality.

Tests the full terminal flow:
1. Navigate to terminal view
2. Select local box
3. Wait for WebSocket connection
4. Send command and verify output
5. Test disconnect/reconnect behavior

xterm paints into a canvas, so the rendered text cannot be read from the DOM.
Tests that need terminal output read the frames the page receives on its
``/ws/term`` WebSocket instead (``WsOutput``) and wait on those frames, never on
a timer.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import socket
import uuid
from urllib.parse import urlencode, urlsplit

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

FRAME_TIMEOUT = 15.0
HOSTILE = "http://evil.example"
# Longer than any folder name, so the server refuses it as a session name.
TOO_LONG_SESSION = "x" * 300


def shell_probe(marker: str) -> tuple[str, str]:
    """Return ``(command, expected_output)`` for a shell echo that proves execution.

    The typed command line (which the tty echoes back) contains the marker and
    the number separated by a space; only an executed ``printf`` produces
    ``<marker>_42``. A terminal that swallows input, or one that merely echoes
    keystrokes, never yields the expected string.
    """
    return f"printf '%s_%s\\n' {marker} 42", f"{marker}_42"


class WsOutput:
    """Collects the frames a page receives on its ``/ws/term`` WebSocket."""

    def __init__(self, page) -> None:
        self.text = ""
        self.frames = 0
        self.urls: list[str] = []
        self._changed = asyncio.Event()
        page.on("websocket", self._on_websocket)

    def _on_websocket(self, ws) -> None:
        if "/ws/term" not in ws.url:
            return
        self.urls.append(ws.url)
        ws.on("framereceived", self._on_frame)

    def _on_frame(self, payload) -> None:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        self.text += payload
        self.frames += 1
        self._changed.set()

    async def wait_for(self, needle: str, timeout: float = FRAME_TIMEOUT) -> None:
        """Block until *needle* appears in the received output; fail with the output so far."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while needle not in self.text:
            self._changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(
                    f"{needle!r} never arrived on /ws/term; received so far: {self.text[-300:]!r}"
                )
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                continue

    async def wait_for_first_frame(self, timeout: float = FRAME_TIMEOUT) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.frames == 0:
            self._changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError("no frame arrived on /ws/term")
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                continue


@pytest.mark.asyncio
async def test_terminal_websocket_connection(open_page):
    """The terminal view opens a /ws/term socket for the local box and receives shell output.

    Mutation: the page never opens the socket, or the server sends no frame
    -> ``wait_for_first_frame`` raises; a socket that connects but never
    runs the shell -> the probe output never arrives and ``wait_for`` raises.
    """
    page = await open_page()
    ws = WsOutput(page)

    await page.goto("/app/terminal?box=local", wait_until="load")
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)
    await ws.wait_for_first_frame()

    assert "host=local" in ws.urls[0]

    marker = f"CONN_{uuid.uuid4().hex[:8]}"
    command, expected = shell_probe(marker)
    await page.locator(".xterm").click()
    await page.keyboard.type(command)
    await page.keyboard.press("Enter")
    await ws.wait_for(expected)


@pytest.mark.asyncio
async def test_terminal_send_command(open_page):
    """Typing a command in the xterm executes it in the shell and the result comes back.

    Mutation: type ``printf ... {marker} 43`` (or drop the Enter press) while
    still expecting ``{marker}_42`` -> ``wait_for`` raises.
    """
    marker = f"E2E_TEST_{uuid.uuid4().hex[:8]}"
    command, expected = shell_probe(marker)
    page = await open_page()
    ws = WsOutput(page)

    await page.goto("/app/terminal?box=local&dir=/tmp", wait_until="load")
    terminal = page.locator(".xterm")
    await expect(terminal).to_be_visible(timeout=15000)
    await ws.wait_for_first_frame()

    await terminal.click()
    await page.keyboard.type(command)
    await page.keyboard.press("Enter")

    await ws.wait_for(expected)
    assert expected in ws.text


@pytest.mark.asyncio
async def test_terminal_via_files_view(open_page):
    """Clicking the Terminal nav link on the files view lands on /terminal and opens a live socket.

    Mutation: nav link points elsewhere -> ``to_have_url`` fails; terminal
    never connects -> ``wait_for_first_frame`` raises.
    """
    page = await open_page()
    ws = WsOutput(page)

    await page.goto("/app/files", wait_until="load")
    nav = page.locator(".desktop-nav")
    await expect(nav).to_be_visible()

    # The collapsed rail shows only each link's icon; the rest of the link is clipped
    # under the page, so click the icon, as a person does.
    await nav.get_by_role("link", name=re.compile(r"^Terminal \(")).click(position={"x": 20, "y": 20})
    await expect(page).to_have_url(re.compile(r"/app/terminal(\?.*)?$"))

    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)
    await ws.wait_for_first_frame()


@pytest.mark.asyncio
async def test_terminal_websocket_api_direct(open_page, app_server):
    """Drive /ws/term directly: after the first frame, send a command and read its result back.

    Mutation: send a different string than the one expected (or never send)
    -> ``echoReceived`` stays false and the assertion fails.
    """
    _, token = app_server
    marker = f"WSTEST_{uuid.uuid4().hex[:8]}"
    command, expected = shell_probe(marker)
    page = await open_page()
    await page.goto("/app/", wait_until="load")

    result = await page.evaluate(
        """async ([token, command, expected]) => {
            const hsResponse = await fetch('/api/v1/terminal/handshake', {
                headers: { 'X-SSHLER-TOKEN': token }
            });
            if (!hsResponse.ok) {
                return { success: false, error: 'Handshake failed: ' + hsResponse.status };
            }
            const handshake = await hsResponse.json();

            const params = new URLSearchParams({
                host: 'local',
                dir: '/tmp',
                session: 'e2e-test',
                cols: '80',
                rows: '24',
                token: token
            });
            const wsUrl = handshake.ws_url + '?' + params.toString();

            return new Promise((resolve) => {
                const ws = new WebSocket(wsUrl);
                ws.binaryType = 'arraybuffer';

                let connected = false;
                let dataChunks = 0;
                let sent = false;
                let output = '';

                const finish = (extra) => {
                    clearTimeout(timeout);
                    ws.close();
                    resolve({
                        connected,
                        dataChunks,
                        echoReceived: output.includes(expected),
                        outputTail: output.slice(-300),
                        ...extra
                    });
                };
                const timeout = setTimeout(() => finish({ error: 'timeout' }), 15000);

                ws.onopen = () => { connected = true; };

                ws.onmessage = (event) => {
                    dataChunks++;
                    if (event.data instanceof ArrayBuffer) {
                        output += new TextDecoder().decode(event.data);
                    }
                    if (!sent) {
                        // First frame = the shell is attached; later chunks carry the reply.
                        sent = true;
                        ws.send(new TextEncoder().encode(command + '\\n'));
                    }
                    if (output.includes(expected)) {
                        finish({});
                    }
                };

                ws.onerror = () => finish({ error: 'WebSocket error' });
                ws.onclose = (event) => {
                    if (!connected) {
                        finish({ error: `Connection closed: ${event.code} ${event.reason}` });
                    }
                };
            });
        }""",
        [token, command, expected],
    )

    assert result["connected"], f"WebSocket did not connect: {result}"
    assert result["echoReceived"], f"{expected!r} never came back: {result}"


# --- a refused socket tells the page why ----------------------------------------------
#
# A close before the accept is answered by uvicorn with HTTP 403, which a browser reports
# as close 1006 with no reason. A page on an allowed origin must instead see the code.


def raw_upgrade(base_url: str, path: str, headers: dict[str, str]) -> tuple[str, int, str]:
    """Send one WebSocket upgrade by hand: the status line, and the close frame's code
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


def _term_path(token: str, **extra: str) -> str:
    query = {"host": "local", "dir": "/tmp", "session": "refused", "token": token, **extra}
    return "/ws/term?" + urlencode(query)


async def _browser_close(page, path: str) -> dict:
    """Open *path* as a WebSocket from the page; how it ended, as the browser saw it."""
    return await page.evaluate(
        """(path) => new Promise((resolve) => {
            const url = location.origin.replace(/^http/, 'ws') + path;
            const ws = new WebSocket(url);
            let opened = false;
            const timer = setTimeout(() => resolve({ error: 'timeout' }), 10000);
            ws.onopen = () => { opened = true; };
            ws.onclose = (event) => {
                clearTimeout(timer);
                resolve({ opened, code: event.code, reason: event.reason });
            };
        })""",
        path,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("socket_path", "closed"),
    [
        (lambda token: _term_path("wrong"), (4403, "Invalid token")),
        (lambda token: _term_path(token, session=TOO_LONG_SESSION), (4400, "Invalid session name")),
        (lambda token: _term_path(token, shell="bash"), (4400, "Invalid shell")),
        (lambda token: "/ws/progress?token=wrong", (4403, "Invalid token")),
        (lambda token: "/ws/ping?token=wrong", (4403, "Invalid token")),
    ],
    ids=["term-token", "term-session-name", "term-shell", "progress-token", "ping-token"],
)
async def test_a_refused_socket_reaches_the_browser_with_its_code(
    open_page, app_server, socket_path, closed
):
    """A page on the server's own origin is told the code and reason of a refusal.

    Mutation: closing before the accept -> the browser sees 1006 and an empty reason.
    """
    _, token = app_server
    page = await open_page()
    await page.goto("/app/", wait_until="load")
    result = await _browser_close(page, socket_path(token))
    assert result == {"opened": True, "code": closed[0], "reason": closed[1]}


@pytest.mark.parametrize(
    ("origin", "token"),
    [(HOSTILE, "right"), (HOSTILE, "wrong"), ("null", "right"), ("null", "wrong")],
)
@pytest.mark.parametrize("socket_name", ["term", "progress", "ping"])
def test_a_foreign_origin_is_answered_403_and_never_upgraded(
    app_server, origin: str, token: str, socket_name: str
):
    """A cross-site page gets HTTP 403, whatever else is right or wrong with its request.

    Mutation: accepting before the Origin is judged -> a 101 and a close frame.
    """
    base_url, server_token = app_server
    supplied = server_token if token == "right" else "wrong"
    path = _term_path(supplied) if socket_name == "term" else f"/ws/{socket_name}?token={supplied}"
    assert raw_upgrade(base_url, path, {"Origin": origin}) == ("HTTP/1.1 403 Forbidden", 0, "")


def test_an_allowed_origin_with_a_wrong_token_is_upgraded_and_told_4403(app_server):
    """The other side of the 403: the server's own origin is upgraded, then closed 4403.

    Mutation: refusing every wrong token before the accept -> a 403 here too.
    """
    base_url, _ = app_server
    assert raw_upgrade(base_url, "/ws/ping?token=wrong", {"Origin": base_url}) == (
        "HTTP/1.1 101 Switching Protocols",
        4403,
        "Invalid token",
    )


@pytest.mark.asyncio
async def test_a_refused_session_name_is_shown_and_not_retried(open_page):
    """The terminal page with a session name the server refuses shows the refusal and opens
    no second socket in the time its first retry would take (one second).

    Mutation: the refusal closed before the accept -> the page sees 1006, shows nothing
    and retries after a second (a second socket).
    """
    page = await open_page()
    ws = WsOutput(page)
    await page.goto(f"/app/terminal?box=local&session={TOO_LONG_SESSION}", wait_until="load")
    await expect(
        page.get_by_text(
            "The server refused this session name. Open the terminal with another name."
        )
    ).to_be_visible(timeout=15000)
    await page.wait_for_timeout(2500)
    assert len(ws.urls) == 1
