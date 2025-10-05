"""Playwright E2E tests for Vue SPA navigation.

Run with:
    uv run pytest tests/e2e/test_vue_app.py -v
"""
from __future__ import annotations

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
async_playwright = playwright_async.async_playwright
expect = playwright_async.expect


@pytest.mark.asyncio
async def test_vue_app_bootstrap_and_navigation(app_server):
    """Test Vue SPA basic navigation via direct URL routing."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use desktop viewport to ensure nav is visible
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Load SPA - wait for the overview page to load
        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_selector("text=Your Servers", timeout=10000)

        # Navigate to Files view via URL (more reliable than clicking)
        await page.goto(f"{base_url}/app/files", wait_until="load")
        await page.wait_for_selector("text=File Browser", timeout=5000)

        # Navigate to Boxes view via URL
        await page.goto(f"{base_url}/app/boxes", wait_until="load")
        await page.wait_for_selector("text=Available Boxes", timeout=5000)

        # Verify local box is shown
        local_box = page.locator("text=local").first
        await expect(local_box).to_be_visible()

        # Navigate to Terminal view via URL
        await page.goto(f"{base_url}/app/terminal", wait_until="load")
        await page.wait_for_timeout(2000)  # Terminal takes a moment to initialize

        # Navigate back to overview via URL
        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_selector("text=Your Servers", timeout=5000)

        await browser.close()


@pytest.mark.asyncio
async def test_vue_app_nav_link_clicks(app_server):
    """Test Vue SPA navigation by clicking nav links (desktop viewport)."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use desktop viewport to ensure nav links are visible
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Load SPA
        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_selector("text=Your Servers", timeout=10000)

        # Click nav links in the desktop nav
        await page.click(".desktop-nav >> text=Files")
        await page.wait_for_selector("text=File Browser", timeout=5000)

        await page.click(".desktop-nav >> text=Boxes")
        await page.wait_for_selector("text=Available Boxes", timeout=5000)

        await page.click(".desktop-nav >> text=Terminal")
        await page.wait_for_timeout(2000)

        await page.click(".desktop-nav >> text=Overview")
        await page.wait_for_selector("text=Your Servers", timeout=5000)

        await browser.close()
