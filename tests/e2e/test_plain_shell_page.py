"""Playwright tests of the Shell choice in the page: "tmux" or "Plain shell" on every box.

The shared server of the suite has tmux, so it proves the dropdown, the plain shell itself, the
tmux toolbar going and coming back, the requests a plain terminal does not make, and the multi
view's panes. A second server of this module starts with a ``PATH`` that holds no tmux, so a
terminal opened there falls back to a plain shell and the page prints its one line first.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import pytest_asyncio
import yaml

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

pytestmark = pytest.mark.asyncio

TIMEOUT_MS = 15000
DESKTOP = {"width": 1366, "height": 768}
MOBILE = {"width": 390, "height": 844}
NOTICE = (
    "tmux is not installed on this box: this is a plain shell, "
    "it ends when the tab closes or the connection drops"
)

# Everything xterm holds, one string per logical line (wrapped rows joined).
BUFFER_LINES = """() => {
  const b = window.term.buffer.active, lines = [];
  for (let y = 0; y < b.length; y++) {
    const l = b.getLine(y);
    const s = l.translateToString(true);
    if (l.isWrapped && lines.length) lines[lines.length - 1] += s; else lines.push(s);
  }
  return lines;
}"""

SHELL_SELECT = ".header-controls .shell-select"


def fresh_dir(tmp_path: Path) -> Path:
    directory = tmp_path / f"p{uuid.uuid4().int % 10**6:06d}"
    directory.mkdir()
    return directory


async def buffer_lines(page) -> list[str]:
    return list(await page.evaluate(BUFFER_LINES))


async def wait_for_prompt(page) -> None:
    await expect(page.locator(".terminal .xterm-screen")).to_be_visible(timeout=TIMEOUT_MS)
    await page.wait_for_function(
        """() => { const b = window.term && window.term.buffer.active;
          if (!b) return false;
          const line = b.getLine(b.baseY + b.cursorY);
          return !!line && /[$#%>] ?$/.test(line.translateToString(true)); }""",
        timeout=TIMEOUT_MS,
    )


async def wait_for_line(page, pattern: str) -> re.Match[str]:
    """Wait until a whole line of the terminal matches *pattern*; return the match."""
    await page.wait_for_function(
        f"(p) => ({BUFFER_LINES})().some((l) => new RegExp(p).test(l))",
        arg=pattern,
        timeout=TIMEOUT_MS,
    )
    for line in await buffer_lines(page):
        match = re.search(pattern, line)
        if match:
            return match
    raise AssertionError(f"{pattern!r} vanished from the terminal")


async def run_in_shell(page, command: str) -> None:
    await page.locator(".terminal .xterm").click()
    await page.keyboard.type(command)
    await page.keyboard.press("Enter")


async def tmux_variable(page) -> str:
    """What ``$TMUX`` holds in the shell of the terminal: a path inside tmux, ``none`` outside."""
    tag = f"V{uuid.uuid4().hex[:6]}"
    await run_in_shell(page, f"echo {tag}=${{TMUX:-none}}")
    return (await wait_for_line(page, rf"^{tag}=(\S+)$")).group(1)


async def pick_shell(page, label: str) -> None:
    await page.locator(SHELL_SELECT).first.click()
    await page.locator(".n-base-select-option", has_text=label).click()


class Traffic:
    """The ``/ws/term`` sockets a page opens."""

    def __init__(self, page) -> None:  # type: ignore[no-untyped-def]
        self.sockets: list = []
        page.on("websocket", lambda ws: self.sockets.append(ws) if "/ws/term" in ws.url else None)

    @property
    def open_sockets(self) -> list:
        return [ws for ws in self.sockets if not ws.is_closed()]


async def wait_for_count(page, read, expected: int, what: str) -> None:
    """Wait, with a limit, until ``read()`` is *expected*; never read a count once."""
    deadline = time.monotonic() + TIMEOUT_MS / 1000
    while read() != expected:
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: wanted {expected}, still {read()} after {TIMEOUT_MS} ms")
        await page.wait_for_timeout(25)


async def open_sockets(page, traffic: Traffic, expected: int) -> list:
    await wait_for_count(page, lambda: len(traffic.open_sockets), expected, "open sockets")
    return traffic.open_sockets


@pytest.fixture(scope="module")
def no_tmux_server(tmp_path_factory, e2e_tmux_dir: Path) -> Iterator[tuple[str, str]]:
    """A server whose ``PATH`` holds no tmux, so its terminals fall back to a plain shell."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    token = "e2e-plain-shell-token"
    config_dir: Path = tmp_path_factory.mktemp("plain_shell_config")
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    home: Path = tmp_path_factory.mktemp("plain_shell_home")
    (home / ".zshrc").write_text("", encoding="utf-8")
    empty_path: Path = tmp_path_factory.mktemp("plain_shell_path")
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.update(
        {
            "PATH": str(empty_path),
            "TMUX_TMPDIR": str(e2e_tmux_dir),
            "HOME": str(home),
            "SSHLER_CONFIG_DIR": str(config_dir),
            "SSHLER_PUBLIC_URL": f"http://127.0.0.1:{port}",
        }
    )
    code = (
        "from sshler.cli.server import serve; "
        f"serve(host='127.0.0.1', port={port}, reload=False, "
        "allow_origins=[], basic_auth=None, max_upload_mb=50, "
        f"allow_ssh_alias=True, log_level='warning', open_browser=False, token='{token}')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env
    )
    try:
        deadline = time.time() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError("the server without tmux exited before it was ready")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.25).close()
                break
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError("the server without tmux did not start") from None
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}", token
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest_asyncio.fixture
async def open_no_tmux_page(no_tmux_server):
    """Factory ``await open(viewport) -> Page`` on the server without tmux."""
    base_url, token = no_tmux_server
    playwright = await playwright_async.async_playwright().start()
    browsers = []

    async def _open(viewport: dict[str, int]):
        browser = await playwright.chromium.launch(headless=True)
        browsers.append(browser)
        context = await browser.new_context(base_url=base_url, viewport=viewport)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})
        return page

    try:
        yield _open
    finally:
        for browser in browsers:
            await browser.close()
        await playwright.stop()


async def test_pick_plain_runs_a_shell_without_tmux_and_back(open_page, tmp_path):
    page = await open_page(DESKTOP)
    traffic = Traffic(page)
    await page.goto(f"/app/terminal?box=local&dir={fresh_dir(tmp_path)}", wait_until="load")
    await wait_for_prompt(page)

    # Opens as tmux: the dropdown says so, the toolbar is there, and the shell is inside tmux.
    await expect(page.locator(SHELL_SELECT).first).to_have_text("tmux")
    await expect(page.locator(".toolbar-group")).to_have_count(3)
    assert await tmux_variable(page) != "none"

    await pick_shell(page, "Plain shell")
    await expect(page.locator(SHELL_SELECT).first).to_have_text("Plain shell")
    await wait_for_prompt(page)
    assert await tmux_variable(page) == "none"
    await expect(page.locator(".toolbar-group")).to_have_count(0)
    (plain_socket,) = await open_sockets(page, traffic, 1)
    assert "shell=plain" in plain_socket.url

    await pick_shell(page, "tmux")
    await expect(page.locator(".toolbar-group")).to_have_count(3)
    await wait_for_prompt(page)
    assert await tmux_variable(page) != "none"


async def test_a_reload_opens_tmux_after_a_plain_pick(open_page, tmp_path):
    page = await open_page(DESKTOP)
    traffic = Traffic(page)
    directory = fresh_dir(tmp_path)
    await page.goto(f"/app/terminal?box=local&dir={directory}", wait_until="load")
    await wait_for_prompt(page)
    await pick_shell(page, "Plain shell")
    await expect(page.locator(".toolbar-group")).to_have_count(0)

    await page.reload(wait_until="load")
    await wait_for_prompt(page)
    await expect(page.locator(SHELL_SELECT).first).to_have_text("tmux")
    await expect(page.locator(".toolbar-group")).to_have_count(3)
    (socket_after,) = await open_sockets(page, traffic, 1)
    assert "shell=" not in socket_after.url
    assert page.url.endswith(f"dir={directory}")


async def test_picking_plain_twice_leaves_one_socket(open_page, tmp_path):
    page = await open_page(DESKTOP)
    traffic = Traffic(page)
    await page.goto(f"/app/terminal?box=local&dir={fresh_dir(tmp_path)}", wait_until="load")
    await wait_for_prompt(page)
    await pick_shell(page, "Plain shell")
    await pick_shell(page, "Plain shell")
    await wait_for_prompt(page)
    await expect(page.locator(".toolbar-group")).to_have_count(0)

    await open_sockets(page, traffic, 1)
    await wait_for_count(
        page, lambda: len(traffic.sockets), 2, "sockets in all"
    )  # tmux, then plain


async def test_a_box_without_tmux_opens_a_plain_shell_with_the_line_first(
    open_no_tmux_page, tmp_path
):
    page = await open_no_tmux_page(DESKTOP)
    traffic = Traffic(page)
    await page.goto(f"/app/terminal?box=local&dir={fresh_dir(tmp_path)}", wait_until="load")
    await wait_for_prompt(page)

    lines = await buffer_lines(page)
    assert lines[0] == NOTICE
    assert sum(line == NOTICE for line in lines) == 1
    await expect(page.locator(SHELL_SELECT).first).to_have_text("Plain shell")
    await expect(page.locator(".toolbar-group")).to_have_count(0)
    assert await tmux_variable(page) == "none"
    await open_sockets(page, traffic, 1)
    await wait_for_count(page, lambda: len(traffic.sockets), 1, "sockets in all")


async def test_the_line_wraps_on_a_narrow_screen_and_the_shell_starts_its_own_row(
    open_no_tmux_page, tmp_path
):
    page = await open_no_tmux_page(MOBILE)
    await page.goto(f"/app/terminal?box=local&dir={fresh_dir(tmp_path)}", wait_until="load")
    await wait_for_prompt(page)

    rows = await page.evaluate(
        """() => { const b = window.term.buffer.active, rows = [];
          for (let y = 0; y < b.length; y++) {
            const l = b.getLine(y);
            rows.push([l.translateToString(true), l.isWrapped]);
          }
          return rows; }"""
    )
    assert rows[0][1] is False
    notice_rows = 1
    while rows[notice_rows][1]:
        notice_rows += 1
    assert notice_rows > 1, "at 390 px the notice takes more than one row"
    assert "".join(text for text, _ in rows[:notice_rows]) == NOTICE
    first_shell_row = rows[notice_rows]
    assert first_shell_row[0] != "" and first_shell_row[1] is False
    assert first_shell_row[0] not in NOTICE


async def run_on_phone(page, command: str) -> None:
    """Type into the input bar, as a user of a phone does."""
    box = page.locator(".mobile-input-bar textarea")
    await box.fill(command)
    await box.press("Enter")


async def open_controls(page) -> None:
    """The panel closes when the terminal takes focus, so a user opens it again when needed."""
    if not await page.locator(".mobile-controls-dropdown").is_visible():
        await page.locator(".mobile-title-btn").click()
    await expect(page.locator(".mobile-controls-dropdown")).to_be_visible()


async def pick_shell_on_phone(page, label: str) -> None:
    """Open the controls panel under the title, then pick in its dropdown."""
    await open_controls(page)
    await page.locator(".mobile-controls-dropdown .shell-select").click()
    await page.locator(".n-base-select-option", has_text=label).click()


async def test_on_a_phone_the_dropdown_picks_plain_and_the_bar_drops_tmux_keys(open_page, tmp_path):
    page = await open_page(MOBILE)
    await page.goto(f"/app/terminal?box=local&dir={fresh_dir(tmp_path)}", wait_until="load")
    await wait_for_prompt(page)
    tmux_keys = page.locator(
        ".mobile-input-bar button.quick-key:is([title='Scroll mode'], [title='Prev window'], "
        "[title='New window'], [title='Next window'], [title='Split vertical'], "
        "[title='Split horizontal'])"
    )
    await expect(tmux_keys).to_have_count(6)

    await pick_shell_on_phone(page, "Plain shell")
    await wait_for_prompt(page)
    await open_controls(page)
    await expect(page.locator(".mobile-controls-dropdown .shell-select")).to_have_text(
        "Plain shell"
    )
    tag = f"V{uuid.uuid4().hex[:6]}"
    await run_on_phone(page, f"echo {tag}=${{TMUX:-none}}")
    assert (await wait_for_line(page, rf"^{tag}=(\S+)$")).group(1) == "none"
    await expect(tmux_keys).to_have_count(0)
    await expect(page.locator(".mobile-input-bar .quick-key[title='Enter']")).to_have_count(1)

    await pick_shell_on_phone(page, "tmux")
    await expect(tmux_keys).to_have_count(6)
    await wait_for_prompt(page)
    tag = f"V{uuid.uuid4().hex[:6]}"
    await run_on_phone(page, f"echo {tag}=${{TMUX:-none}}")
    assert (await wait_for_line(page, rf"^{tag}=(\S+)$")).group(1) != "none"


async def test_a_plain_pane_of_the_multi_view_comes_back_plain_after_a_reload(open_page, tmp_path):
    page = await open_page(DESKTOP)
    traffic = Traffic(page)
    await page.goto("/app/multi-terminal?box=local", wait_until="load")
    await page.get_by_role("button", name="Add Terminal").first.click()
    shell_select = page.locator(".n-card .shell-select")
    await expect(shell_select).to_have_text("tmux")
    await shell_select.click()
    await page.locator(".n-base-select-option", has_text="Plain shell").click()
    await page.locator(".n-card").get_by_role("button", name="Add Terminal").click()
    await wait_for_prompt(page)
    assert await tmux_variable(page) == "none"
    assert "shell=plain" in (await open_sockets(page, traffic, 1))[-1].url

    before = len(traffic.sockets)
    await page.reload(wait_until="load")
    await wait_for_prompt(page)
    assert await tmux_variable(page) == "none"
    await wait_for_count(page, lambda: len(traffic.sockets) - before, 1, "sockets after the reload")
    assert "shell=plain" in traffic.sockets[before].url
