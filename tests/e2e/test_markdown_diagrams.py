"""Playwright E2E tests for Mermaid diagrams in the Markdown file preview.

Run with:
    uv run pytest tests/e2e/test_markdown_diagrams.py -v

The frontend must be built first (``pnpm --prefix frontend build``): the server
serves ``sshler/static/dist``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}

DIAGRAMS_MD = """# Diagrams

```mermaid
flowchart TD
    A[Start] --> B{Is it ready?}
    B -->|Yes| C[Ship it]
    B -->|No| D[Keep going]
```

```mermaid
sequenceDiagram
    participant Alice
    participant Bob
    Alice->>Bob: Hello Bob
    Bob-->>Alice: Hi Alice
```

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: begin
    Running --> [*]
```

```mermaid
this is not a diagram
```
"""

HOSTILE_MD = """# Hostile

```mermaid
flowchart TD
    A["<script>window.__pwned = 1</script>Safe text"] --> B
    B["<img src=x onerror=window.__pwned=2>Other text"]
```
"""

FLOWCHART_LABELS = ["Start", "Is it ready?", "Ship it", "Keep going", "Yes", "No"]
SEQUENCE_LABELS = ["Alice", "Bob", "Hello Bob", "Hi Alice"]
STATE_LABELS = ["Idle", "Running", "begin"]


async def open_preview(page, directory: Path, name: str) -> None:
    await page.goto(f"/app/files?box=local&dir={directory}&preview={name}", wait_until="load")
    await expect(page.locator(".markdown-rendered")).to_be_visible(timeout=15000)


@pytest.mark.asyncio
async def test_markdown_preview_renders_each_diagram_as_svg(open_page, tmp_path):
    """Three valid diagrams become three SVGs with their labels; the invalid one shows an error box.

    Mutation: make ``renderMermaidBlocks`` return at once -> the rendered count
    stays 0 and the code blocks remain; change a label -> the text assertion fails.
    """
    (tmp_path / "diagrams.md").write_text(DIAGRAMS_MD, encoding="utf-8")
    page = await open_page(DESKTOP_VIEWPORT)
    await open_preview(page, tmp_path, "diagrams.md")

    rendered = page.locator(".markdown-rendered .mermaid-rendered")
    await expect(rendered).to_have_count(3, timeout=30000)
    await expect(page.locator(".markdown-rendered .mermaid-error")).to_have_count(1)
    # The three valid code blocks are gone; only the invalid one is left, as an error box.
    await expect(page.locator(".markdown-rendered code.language-mermaid")).to_have_count(0)

    expected = [FLOWCHART_LABELS, SEQUENCE_LABELS, STATE_LABELS]
    for index, labels in enumerate(expected):
        svg = rendered.nth(index).locator("svg")
        await expect(svg).to_have_count(1)
        texts = [
            t.strip()
            for t in await svg.evaluate(
                "el => Array.from(el.querySelectorAll('text, foreignObject p, foreignObject span'))"
                ".map(n => n.textContent)"
            )
            if t.strip()
        ]
        for label in labels:
            assert label in texts, f"diagram {index}: {label!r} not in {texts}"

    # A node is as wide as its text needs: mermaid 12 gives short labels a shared minimum
    # width unless `minNodeWidth: 0` is pinned (frontend/src/utils/mermaidConfig.ts).
    widths = await rendered.nth(0).evaluate(
        """el => Object.fromEntries(Array.from(el.querySelectorAll('g.node')).map(
            n => [n.textContent.trim(), n.querySelector('rect, polygon').getBBox().width]))"""
    )
    assert sorted(widths) == ["Is it ready?", "Keep going", "Ship it", "Start"]
    assert widths["Start"] < widths["Ship it"] < widths["Keep going"]

    error = (await page.locator(".markdown-rendered .mermaid-error").inner_text()).strip()
    assert error.startswith("Mermaid render error:")
    assert error.endswith("this is not a diagram")


@pytest.mark.asyncio
async def test_markdown_preview_diagram_svg_carries_no_script(open_page, tmp_path):
    """A label holding <script> or an onerror attribute leaves no script element or on* attribute.

    Mutation: set mermaid's ``securityLevel`` to ``loose`` -> the SVG keeps the
    injected markup and the assertions fail.
    """
    (tmp_path / "hostile.md").write_text(HOSTILE_MD, encoding="utf-8")
    page = await open_page(DESKTOP_VIEWPORT)
    await open_preview(page, tmp_path, "hostile.md")

    rendered = page.locator(".markdown-rendered .mermaid-rendered")
    await expect(rendered).to_have_count(1, timeout=30000)
    await expect(page.locator(".markdown-rendered .mermaid-error")).to_have_count(0)

    found = json.loads(
        await rendered.evaluate(
            """el => JSON.stringify({
                scripts: el.querySelectorAll('script').length,
                handlers: Array.from(el.querySelectorAll('*')).flatMap(
                    n => n.getAttributeNames().filter(a => a.toLowerCase().startsWith('on'))),
                text: el.textContent,
            })"""
        )
    )
    assert found["scripts"] == 0
    assert found["handlers"] == []
    assert "Safe text" in found["text"]
    assert "Other text" in found["text"]
    assert await page.evaluate("window.__pwned") is None
