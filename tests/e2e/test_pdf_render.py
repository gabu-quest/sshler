"""The PDF renderer against the real headless Chromium.

Nothing else renders a PDF: the unit tests cover the route with the renderer
switched off, so a Playwright or Chromium that stopped producing PDFs would go
unnoticed, and the renderer itself reports a browser it cannot launch as
"feature off" rather than as an error.
"""

from __future__ import annotations

import re

import pytest

from sshler.pdf import PDFRenderer

TWO_PAGES = """<!DOCTYPE html>
<html>
  <head><style>.page { break-after: page; }</style></head>
  <body>
    <div class="page"><h1>First page</h1></div>
    <div><h1>Second page</h1></div>
  </body>
</html>"""

# A4 in PDF points (1/72 inch): 210 mm x 297 mm.
A4_POINTS = (595.3, 841.9)


@pytest.mark.asyncio
async def test_renders_a_two_page_a4_pdf():
    renderer = PDFRenderer()
    await renderer.start()
    try:
        assert renderer.available is True
        pdf = await renderer.render(TWO_PAGES)
    finally:
        await renderer.stop()

    assert pdf[:5] == b"%PDF-"
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 2
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
    assert len(boxes) == 2
    for width, height in boxes:
        assert float(width) == pytest.approx(A4_POINTS[0], abs=1.0)
        assert float(height) == pytest.approx(A4_POINTS[1], abs=1.0)
    assert renderer.available is False


@pytest.mark.asyncio
async def test_a_stopped_renderer_refuses_to_render():
    renderer = PDFRenderer()
    with pytest.raises(RuntimeError, match="PDF renderer not available"):
        await renderer.render(TWO_PAGES)
