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
import re
import uuid

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

FRAME_TIMEOUT = 15.0


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

    await nav.get_by_role("link", name=re.compile(r"^Terminal \(")).click()
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
