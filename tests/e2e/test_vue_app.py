"""Playwright E2E tests for Vue SPA navigation.

Run with:
    uv run pytest tests/e2e/test_vue_app.py -v
"""

from __future__ import annotations

import re

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
# The collapsed rail shows only its first 52 px (the panel clips the rest), so a click on a
# rail item goes to its icon, as a user's does.
ICON = {"x": 20, "y": 20}


def nav_link(page, label: str):
    """The single desktop-nav link for *label* (accessible name is ``"<label> (<shortcut>)"``)."""
    return page.locator(".desktop-nav").get_by_role("link", name=re.compile(rf"^{label} \("))


async def tool_link(page, label: str):
    """The link for *label* in the rail's Tools flyout, opened first."""
    await page.get_by_test_id("rail-tools").click(position=ICON)
    return page.locator(".rail-tools-menu").get_by_role("menuitem", name=re.compile(rf"^{label} \("))


@pytest.mark.asyncio
async def test_vue_app_bootstrap_and_navigation(open_page):
    """Direct URL routing renders each view; boxes lists the local box; the terminal mounts.

    Mutation: route /app/boxes to another view (heading never appears), drop the
    local box from the boxes payload (card count 0), or break the terminal
    component (``.xterm-screen`` never visible) -> the matching expect fails.
    """
    page = await open_page(DESKTOP_VIEWPORT)

    await page.goto("/app/", wait_until="load")
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()

    await page.goto("/app/files", wait_until="load")
    await expect(page.get_by_test_id("files-workspace")).to_be_visible()

    await page.goto("/app/boxes", wait_until="load")
    await expect(page.get_by_role("heading", name="Available boxes")).to_be_visible()
    local_card = page.locator(".card-title").filter(has_text=re.compile(r"\blocal$"))
    await expect(local_card).to_have_count(1)
    await expect(local_card).to_be_visible()

    await page.goto("/app/terminal", wait_until="load")
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)

    await page.goto("/app/", wait_until="load")
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()


@pytest.mark.asyncio
async def test_vue_app_nav_link_clicks(open_page):
    """Each desktop nav link changes the URL and renders its view.

    Mutation: point a nav link at the wrong route -> ``to_have_url`` fails for
    that step; make the terminal view not mount -> ``.xterm-screen`` never shows.
    """
    page = await open_page(DESKTOP_VIEWPORT)

    await page.goto("/app/", wait_until="load")
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()

    await nav_link(page, "Files").click(position=ICON)
    await expect(page).to_have_url(re.compile(r"/app/files(\?.*)?$"))
    await expect(page.get_by_test_id("files-workspace")).to_be_visible()

    await (await tool_link(page, "Boxes")).click()
    await expect(page).to_have_url(re.compile(r"/app/boxes$"))
    await expect(page.get_by_role("heading", name="Available boxes")).to_be_visible()
    await expect(page.get_by_test_id("rail-tools")).to_have_class(re.compile(r"\bactive\b"))
    await expect(page.locator(".rail-tools-menu")).to_be_hidden()

    await nav_link(page, "Terminal").click(position=ICON)
    await expect(page).to_have_url(re.compile(r"/app/terminal(\?.*)?$"))
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)

    await nav_link(page, "Overview").click(position=ICON)
    await expect(page).to_have_url(re.compile(r"/app/$"))
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()


LAPTOP_VIEWPORT = {"width": 1366, "height": 768}
COLLAPSED = 52
EXPANDED = 208


async def box_of(page, selector: str) -> dict[str, float]:
    box = await page.locator(selector).first.bounding_box()
    assert box is not None, f"{selector} is not on the page"
    return {key: round(value) for key, value in box.items()}


async def rail_panel_x(page) -> float:
    """Where the rail's panel stands: 0 expanded, 52 - 208 collapsed (read from its transform)."""
    return await page.evaluate(
        "new DOMMatrix(getComputedStyle(document.querySelector('.rail-panel')).transform).m41"
    )


async def wait_panel_at(page, x: int) -> None:
    await page.wait_for_function(
        """x => {
          const panel = document.querySelector('.rail-panel');
          return panel !== null && Math.round(new DOMMatrix(getComputedStyle(panel).transform).m41) === x;
        }""",
        arg=x,
    )


@pytest.mark.asyncio
async def test_terminal_page_has_no_top_bar_only_the_rail(open_page):
    """At 1366 x 768 the terminal page has the 52 px rail at x 0 the window's height, and
    nothing above the main area.

    Mutation: a top bar back in the shell -> the main area's top is below 0 and
    `.app-header` is found; a rail that is not the full height -> its height is not 768.
    """
    page = await open_page(LAPTOP_VIEWPORT)
    await page.goto("/app/terminal?box=local", wait_until="load")
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)

    assert await box_of(page, ".app-rail") == {"x": 0, "y": 0, "width": COLLAPSED, "height": 768}
    await expect(page.locator(".app-header")).to_have_count(0)
    assert await box_of(page, ".app-main") == {
        "x": COLLAPSED, "y": 0, "width": 1366 - COLLAPSED, "height": 768,
    }


@pytest.mark.asyncio
async def test_expanded_rail_floats_and_closes(open_page):
    """Expanding the rail moves only its panel, by transform: the main area keeps its width
    before, during and after; Escape, a click outside and Alt+\\ close or toggle it.

    Mutation: animate the rail's width (or push the page) -> the main area's width changes
    during or after the toggle; a missing Escape or outside-click handler -> the panel
    stays at x 0.
    """
    page = await open_page(LAPTOP_VIEWPORT)
    await page.goto("/app/files?box=local", wait_until="load")
    await expect(page.get_by_test_id("files-workspace")).to_be_visible()
    await wait_panel_at(page, COLLAPSED - EXPANDED)

    main_before = await box_of(page, ".app-main")
    # The page's own width: the files view's light theme puts a 1 px border on <html>
    # (a stray global rule in the editor's styles), so the body is read, not assumed.
    page_width = await page.evaluate("document.body.clientWidth")
    assert page_width >= 1365
    assert main_before["width"] == page_width - COLLAPSED
    # Sample the main area's width on every frame from the toggle until the panel's
    # transform transition has ended (or 2 s pass). No frame-count floor: a busy box may
    # render few frames; what matters is that every sampled frame kept the width.
    result = await page.evaluate(
        """() => new Promise((resolve) => {
          const main = document.querySelector('.app-main');
          const panel = document.querySelector('.rail-panel');
          const seen = [];
          let ended = false;
          const finish = () => { ended = true; };
          panel.addEventListener('transitionend', (event) => {
            if (event.propertyName === 'transform') finish();
          }, { once: false });
          const giveUp = setTimeout(finish, 2000);
          const tick = () => {
            seen.push(Math.round(main.getBoundingClientRect().width));
            if (!ended) requestAnimationFrame(tick);
            else { clearTimeout(giveUp); resolve(seen); }
          };
          document.querySelector('[data-testid="rail-toggle"]').click();
          requestAnimationFrame(tick);
        })"""
    )
    assert len(result) >= 1
    assert set(result) == {main_before["width"]}
    await wait_panel_at(page, 0)
    assert await box_of(page, ".app-rail") == {"x": 0, "y": 0, "width": COLLAPSED, "height": 768}
    assert await box_of(page, ".rail-panel") == {"x": 0, "y": 0, "width": EXPANDED, "height": 768}
    assert await box_of(page, ".app-main") == main_before
    assert await page.evaluate(
        "getComputedStyle(document.querySelector('.rail-panel')).transitionProperty"
    ) == "transform"
    await expect(page.get_by_test_id("rail-toggle")).to_have_attribute("aria-expanded", "true")

    await page.keyboard.press("Escape")
    await wait_panel_at(page, COLLAPSED - EXPANDED)

    await page.keyboard.press("Alt+Backslash")
    await wait_panel_at(page, 0)
    await page.mouse.click(900, 400)
    await wait_panel_at(page, COLLAPSED - EXPANDED)
    assert await box_of(page, ".app-main") == main_before

    # Remembered: the state is stored, and a reload reads it back. Open it, reload: open.
    await page.get_by_test_id("rail-toggle").click(position=ICON)
    await wait_panel_at(page, 0)
    assert await page.evaluate("localStorage.getItem('sshler:ui:rail-collapsed')") == "false"
    await page.reload(wait_until="load")
    await wait_panel_at(page, 0)
    # Without the stored value the same reload opens collapsed, so the step above proves
    # the storage and not a default.
    await page.evaluate("localStorage.removeItem('sshler:ui:rail-collapsed')")
    await page.reload(wait_until="load")
    await wait_panel_at(page, COLLAPSED - EXPANDED)


@pytest.mark.asyncio
async def test_rail_toggle_sends_no_resize_to_the_terminal(open_page):
    """The expanded rail floats over the terminal, so opening and closing it tells tmux
    nothing: no resize frame is sent on the socket.

    Mutation: a rail that pushes the page -> the terminal refits and sends `resize`.
    """
    page = await open_page(LAPTOP_VIEWPORT)
    sent: list[str] = []

    def on_socket(ws) -> None:
        if "/ws/term" in ws.url:
            ws.on("framesent", lambda payload: sent.append(payload if isinstance(payload, str) else ""))

    page.on("websocket", on_socket)
    await page.goto("/app/terminal?box=local", wait_until="load")
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)
    resize = re.compile(r'"op"\s*:\s*"resize"')
    # The first resize after connect is the terminal's own; wait until it has gone.
    for _ in range(50):
        if any(resize.search(frame) for frame in sent):
            break
        await page.wait_for_timeout(100)
    before = sum(1 for frame in sent if resize.search(frame))
    assert before >= 1

    await page.get_by_test_id("rail-toggle").click(position=ICON)
    await wait_panel_at(page, 0)
    await page.keyboard.press("Escape")
    await wait_panel_at(page, COLLAPSED - EXPANDED)
    # Longer than the terminal's resize debounce (150 ms): a refit would have been sent.
    await page.wait_for_timeout(600)
    assert sum(1 for frame in sent if resize.search(frame)) == before


@pytest.mark.asyncio
async def test_a_query_change_keeps_the_view_and_a_page_change_replaces_it(open_page):
    """The view is keyed by its path: ?dir= changing alone keeps the same element.

    Mutation: key the view by the full URL -> the marked element is replaced after the
    query change.
    """
    page = await open_page(LAPTOP_VIEWPORT)
    await page.goto("/app/files?box=local", wait_until="load")
    await expect(page.get_by_test_id("files-workspace")).to_be_visible()
    await page.evaluate("document.querySelector('.app-content > *').dataset.probe = 'kept'")

    await page.evaluate(
        """() => {
          history.pushState(history.state, '', '/app/files?box=local&dir=%2Ftmp');
          dispatchEvent(new PopStateEvent('popstate', { state: history.state }));
        }"""
    )
    await expect(page).to_have_url(re.compile(r"/app/files\?box=local&dir=%2Ftmp$"))
    await expect(page.locator(".app-content > [data-probe='kept']")).to_have_count(1)

    await nav_link(page, "Overview").click(position=ICON)
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()
    await expect(page.locator("[data-probe='kept']")).to_have_count(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("strip_px", [0, 60])
async def test_files_table_fills_the_window_in_a_full_folder(open_page, tmp_path, strip_px):
    """In a folder of 60 files at 1366 x 768 the table runs to the bottom of the window:
    its bottom border is on screen, it is most of the window high, and the page itself does
    not scroll; with a 60 px block in the progress strip's place it shrinks by that much.

    Mutation: the content box without a height (files not a fill route) -> the table is
    its 200 px floor; the view's own `100dvh - 32px` back -> with the strip the table's
    bottom runs past the window.
    """
    for i in range(60):
        (tmp_path / f"file-{i:02d}.txt").write_text("x")
    viewport = {"width": 1366, "height": 768}
    page = await open_page(viewport)
    await page.goto(f"/app/files?box=local&dir={tmp_path}", wait_until="load")
    await expect(page.get_by_text("file-00.txt")).to_be_visible()
    if strip_px:
        await page.evaluate(
            f"""() => {{
              const block = document.createElement('div');
              block.style.height = '{strip_px}px';
              document.querySelector('.app-frame-fixed').appendChild(block);
            }}"""
        )
    table = page.locator(".table-region .n-data-table")
    await expect(table).to_be_visible()
    box = await table.bounding_box()
    assert box is not None
    bottom = box["y"] + box["height"]
    assert viewport["height"] - 24 <= bottom <= viewport["height"], f"the table ends at {bottom}px"
    assert box["height"] >= 0.6 * viewport["height"] - strip_px, f"the table is {box['height']}px high"
    scroll = await page.evaluate(
        "() => { const s = document.querySelector('.app-main .n-layout-scroll-container');"
        " return s ? s.scrollHeight - s.clientHeight : -1 }"
    )
    assert scroll == 0, f"the page scrolls by {scroll}px"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "ready"),
    [("/app/?box=local", "Your Servers"), ("/app/files?box=local", None)],
    ids=["from-overview", "from-files"],
)
async def test_switching_to_the_terminal_opens_one_socket(open_page, start, ready):
    """The fade between views mounts the terminal once: one /ws/term socket per visit,
    from a scrolling view (the overview) and from a view that fills the frame (files).

    Mutation: a transition that mounts the entering view twice -> two sockets.
    """
    page = await open_page(LAPTOP_VIEWPORT)
    urls: list[str] = []
    page.on("websocket", lambda ws: urls.append(ws.url) if "/ws/term" in ws.url else None)
    await page.goto(start, wait_until="load")
    if ready:
        await expect(page.get_by_role("heading", name=ready)).to_be_visible()
    else:
        await expect(page.get_by_test_id("files-workspace")).to_be_visible()

    await nav_link(page, "Terminal").click(position=ICON)
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)
    await page.wait_for_timeout(500)
    assert len(urls) == 1


@pytest.mark.asyncio
async def test_japanese_rail_labels_fit_at_768(open_page):
    """At exactly 768 px the page is a desktop, and the expanded rail's Japanese labels
    each sit on one line inside it.

    Mutation: a label that wraps (no `nowrap`) -> its height is two lines; a breakpoint at
    769 -> the rail is missing at 768.
    """
    page = await open_page({"width": 768, "height": 700})
    await page.goto("/app/", wait_until="load")
    await page.evaluate("localStorage.setItem('sshler:locale', 'ja')")
    await page.reload(wait_until="load")
    await expect(page.locator(".app-rail")).to_have_count(1)
    await expect(page.locator(".app-header")).to_have_count(0)
    await page.get_by_test_id("rail-toggle").click(position=ICON)
    await wait_panel_at(page, 0)

    labels = await page.evaluate(
        """() => [...document.querySelectorAll('.rail-panel .rail-label:not(.rail-stat-detail)')]
          .filter((el) => el.textContent.trim())
          .map((el) => {
            const box = el.getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(el);
            const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
            return [el.textContent.trim(), lines, el.scrollWidth <= el.clientWidth, Math.round(box.right)];
          })"""
    )
    texts = [label for label, *_ in labels]
    assert "マルチターミナル" in texts, texts
    for text, lines, fits, right in labels:
        assert lines == 1, f"{text} takes {lines} lines"
        assert fits, f"{text} is cut"
        assert right <= EXPANDED, f"{text} leaves the rail ({right}px)"
