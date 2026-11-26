"""Playwright E2E tests for mobile responsiveness.

Tests the actual mobile implementation:
- The terminal's ultra-thin 14px bar with logo + CPU/MEM stats
- Every other view's 36px bar, whose menu button opens the drawer from the left
- No side rail below 768px; the rail from 768px on
- Pages fit within mobile viewport (no horizontal scroll)

Run with:
    uv run pytest tests/e2e/test_mobile_responsive.py -v
"""

from __future__ import annotations

import re

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api",
    reason="Playwright is not installed; run `playwright install chromium`",
)
expect = playwright_async.expect

# Common mobile/tablet viewports
MOBILE_VIEWPORT = {"width": 375, "height": 667}  # iPhone SE
TABLET_VIEWPORT = {"width": 800, "height": 1024}  # Slightly above 768px breakpoint


async def assert_no_horizontal_overflow(page, label: str) -> None:
    """The document is exactly as wide as the viewport: nothing forces a sideways scroll."""
    page_width = await page.evaluate("document.documentElement.scrollWidth")
    assert page_width == MOBILE_VIEWPORT["width"], f"{label} scrollWidth {page_width}px != viewport"


@pytest.mark.asyncio
async def test_mobile_header_ultra_thin(open_page):
    """On the terminal the phone bar is the 14px strip, with no menu button.

    Mutation: give the terminal the 36px bar (or drop the 14px height) -> the
    exact-height assertion fails.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/terminal", wait_until="load")
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)

    header = page.locator(".app-header")
    await expect(header).to_be_visible()

    header_height = await header.evaluate("el => el.getBoundingClientRect().height")
    assert header_height == 14, f"Mobile header is {header_height}px, expected 14px"
    await expect(page.locator(".mobile-menu-btn")).to_have_count(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/app/", "/app/files", "/app/multi-terminal", "/app/commander"])
async def test_mobile_nav_bar_and_drawer(open_page, path):
    """Every view but the terminal has the 36px bar; its menu button opens the drawer
    from the left, and a pick in the drawer navigates and closes it.

    Mutation: no menu button (the drawer nothing opens) -> the click finds nothing; a
    drawer from the right -> its left edge is not 0; a 14px bar here -> the height fails.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto(path, wait_until="load")
    header = page.locator(".app-header.app-header--nav")
    await expect(header).to_be_visible()
    assert await header.evaluate("el => el.getBoundingClientRect().height") == 36
    await expect(page.locator(".app-rail")).to_have_count(0)

    await page.get_by_role("button", name="Open menu").click()
    drawer = page.locator(".n-drawer")
    await expect(drawer).to_be_visible()
    settings = drawer.get_by_role("link", name="Settings")
    await expect(settings).to_be_visible()
    await page.wait_for_function(
        "() => { const d = document.querySelector('.n-drawer'); return d && Math.round(d.getBoundingClientRect().left) === 0; }"
    )
    await settings.click()
    await expect(page).to_have_url(re.compile(r"/app/settings$"))
    await expect(drawer).to_have_count(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("width", "rails", "bars"), [(767, 0, 1), (768, 1, 0)], ids=["767-phone", "768-desktop"]
)
async def test_the_breakpoint_is_768(open_page, width, rails, bars):
    """767px is a phone (the bar, no rail); 768px is a desktop (the rail, no bar).

    Mutation: a breakpoint at 769 or a CSS rule at `max-width: 768px` -> 768 shows the bar.
    """
    page = await open_page({"width": width, "height": 700})
    await page.goto("/app/files", wait_until="load")
    await expect(page.get_by_text("File Browser & Editor", exact=True)).to_be_visible()
    await expect(page.locator(".app-rail")).to_have_count(rails)
    await expect(page.locator(".app-header")).to_have_count(bars)


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [MOBILE_VIEWPORT, {"width": 1366, "height": 768}], ids=["phone", "desktop"])
async def test_sign_in_page_has_no_navigation(open_page, viewport):
    """The sign-in page shows the form alone: no rail, no bar.

    Mutation: render the shell's navigation on /login -> a count is 1.
    """
    page = await open_page(viewport)
    await page.goto("/app/login", wait_until="load")
    await expect(page.get_by_placeholder("Enter username")).to_be_visible()
    await expect(page.locator(".app-rail")).to_have_count(0)
    await expect(page.locator(".app-header")).to_have_count(0)


@pytest.mark.asyncio
async def test_mobile_terminal_renders(open_page):
    """Terminal page renders its xterm inside the mobile viewport without overflow.

    Mutation: give the terminal a fixed width wider than 375px -> the xterm
    box leaves the viewport and the scrollWidth equality fails; a zero or
    tiny terminal -> the 300px lower bound fails.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/terminal", wait_until="load")

    screen = page.locator(".xterm-screen")
    await expect(screen).to_be_visible(timeout=15000)

    box = await screen.bounding_box()
    assert box is not None
    assert box["x"] + box["width"] <= MOBILE_VIEWPORT["width"]
    assert box["width"] >= 300, f"terminal is only {box['width']}px wide"
    await assert_no_horizontal_overflow(page, "terminal")


@pytest.mark.asyncio
async def test_mobile_file_browser_no_horizontal_scroll(open_page):
    """File browser at mobile width renders and does not require horizontal scrolling.

    Mutation: make the file list wider than the viewport -> scrollWidth
    exceeds 375.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/files", wait_until="load")
    await expect(page.get_by_text("File Browser & Editor", exact=True)).to_be_visible()

    await assert_no_horizontal_overflow(page, "file browser")


@pytest.mark.asyncio
async def test_overview_grid_collapses_on_mobile(open_page):
    """Overview renders at mobile width, its grid does not overflow, and a card spans the viewport.

    Mutation: a multi-column grid with a min width past the viewport ->
    scrollWidth exceeds 375; a grid that never collapses (cards stay a third
    of the width) -> the card width is below 0.8 x the viewport and the
    assertion fails.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/", wait_until="load")
    await expect(page.get_by_text("Your Servers", exact=True)).to_be_visible()

    await assert_no_horizontal_overflow(page, "overview")

    card = page.locator(".server-card").first
    await expect(card).to_be_visible()
    card_box = await card.bounding_box()
    assert card_box is not None
    min_width = 0.8 * MOBILE_VIEWPORT["width"]
    assert card_box["width"] >= min_width, (
        f"overview card is {card_box['width']}px wide, expected >= {min_width}px "
        "(grid did not collapse)"
    )


@pytest.mark.asyncio
async def test_tablet_viewport_layout(open_page):
    """At tablet width the side rail is the navigation and lists the main destinations.

    Mutation: raise the mobile breakpoint above 800px -> nav hidden, the
    visibility check fails.
    """
    page = await open_page(TABLET_VIEWPORT)
    await page.goto("/app/", wait_until="load")

    desktop_nav = page.locator(".desktop-nav")
    await expect(desktop_nav).to_be_visible()
    await expect(desktop_nav.get_by_role("link", name=re.compile(r"^Terminal \("))).to_have_count(1)
    await expect(page.locator(".app-header")).to_have_count(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wrapper", "dropdown", "query"),
    [
        (".directory-search", ".search-dropdown", "no-such-folder-name"),
        (".content-search", ".grep-dropdown", "no-such-text-in-any-file"),
    ],
    ids=["folder-search", "content-search"],
)
async def test_mobile_search_dropdown_opens_under_its_input(open_page, wrapper, dropdown, query):
    """A search's dropdown opens right under its input on a phone, inside the viewport.

    Mutation: position the dropdown against the viewport with `top: 100%` -> its
    top is the viewport's bottom edge, so nothing of it can be seen.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/files?box=local", wait_until="load")
    field = page.locator(wrapper).first
    await field.locator("input").fill(query)
    panel = page.locator(dropdown).first
    await expect(panel).to_be_attached()

    field_box = await field.bounding_box()
    panel_box = await panel.bounding_box()
    # 4px is the dropdown's own margin under the input.
    assert round(panel_box["y"]) == round(field_box["y"] + field_box["height"]) + 4
    assert panel_box["x"] >= 0
    assert panel_box["x"] + panel_box["width"] <= MOBILE_VIEWPORT["width"]
