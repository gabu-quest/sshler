"""Server-side HTML→PDF rendering via Playwright (headless Chromium).

Playwright is an optional dependency (the `[pdf]` extra). If it isn't
installed or chromium isn't available on disk, the renderer reports
itself as unavailable and the frontend hides the PDF button — the app
otherwise runs normally.

The browser is launched once at app startup (via the FastAPI lifespan)
and reused across requests. A fresh BrowserContext+Page is created per
render and closed after — cheap (~50ms) and keeps requests isolated.
A module-level asyncio.Lock serializes Page creation; sshler is a
single-user localhost tool, so parallelism here would buy nothing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PDFRenderer:
    """Singleton wrapper around a long-lived Playwright/Chromium instance."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self.available: bool = False

    async def start(self) -> None:
        """Try to launch chromium. Sets self.available based on outcome.

        Never raises — a failure here is a soft 'feature off' state, not an
        app-startup failure. The most common failure modes are:
          - playwright not installed (user didn't install the [pdf] extra)
          - chromium not installed (`playwright install chromium` not run)
          - sandboxing / OS denial in unusual environments
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.info("PDF export unavailable: playwright not installed (pip install 'sshler[pdf]')")
            self.available = False
            return

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self.available = True
            logger.info("PDF export available (headless chromium launched)")
        except Exception as exc:
            logger.info("PDF export unavailable: %s (try 'playwright install chromium')", exc)
            # Clean up partial state on failure
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._browser = None
            self.available = False

    async def stop(self) -> None:
        """Close the browser and stop the playwright driver."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.warning("Error closing chromium: %s", exc)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning("Error stopping playwright: %s", exc)
            self._playwright = None
        self.available = False

    async def render(self, html: str) -> bytes:
        """Render an HTML document to PDF bytes.

        The HTML must be a complete document (own <!DOCTYPE>, <html>, <head>
        with <style>, <body>). The renderer trusts the HTML — sshler is
        single-user localhost and the HTML originates from our own frontend.
        """
        if not self.available or self._browser is None:
            raise RuntimeError("PDF renderer not available")

        async with self._lock:
            ctx = await self._browser.new_context()
            try:
                page = await ctx.new_page()
                # `networkidle` waits for the document to settle. Since we inline
                # images as data: URLs and embed mermaid SVGs directly, there's
                # near-zero real network activity and this fires quickly.
                await page.set_content(html, wait_until="networkidle")
                pdf_bytes: bytes = await page.pdf(
                    format="A4",
                    margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"},
                    print_background=True,
                )
                return pdf_bytes
            finally:
                await ctx.close()


PDF_RENDERER = PDFRenderer()
