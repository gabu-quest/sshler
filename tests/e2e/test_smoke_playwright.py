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

        # Create a new custom box using fetch (since form POST doesn't include extra headers)
        await page.goto(f"{base_url}/boxes/new", wait_until="domcontentloaded")

        # Use JavaScript fetch to submit the form with the token header
        # (Native form POST doesn't include custom headers set via set_extra_http_headers)
        result = await page.evaluate(
            """async ([url, token, boxName]) => {
                const formData = new FormData();
                formData.append('name', boxName);
                formData.append('host', '192.0.2.10');
                formData.append('user', 'demo');
                formData.append('port', '22');
                formData.append('default_dir', '/home/demo');
                formData.append('keyfile', '');
                formData.append('ssh_alias', '');
                formData.append('favorites', '');
                formData.append('known_hosts', '');

                const response = await fetch(url + '/boxes/new', {
                    method: 'POST',
                    headers: { 'x-sshler-token': token },
                    body: formData,
                    redirect: 'follow'
                });
                return { ok: response.ok, status: response.status };
            }""",
            [base_url, token, box_name],
        )
        assert result["ok"], f"Form submission failed with status {result['status']}"

        # Navigate to boxes page to verify
        await page.goto(f"{base_url}/boxes", wait_until="load")

        # Wait for the new box to appear
        box_locator = page.locator(f'li.card[data-box-name="{box_name}"]')
        await box_locator.wait_for(timeout=10000)

        # Verify the box title
        box_title = await box_locator.locator(".title").inner_text()
        assert box_title == box_name

        await browser.close()
