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
    base_url, _token = app_server

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        from pathlib import Path

        # Load SPA
        await page.goto(f"{base_url}/app/", wait_until="domcontentloaded")
        await page.wait_for_selector("text=vue spa rollout")

        # Expect overview stats to render (version/token placeholders)
        await page.wait_for_timeout(250)
        assert await page.locator("text=live bootstrap").count() == 1

        # Switch to Files view and fetch directory listing (should succeed even with zero boxes)
        await page.click("text=files")
        await page.wait_for_selector("text=live listing")

        # Set directory and touch a file
        await page.fill("input[placeholder='directory']", "/tmp")
        await page.click("button:has-text('load')")
        filename = f"spa-{uuid.uuid4().hex[:6]}.txt"
        await page.fill("input[placeholder='new filename']", filename)
        await page.click("button:has-text('touch')")
        await page.wait_for_selector(f"text={filename}")

        # Rename
        await page.click(f"button:has-text('rename')")
        await page.fill("input[placeholder='new name']", filename + "-renamed")
        await page.click("button:has-text('rename target')")
        await page.wait_for_selector(f"text={filename}-renamed")

        # Upload
        upload_path = "/tmp/upload-spa.txt"
        with open(upload_path, "w", encoding="utf-8") as fp:
            fp.write("upload data")
        await page.set_input_files("input[type='file']", upload_path)
        await page.wait_for_timeout(200)
        download_ok = await page.evaluate(
            """async () => {
                const token = localStorage.getItem("sshler:token");
                const res = await fetch("/api/v1/boxes/local/download?path=/tmp/upload-spa.txt", {
                  headers: { "X-SSHLER-TOKEN": token }
                });
                const buf = await res.arrayBuffer();
                const text = new TextDecoder().decode(buf);
                return { status: res.status, text };
            }"""
        )
        assert download_ok["status"] == 200
        assert "upload data" in download_ok["text"]
        await page.wait_for_timeout(500)

        # Delete via actions column
        await page.click(f"button:has-text('delete')")
        await page.wait_for_timeout(500)

        # Terminal connect + resize
        await page.click("text=terminal")
        await page.wait_for_selector("text=handshake + sessions")
        await page.click("button:has-text('connect')")
        await page.wait_for_timeout(500)
        await page.set_viewport_size({"width": 800, "height": 600})
        await page.wait_for_timeout(300)

        # Favorites/pins via boxes page
        # Boxes list
        await page.click("text=boxes")
        await page.wait_for_selector("text=Available boxes")
        card = page.locator(".n-card").filter(has_text="local").first
        pin_button = card.get_by_role("button", name="pin")
        favorite_button = card.get_by_role("button").filter(has_text="favorite")
        await pin_button.click()
        await expect(pin_button).to_have_text("unpin")
        await favorite_button.click()
        await expect(favorite_button).to_have_text("unfavorite")

        # Verify favorites/pins reflected in Files view
        await page.click("text=files")
        await page.wait_for_selector("text=live listing")
        favorites_panel = page.locator(".card-title", has_text="favorites").first.locator("..")
        await expect(favorites_panel.get_by_text(str(Path.home()))).to_be_visible()
        await expect(favorites_panel.get_by_role("button", name="unpin")).to_be_visible()
        toggle_dir_button = page.get_by_role("button", name="favorite dir")
        await toggle_dir_button.click()
        await expect(toggle_dir_button).to_have_text("unfavorite dir")
        await expect(favorites_panel.get_by_text("/tmp")).to_be_visible()

        # Reload to ensure persistence
        await page.reload()
        await page.wait_for_selector("text=live listing")
        # Navigate boxes->files to force store hydration after reload
        await page.click("text=boxes")
        await page.wait_for_selector("text=Available boxes")
        await page.click("text=files")
        await page.wait_for_selector("text=live listing")
        favorites_panel_after = page.locator(".card-title", has_text="favorites").first.locator("..")
        await expect(favorites_panel_after.get_by_text("/tmp")).to_be_visible()
        await expect(favorites_panel_after.get_by_role("button", name="unpin")).to_be_visible()

        await browser.close()
