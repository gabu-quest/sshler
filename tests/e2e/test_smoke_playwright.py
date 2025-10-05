"""Playwright E2E smoke tests for basic page rendering.

Tests basic page loading - not the legacy HTMX form flows.

Run with:
    uv run pytest tests/e2e/test_smoke_playwright.py -v
"""
from __future__ import annotations

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
async_playwright = playwright_async.async_playwright


@pytest.mark.asyncio
async def test_legacy_boxes_page_renders(app_server):
    """Legacy HTMX boxes page renders with heading."""
    base_url, _token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{base_url}/boxes", wait_until="domcontentloaded")
        heading = await page.locator("h1").first.inner_text()
        assert "Boxes" in heading
        await browser.close()


@pytest.mark.asyncio
async def test_vue_app_loads(app_server):
    """Vue SPA loads and shows the overview page."""
    base_url, token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        await page.goto(f"{base_url}/app/", wait_until="load")
        # Wait for Vue app to hydrate and show content
        await page.wait_for_selector("text=Your Servers", timeout=10000)
        await browser.close()
