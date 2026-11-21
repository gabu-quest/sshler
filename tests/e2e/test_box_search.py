"""Playwright tests of the search field on each box card of the front page.

A module server of its own has a home folder the test fills, so the folder search finds known
folders. A running session is made by opening a terminal in a folder, as a user does.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
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
ROWS = "[data-testid='box-search-list'] .search-result"


def run_search_server(
    tmp_path_factory, e2e_tmux_dir: Path, boxes: list[dict]
) -> Iterator[tuple[str, str, Path]]:
    """A server with its own home folder and the given extra boxes: ``(url, token, home)``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    token = "e2e-box-search-token"
    config_dir: Path = tmp_path_factory.mktemp("box_search_config")
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": boxes}, sort_keys=False), encoding="utf-8"
    )
    home: Path = tmp_path_factory.mktemp("box_search_home")
    (home / ".zshrc").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.update(
        {
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
                raise RuntimeError("the search server exited before it was ready")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.25).close()
                break
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError("the search server did not start") from None
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}", token, home
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def search_server(tmp_path_factory, e2e_tmux_dir: Path) -> Iterator[tuple[str, str, Path]]:
    yield from run_search_server(tmp_path_factory, e2e_tmux_dir, [])


@pytest.fixture(scope="module")
def three_box_server(tmp_path_factory, e2e_tmux_dir: Path) -> Iterator[tuple[str, str, Path]]:
    """The local box and two remote boxes nothing listens for: three cards."""
    far = [
        {"name": f"far-{n}", "host": "127.0.0.1", "user": "nobody", "port": 1, "pinned": True}
        for n in ("a", "b")
    ]
    yield from run_search_server(tmp_path_factory, e2e_tmux_dir, far)


@pytest_asyncio.fixture
async def open_search_page(search_server):
    """Factory ``await open(viewport, touch=False, server=None) -> Page``."""
    playwright = await playwright_async.async_playwright().start()
    browsers = []

    async def _open(viewport: dict[str, int], *, touch: bool = False, server=None):
        base_url, token, _home = server or search_server
        browser = await playwright.chromium.launch(headless=True)
        browsers.append(browser)
        context = await browser.new_context(
            base_url=base_url, viewport=viewport, has_touch=touch, is_mobile=touch
        )
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})
        return page

    try:
        yield _open
    finally:
        for browser in browsers:
            await browser.close()
        await playwright.stop()


def make_folders(home: Path, *relative: str) -> None:
    for name in relative:
        (home / name).mkdir(parents=True, exist_ok=True)


async def start_session(page, folder: Path) -> None:
    """Open a terminal in *folder* and wait for its shell: its tmux session now runs."""
    await page.goto(f"/app/terminal?box=local&dir={folder}", wait_until="load")
    await expect(page.locator(".terminal .xterm-screen")).to_be_visible(timeout=TIMEOUT_MS)
    await page.wait_for_function(
        """() => { const b = window.term && window.term.buffer.active;
          if (!b) return false;
          const line = b.getLine(b.baseY + b.cursorY);
          return !!line && /[$#%>] ?$/.test(line.translateToString(true)); }""",
        timeout=TIMEOUT_MS,
    )


async def type_in_card(page, text: str, *, sessions_loaded: bool = False) -> None:
    await page.goto("/app/", wait_until="load")
    if sessions_loaded:
        # The card holds its sessions once it says how many run.
        await expect(page.locator(".session-link").first).to_be_visible(timeout=TIMEOUT_MS)
    field = page.locator(".server-card input").first
    await expect(field).to_be_visible(timeout=TIMEOUT_MS)
    await field.fill(text)


async def row_texts(page) -> list[str]:
    """Every row of the open list as ``kind: name path``, top to bottom."""
    texts = []
    for row in await page.locator(ROWS).all():
        parts = [
            await part.inner_text()
            for part in await row.locator(".result-name, .result-path").all()
        ]
        texts.append(f"{await row.get_attribute('data-kind')}: {' '.join(parts)}")
    return texts


async def tmux_lines(e2e_tmux_dir: Path, name: str, *args: str) -> list[str] | None:
    """What ``tmux -L ts-<name> ARGS`` prints in the fixture's socket folder, by word;
    None when tmux answers with an error. Run without blocking the event loop."""
    env = {**os.environ, "TMUX_TMPDIR": str(e2e_tmux_dir)}
    env.pop("TMUX", None)
    tmux = shutil.which("tmux")
    assert tmux
    process = await asyncio.create_subprocess_exec(
        tmux,
        "-L",
        f"ts-{name}",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return out.decode().split() if process.returncode == 0 else None


async def tmux_until(e2e_tmux_dir: Path, name: str, args: list[str], done, what: str):
    """``tmux_lines`` until ``done`` holds for its answer, at most ``TIMEOUT_MS``; the
    last answer."""
    deadline = time.monotonic() + TIMEOUT_MS / 1000
    while True:
        lines = await tmux_lines(e2e_tmux_dir, name, *args)
        if done(lines):
            return lines
        assert time.monotonic() < deadline, f"waited for {what} on ts-{name}: {lines!r}"
        await asyncio.sleep(0.1)


async def wait_for_session(e2e_tmux_dir: Path, name: str) -> list[str]:
    """The sessions of the ``ts-<name>`` server of the fixture, read through tmux itself."""
    return await tmux_until(
        e2e_tmux_dir,
        name,
        ["list-sessions", "-F", "#{session_name}"],
        bool,
        "a tmux session",
    )


async def test_a_running_session_lists_above_folders_and_a_folder_opens_its_ts_session(
    open_search_page, search_server, e2e_tmux_dir
):
    _url, _token, home = search_server
    make_folders(home, "alpha-proj", "work/alpha-lib")
    # The terminal stays open in a tab of its own: a session is running while a tab holds it.
    terminal_tab = await open_search_page(DESKTOP)
    await start_session(terminal_tab, home / "alpha-proj")
    page = await open_search_page(DESKTOP)

    await type_in_card(page, "alpha", sessions_loaded=True)
    await expect(page.locator(ROWS)).to_have_count(2, timeout=TIMEOUT_MS)
    assert await row_texts(page) == [
        f"session: alpha-proj {home}/alpha-proj",
        f"folder: {home}/work/alpha-lib",
    ]

    await page.locator(ROWS).nth(1).click()
    await page.wait_for_url("**/app/terminal?**", timeout=TIMEOUT_MS)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
    assert query["box"] == ["local"]
    assert query["dir"] == [f"{home}/work/alpha-lib"]
    assert await wait_for_session(e2e_tmux_dir, "alpha-lib") == ["alpha-lib"]


async def test_a_session_row_opens_the_terminal_of_that_session(open_search_page, search_server):
    _url, _token, home = search_server
    make_folders(home, "beta-proj")
    terminal_tab = await open_search_page(DESKTOP)
    await start_session(terminal_tab, home / "beta-proj")
    page = await open_search_page(DESKTOP)

    await type_in_card(page, "beta", sessions_loaded=True)
    await expect(page.locator(ROWS)).to_have_count(1, timeout=TIMEOUT_MS)
    assert await row_texts(page) == [f"session: beta-proj {home}/beta-proj"]
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    await page.wait_for_url("**/app/terminal?**", timeout=TIMEOUT_MS)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
    assert query["session"] == ["beta-proj"]


async def test_a_session_whose_tab_closed_is_still_listed_as_running(
    open_search_page, search_server, e2e_tmux_dir
):
    _url, _token, home = search_server
    make_folders(home, "epsilon-proj")
    terminal_tab = await open_search_page(DESKTOP)
    await start_session(terminal_tab, home / "epsilon-proj")
    assert await wait_for_session(e2e_tmux_dir, "epsilon-proj") == ["epsilon-proj"]
    await terminal_tab.close()
    # The server has ended the socket once tmux has no client on the session left.
    await tmux_until(
        e2e_tmux_dir,
        "epsilon-proj",
        ["list-clients", "-F", "#{client_tty}"],
        lambda clients: not clients,
        "the closed tab's tmux client to leave",
    )
    assert await tmux_lines(
        e2e_tmux_dir, "epsilon-proj", "list-sessions", "-F", "#{session_name}"
    ) == ["epsilon-proj"]
    page = await open_search_page(DESKTOP)

    await type_in_card(page, "epsilon", sessions_loaded=True)
    await expect(page.locator(ROWS)).to_have_count(1, timeout=TIMEOUT_MS)
    assert await row_texts(page) == [f"session: epsilon-proj {home}/epsilon-proj"]


async def test_nothing_matching_says_so_in_one_line(open_search_page):
    page = await open_search_page(DESKTOP)
    await type_in_card(page, "zzqqxx")
    await expect(page.locator("[data-testid='box-search-list']")).to_have_text(
        "No matches found", timeout=TIMEOUT_MS
    )
    await expect(page.locator(ROWS)).to_have_count(0)


async def test_on_a_phone_the_last_row_of_a_long_list_is_reachable_and_picks(
    open_search_page, search_server
):
    _url, _token, home = search_server
    names = [f"gamma-{n:02d}" for n in range(12)]
    make_folders(home, *(f"many/{name}" for name in names))
    page = await open_search_page(MOBILE, touch=True)
    await type_in_card(page, "gamma-")
    await expect(page.locator(ROWS)).to_have_count(12, timeout=TIMEOUT_MS)

    last = page.locator(ROWS).last
    await last.scroll_into_view_if_needed()
    box = await last.bounding_box()
    assert box is not None
    assert 0 <= box["y"] and box["y"] + box["height"] <= MOBILE["height"]
    assert 0 <= box["x"] and box["x"] + box["width"] <= MOBILE["width"]
    picked = (await last.locator(".result-path").inner_text()).rsplit("/", 1)[-1]
    await last.tap()
    await page.wait_for_url("**/app/terminal?**", timeout=TIMEOUT_MS)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
    assert query["dir"][0].rsplit("/", 1)[-1] == picked


async def test_the_highlighted_row_stays_in_view_and_is_the_only_one_painted(
    open_search_page, search_server
):
    _url, _token, home = search_server
    make_folders(home, *(f"long/delta-{n:02d}" for n in range(15)))
    page = await open_search_page(DESKTOP)
    await type_in_card(page, "delta-")
    await expect(page.locator(ROWS)).to_have_count(15, timeout=TIMEOUT_MS)
    list_box = await page.locator("[data-testid='box-search-list']").bounding_box()
    assert list_box is not None
    last_row = await page.locator(ROWS).last.bounding_box()
    assert (
        last_row is not None
        and last_row["y"] + last_row["height"] > list_box["y"] + list_box["height"]
    )

    for _ in range(13):
        await page.keyboard.press("ArrowDown")
    row = await page.locator(".search-result.highlighted").bounding_box()
    list_box = await page.locator("[data-testid='box-search-list']").bounding_box()
    assert row is not None and list_box is not None
    assert row["y"] >= list_box["y"]
    assert row["y"] + row["height"] <= list_box["y"] + list_box["height"]

    # The mouse rests on one row while an arrow key moves the highlight to the next.
    await page.locator(ROWS).nth(11).hover()
    await page.keyboard.press("ArrowDown")
    await page.wait_for_function(
        """selector => [...document.querySelectorAll(selector)].filter(
            el => getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)').length === 1""",
        arg=ROWS,
        timeout=TIMEOUT_MS,
    )


async def test_with_three_boxes_a_long_path_stays_inside_the_window_and_over_the_next_card(
    open_search_page, three_box_server
):
    _url, _token, home = three_box_server
    deep = "/".join(["segment-of-a-long-path"] * 4)
    make_folders(home, f"{deep}/tail-folder")
    page = await open_search_page(DESKTOP, server=three_box_server)
    await page.goto("/app/", wait_until="load")
    cards = page.locator(".server-card")
    await expect(cards).to_have_count(3, timeout=TIMEOUT_MS)
    names = [await card.locator(".server-name").inner_text() for card in await cards.all()]
    local = next(index for index, name in enumerate(names) if name.endswith("local"))
    # The local box is the first card; the grid puts it in the last column when it comes last.
    await page.add_style_tag(content=".n-grid > :first-child { order: 3; }")
    card = cards.nth(local)
    card_box = await card.bounding_box()
    assert card_box is not None
    assert card_box["x"] > DESKTOP["width"] / 2
    await card.locator("input").fill("tail-folder")
    await expect(page.locator(ROWS)).to_have_count(1, timeout=TIMEOUT_MS)
    path = await page.locator(ROWS).first.locator(".result-path").get_attribute("title")
    assert path is not None and len(path) >= 70
    right = await page.locator("[data-testid='box-search-list']").evaluate(
        "el => el.getBoundingClientRect().right"
    )
    assert right <= DESKTOP["width"]
    # A path too long for its row loses its start, never its end: the folder's own name,
    # the part the user typed, is painted inside the row.
    tail, shown = (
        await page.locator(ROWS)
        .first.locator(".result-path")
        .evaluate(
            """el => {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            const node = walker.nextNode();
            const text = node.textContent;
            const range = document.createRange();
            range.setStart(node, text.length - 'tail-folder'.length);
            range.setEnd(node, text.length);
            const name = range.getBoundingClientRect();
            const box = el.getBoundingClientRect();
            return [range.toString(), name.left >= box.left - 1 && name.right <= box.right + 1];
        }"""
        )
    )
    assert tail == "tail-folder"
    assert shown is True
    # A press on the row lands on the row, not on another card.
    box = await page.locator(ROWS).first.bounding_box()
    assert box is not None
    hit = await page.evaluate(
        "([x, y]) => !!document.elementFromPoint(x, y).closest('.search-result')",
        [box["x"] + 20, box["y"] + box["height"] / 2],
    )
    assert hit is True
