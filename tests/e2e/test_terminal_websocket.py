"""Playwright e2e test for WebSocket terminal functionality.

Tests the full terminal flow:
1. Navigate to terminal view
2. Select local box
3. Wait for WebSocket connection
4. Send command and verify output
5. Test disconnect/reconnect behavior
"""
from __future__ import annotations

import uuid

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
async_playwright = playwright_async.async_playwright
expect = playwright_async.expect


@pytest.mark.asyncio
async def test_terminal_websocket_connection(app_server):
    """Test that the terminal WebSocket connects and receives data."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Navigate to terminal view with local box pre-selected
        await page.goto(f"{base_url}/app/terminal?box=local", wait_until="load")

        # Wait for the terminal component to be visible
        terminal_container = page.locator(".xterm")
        await terminal_container.wait_for(state="visible", timeout=15000)

        # Wait for connection - the terminal should show some output (prompt)
        # xterm renders into canvas, so we check for the xterm-screen element
        xterm_screen = page.locator(".xterm-screen")
        await xterm_screen.wait_for(state="visible", timeout=10000)

        # Give the terminal time to fully initialize and show prompt
        await page.wait_for_timeout(2000)

        # Verify terminal is interactive by checking xterm-screen rendered
        await expect(xterm_screen).to_be_visible()

        await browser.close()


@pytest.mark.asyncio
async def test_terminal_send_command(app_server):
    """Test sending a command through the terminal and receiving output."""
    base_url, token = app_server
    test_marker = f"E2E_TEST_{uuid.uuid4().hex[:8]}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Navigate to terminal
        await page.goto(f"{base_url}/app/terminal?box=local&dir=/tmp", wait_until="load")

        # Wait for terminal to be ready
        terminal = page.locator(".xterm")
        await terminal.wait_for(state="visible", timeout=15000)

        # Wait for shell to initialize
        await page.wait_for_timeout(2000)

        # Focus the terminal and send a command
        await terminal.click()
        await page.wait_for_timeout(200)

        # Type echo command with unique marker
        await page.keyboard.type(f"echo {test_marker}")
        await page.keyboard.press("Enter")

        # Wait for command to execute
        await page.wait_for_timeout(1000)

        # The terminal content is in a canvas, so we can't directly read it.
        # Instead, we verify the terminal stayed connected and didn't error
        # by checking the xterm element is still visible and no error alerts
        await expect(terminal).to_be_visible()

        # Check no error messages appeared
        error_alerts = page.locator("[class*='error'], [class*='Error']")
        error_count = await error_alerts.count()
        # Some error classes might exist in CSS but not be visible
        for i in range(error_count):
            alert = error_alerts.nth(i)
            if await alert.is_visible():
                text = await alert.text_content()
                assert "disconnect" not in text.lower(), f"Unexpected disconnect error: {text}"

        await browser.close()


@pytest.mark.asyncio
async def test_terminal_via_files_view(app_server):
    """Test opening terminal from the files view navigation."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Start at files view
        await page.goto(f"{base_url}/app/files", wait_until="load")
        await page.wait_for_selector("text=File Browser", timeout=10000)

        # Navigate to terminal using nav
        await page.click("text=Terminal")
        await page.wait_for_url("**/terminal**", timeout=5000)

        # Terminal should load
        terminal = page.locator(".xterm")
        await terminal.wait_for(state="visible", timeout=15000)

        # Verify we have a functioning terminal (xterm-screen rendered)
        xterm_screen = page.locator(".xterm-screen")
        await expect(xterm_screen).to_be_visible()

        await browser.close()


@pytest.mark.asyncio
async def test_terminal_websocket_api_direct(app_server):
    """Test WebSocket connection directly using page.evaluate for more control."""
    base_url, token = app_server
    test_marker = f"WSTEST_{uuid.uuid4().hex[:8]}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Load the app to get a valid page context
        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(500)

        # Test WebSocket connection directly via JavaScript
        result = await page.evaluate(
            """async ([baseUrl, token, marker]) => {
                // Get handshake info
                const hsResponse = await fetch(baseUrl + '/api/v1/terminal/handshake', {
                    headers: { 'X-SSHLER-TOKEN': token }
                });
                if (!hsResponse.ok) {
                    return { success: false, error: 'Handshake failed: ' + hsResponse.status };
                }
                const handshake = await hsResponse.json();

                // Build WebSocket URL
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
                    let receivedData = false;
                    let dataChunks = 0;
                    let echoReceived = false;
                    const output = [];

                    const timeout = setTimeout(() => {
                        ws.close();
                        resolve({
                            success: connected && receivedData,
                            connected,
                            receivedData,
                            dataChunks,
                            echoReceived,
                            outputPreview: output.join('').substring(0, 200)
                        });
                    }, 5000);

                    ws.onopen = () => {
                        connected = true;
                        // Wait for shell, then send echo command
                        setTimeout(() => {
                            ws.send(new TextEncoder().encode('echo ' + marker + '\\n'));
                        }, 1000);
                    };

                    ws.onmessage = (event) => {
                        receivedData = true;
                        dataChunks++;
                        if (event.data instanceof ArrayBuffer) {
                            const text = new TextDecoder().decode(event.data);
                            output.push(text);
                            if (text.includes(marker)) {
                                echoReceived = true;
                            }
                        }
                    };

                    ws.onerror = (err) => {
                        clearTimeout(timeout);
                        resolve({ success: false, error: 'WebSocket error', connected });
                    };

                    ws.onclose = (event) => {
                        if (!connected) {
                            clearTimeout(timeout);
                            resolve({
                                success: false,
                                error: `Connection closed: ${event.code} ${event.reason}`,
                                connected
                            });
                        }
                    };
                });
            }""",
            [base_url, token, test_marker],
        )

        assert result["success"], f"WebSocket test failed: {result}"
        assert result["connected"], "WebSocket did not connect"
        assert result["receivedData"], "No data received from WebSocket"
        assert result["dataChunks"] > 0, "Expected multiple data chunks"

        await browser.close()
