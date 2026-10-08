"""Playwright E2E tests for mobile responsiveness.

Tests the actual mobile implementation:
- Ultra-thin 14px header with logo + CPU/MEM stats
- No hamburger menu (mobile navigation removed for maximum screen space)
- Desktop nav hidden on mobile
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
    """Mobile header is the 14px strip from AppHeader.vue's mobile media query.

    Mutation: drop the mobile ``height: 14px`` rule (22px desktop height) or
    collapse the header to 0 -> the exact-height assertion fails.
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/", wait_until="load")

    header = page.locator(".app-header")
    await expect(header).to_be_visible()

    header_height = await header.evaluate("el => el.getBoundingClientRect().height")
    assert header_height == 14, f"Mobile header is {header_height}px, expected 14px"


@pytest.mark.asyncio
async def test_mobile_desktop_nav_hidden(open_page):
    """Desktop nav is in the DOM but hidden on a mobile viewport.

    Mutation: remove the mobile ``.desktop-nav { display: none }`` rule ->
    ``to_be_hidden`` fails. The header-visible and count checks stop a page that
    never rendered from passing as "hidden".
    """
    page = await open_page(MOBILE_VIEWPORT)
    await page.goto("/app/", wait_until="load")
    await expect(page.locator(".app-header")).to_be_visible()

    desktop_nav = page.locator(".desktop-nav")
    await expect(desktop_nav).to_have_count(1)
    await expect(desktop_nav).to_be_hidden()


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
    """At tablet width the desktop nav is visible and lists the main destinations.

    Mutation: raise the mobile breakpoint above 800px -> nav hidden, the
    visibility check fails.
    """
    page = await open_page(TABLET_VIEWPORT)
    await page.goto("/app/", wait_until="load")

    desktop_nav = page.locator(".desktop-nav")
    await expect(desktop_nav).to_be_visible()
    await expect(desktop_nav.get_by_role("link", name=re.compile(r"^Terminal \("))).to_have_count(1)
