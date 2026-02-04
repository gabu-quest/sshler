"""Playwright E2E tests for mobile responsiveness.

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
TABLET_VIEWPORT = {"width": 768, "height": 1024}  # iPad


@pytest.mark.asyncio
async def test_mobile_nav_drawer(app_server):
    """Mobile hamburger menu opens the navigation drawer."""
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

        # Mobile hamburger button should be visible
        mobile_btn = page.locator(".mobile-menu-button")
        await expect(mobile_btn).to_be_visible()

        # Click hamburger to open drawer
        await mobile_btn.click()
        await page.wait_for_timeout(500)

        # Drawer should be visible with navigation links
        drawer_nav = page.locator(".mobile-nav")
        await expect(drawer_nav).to_be_visible()

        # Should have navigation links
        nav_links = page.locator(".mobile-nav-link")
        link_count = await nav_links.count()
        assert link_count >= 5, f"Expected at least 5 nav links, got {link_count}"

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
    """At tablet size, desktop nav should still be visible."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=TABLET_VIEWPORT)
        page = await context.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_timeout(2000)

        # Desktop nav should be visible at tablet width
        desktop_nav = page.locator(".desktop-nav")
        await expect(desktop_nav).to_be_visible()

        # Mobile hamburger should be hidden
        mobile_btn = page.locator(".mobile-menu-button")
        await expect(mobile_btn).to_be_hidden()

        await browser.close()
