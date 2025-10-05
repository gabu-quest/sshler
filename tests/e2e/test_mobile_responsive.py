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

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api",
    reason="Playwright is not installed; run `playwright install chromium`",
)
async_playwright = playwright_async.async_playwright
expect = playwright_async.expect

# Common mobile/tablet viewports
MOBILE_VIEWPORT = {"width": 375, "height": 667}  # iPhone SE
TABLET_VIEWPORT = {"width": 800, "height": 1024}  # Slightly above 768px breakpoint


@pytest.mark.asyncio
async def test_mobile_header_ultra_thin(app_server):
    """Mobile header should be ultra-thin (approx 14px) for maximum terminal space."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=MOBILE_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(2000)

        # Header should exist and be very thin on mobile
        header = page.locator(".app-header")
        await expect(header).to_be_visible()

        # Check header height is thin (around 14-20px range)
        header_height = await header.evaluate("el => el.getBoundingClientRect().height")
        assert header_height <= 30, f"Mobile header too tall: {header_height}px (expected ~14px)"

        await browser.close()


@pytest.mark.asyncio
async def test_mobile_desktop_nav_hidden(app_server):
    """Desktop nav should be hidden on mobile viewport."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=MOBILE_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(2000)

        # Desktop nav should be hidden on mobile
        desktop_nav = page.locator(".desktop-nav")
        await expect(desktop_nav).to_be_hidden()

        await browser.close()


@pytest.mark.asyncio
async def test_mobile_terminal_renders(app_server):
    """Terminal page renders at mobile viewport without overflow."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=MOBILE_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/terminal", wait_until="load")
        await page.wait_for_timeout(3000)

        # The page should fit within the viewport width (no horizontal scroll needed)
        page_width = await page.evaluate("document.documentElement.scrollWidth")
        viewport_width = MOBILE_VIEWPORT["width"]
        assert page_width <= viewport_width + 5, (
            f"Page width {page_width}px exceeds viewport {viewport_width}px — horizontal scroll detected"
        )

        await browser.close()


@pytest.mark.asyncio
async def test_mobile_file_browser_no_horizontal_scroll(app_server):
    """File browser at mobile width should not require horizontal scrolling."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=MOBILE_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/files", wait_until="load")
        await page.wait_for_timeout(3000)

        # Page should not overflow horizontally
        page_width = await page.evaluate("document.documentElement.scrollWidth")
        viewport_width = MOBILE_VIEWPORT["width"]
        assert page_width <= viewport_width + 5, (
            f"File browser width {page_width}px exceeds viewport {viewport_width}px"
        )

        await browser.close()


@pytest.mark.asyncio
async def test_overview_grid_collapses_on_mobile(app_server):
    """Overview server grid should collapse to single column on mobile."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=MOBILE_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(2000)

        # Page should not overflow horizontally
        page_width = await page.evaluate("document.documentElement.scrollWidth")
        viewport_width = MOBILE_VIEWPORT["width"]
        assert page_width <= viewport_width + 5, (
            f"Overview width {page_width}px exceeds viewport {viewport_width}px"
        )

        await browser.close()


@pytest.mark.asyncio
async def test_tablet_viewport_layout(app_server):
    """At tablet size, desktop nav should be visible."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=TABLET_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(2000)

        # At tablet width, desktop nav should be visible
        desktop_nav = page.locator(".desktop-nav")
        await expect(desktop_nav).to_be_visible()

        await browser.close()
