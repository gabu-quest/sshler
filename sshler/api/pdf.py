"""PDF export endpoint — converts frontend-rendered HTML to PDF via Playwright.

The frontend builds a complete HTML document (the same one used for the print
window) and POSTs it here. This module hands it to PDF_RENDERER and streams
the resulting PDF back. If chromium isn't installed, answers `program_missing` (501) — the
frontend hides the button when /api/v1/bootstrap reports pdf_available=false,
so this path mostly handles the race where availability changes mid-session.
"""

from __future__ import annotations

import io
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..pdf import PDF_RENDERER
from .dependencies import APIDependencies
from .failure import REASON_FAILED, REASON_PROGRAM_MISSING, Failure

# Cap protects against runaway uploads. Real markdown docs with inlined
# images are well under 1 MB; 20 MB is a generous sanity limit.
_MAX_HTML_BYTES = 20_000_000


class PDFRenderRequest(BaseModel):
    html: str = Field(..., min_length=1)
    filename: str = "document.pdf"


def _safe_filename(name: str) -> str:
    """Strip path separators and quotes so the filename can't break the
    Content-Disposition header or escape downloads dirs."""
    name = name.strip() or "document.pdf"
    # Remove anything that isn't filename-safe; keep dots and dashes.
    name = re.sub(r"[^A-Za-z0-9._\- ]", "_", name)
    if not name.lower().endswith(".pdf"):
        name = name + ".pdf"
    return name[:200]  # cap length too


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/pdf/render")
    async def render_pdf(req: PDFRenderRequest) -> StreamingResponse:
        if not PDF_RENDERER.available:
            raise Failure(
                REASON_PROGRAM_MISSING,
                "PDF export not available. Install with "
                "'pip install sshler[pdf]' and run 'playwright install chromium'.",
            )
        if len(req.html.encode("utf-8")) > _MAX_HTML_BYTES:
            raise Failure("too_large", "HTML payload too large")

        try:
            pdf_bytes = await PDF_RENDERER.render(req.html)
        except RuntimeError as exc:
            # Renderer flipped to unavailable since the request arrived.
            raise Failure(REASON_PROGRAM_MISSING, str(exc)) from exc
        except Exception as exc:
            raise Failure(REASON_FAILED, f"PDF render failed: {exc}") from exc

        safe_name = _safe_filename(req.filename)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    return router
