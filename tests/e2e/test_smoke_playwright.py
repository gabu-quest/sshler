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
expect = playwright_async.expect


@pytest.mark.asyncio
async def test_vue_app_loads(open_page):
    """Vue SPA loads and shows the overview page.

    Mutation: the SPA fails to hydrate (empty shell) -> the heading never
    appears and ``to_be_visible`` fails.
    """
    page = await open_page()

    await page.goto("/app/", wait_until="load")
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()
