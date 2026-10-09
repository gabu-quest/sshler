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


def nav_link(page, label: str):
    """The single desktop-nav link for *label* (accessible name is ``"<label> (<shortcut>)"``)."""
    return page.locator(".desktop-nav").get_by_role("link", name=re.compile(rf"^{label} \("))


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
    await expect(page.get_by_text("File Browser & Editor", exact=True)).to_be_visible()

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

    await nav_link(page, "Files").click()
    await expect(page).to_have_url(re.compile(r"/app/files(\?.*)?$"))
    await expect(page.get_by_text("File Browser & Editor", exact=True)).to_be_visible()

    await nav_link(page, "Boxes").click()
    await expect(page).to_have_url(re.compile(r"/app/boxes$"))
    await expect(page.get_by_role("heading", name="Available boxes")).to_be_visible()

    await nav_link(page, "Terminal").click()
    await expect(page).to_have_url(re.compile(r"/app/terminal(\?.*)?$"))
    await expect(page.locator(".xterm-screen")).to_be_visible(timeout=15000)

    await nav_link(page, "Overview").click()
    await expect(page).to_have_url(re.compile(r"/app/$"))
    await expect(page.get_by_role("heading", name="Your Servers")).to_be_visible()
