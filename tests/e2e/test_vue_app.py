from __future__ import annotations

import pytest
import uuid

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
async_playwright = playwright_async.async_playwright
expect = playwright_async.expect


@pytest.mark.asyncio
async def test_vue_app_bootstrap_and_boxes(app_server):
    """Test Vue SPA basic navigation and file operations."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Load SPA - the overview page shows "Welcome back!"
        await page.goto(f"{base_url}/app/", wait_until="load")
        await page.wait_for_selector("text=Welcome back!", timeout=10000)

        # Verify the dashboard loaded with server info
        await page.wait_for_selector("text=Your Servers", timeout=5000)

        # Navigate to Files view
        await page.click("text=Files")
        await page.wait_for_selector("text=File Browser", timeout=5000)

        # Navigate to Boxes view
        await page.click("text=Boxes")
        await page.wait_for_selector("text=Available Boxes", timeout=5000)

        # Verify local box is shown
        local_box = page.locator("text=local").first
        await expect(local_box).to_be_visible()

        # Navigate to Terminal view
        await page.click("text=Terminal")
        await page.wait_for_selector("text=Terminal", timeout=5000)

        # Navigate back to overview
        await page.click("text=Overview")
        await page.wait_for_selector("text=Welcome back!", timeout=5000)

        await browser.close()
