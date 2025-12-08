from __future__ import annotations

import uuid

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
async_playwright = playwright_async.async_playwright


@pytest.mark.asyncio
async def test_boxes_page_renders(app_server):
    base_url, _token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"{base_url}/boxes", wait_until="domcontentloaded")
        heading = await page.locator("h1").first.inner_text()
        assert "Boxes" in heading
        await browser.close()


@pytest.mark.asyncio
async def test_create_custom_box_flow(app_server):
    base_url, token = app_server
    box_name = f"play-{uuid.uuid4().hex[:8]}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"X-SSHLER-TOKEN": token})

        # Create a new custom box
        await page.goto(f"{base_url}/boxes/new", wait_until="domcontentloaded")
        await page.fill("input[name=name]", box_name)
        await page.fill("input[name=host]", "192.0.2.10")
        await page.fill("input[name=user]", "demo")
        await page.fill("input[name=default_dir]", "/home/demo")
        await page.click("text=Save Box")

        # Verify it appears on the boxes page
        await page.wait_for_url(f"{base_url}/boxes")
        await page.wait_for_selector(f'[data-box-name="{box_name}"]')
        assert await page.locator(f'[data-box-name="{box_name}"] .title').inner_text() == box_name

        await browser.close()
