---
name: sshler-markdown-preview
description: Reference for sshler's markdown preview and print-to-PDF pipeline — what renders, what doesn't, and the non-obvious browser quirks. Load when working on FilePreviewModal.vue, debugging "why does X look weird in print", or extending the print pipeline.
---

# sshler — Markdown Preview & Print Quirks

The render and print paths live in `frontend/src/components/FilePreviewModal.vue`. This skill captures the pipeline shape, the rules each stage enforces, and the print-engine quirks that have been worked around. Read it before "fixing" anything that looks redundant — most of the seemingly-strange code is load-bearing for a specific browser behaviour.

## Pipeline

```
file content (string)
  → marked.parse()         → raw HTML
  → DOMPurify.sanitize()   → safe HTML (v-html into .markdown-rendered)
  → renderMermaidBlocks()  → replaces <code.language-mermaid> with inline SVG
  → inlineMarkdownImages() → rewrites <img src> from path → data: URL
```

Mermaid rendering and image inlining run in parallel after the v-html commit. They touch disjoint DOM nodes (`code.language-mermaid` vs `img`) so they don't race.

## Images

- `![alt](./relative/path.png)` and `![alt](../sibling.jpg)` resolve against the markdown file's directory using the same logic as link-click navigation.
- `![alt](/absolute/path.png)` is treated as a remote-absolute path and fetched as-is.
- `![alt](https://example.com/foo.png)` is left untouched — loads in the modal from the network; behaviour in the print window depends on whether that window has network access (no auth/cookies are forwarded).
- Images that exceed the backend preview byte limit (`image_too_large`) stay broken — same limit as opening them directly via the previewer.
- The inlined-image cache is per-preview and clears when a different file is opened.
- Duplicate references in the same file (one image cited 5×) fetch once.

**Why data: URLs and not a backend image-proxy route?** The print window is a new browser context. It does NOT carry the parent's auth token, so any `/api/...` URL would 403. Inlining as data URLs makes the print HTML self-contained.

## Mermaid

- Fenced blocks tagged ```` ```mermaid ```` are replaced with inline SVG.
- HTML entities (`&lt;`, `&ge;`, `&amp;`) in mermaid source are decoded before rendering — markdown authors commonly write `&lt;` to dodge markdown's `<` handling, and mermaid's lexer would otherwise reject them.
- Literal `\n` (backslash + n) in state-diagram descriptions and `classDef` strings is translated to `<br/>` — mermaid renders that as a line break wherever htmlLabels is on (the default).
- SVG `width`/`height` attrs are stripped after render so CSS can scale via the viewBox. Without this, tall diagrams keep their literal pixel height in print and overflow onto extra pages.
- Diagrams with aspect ratio > 1.4:1 are tagged `.mermaid-wide` and assigned `page: wide-landscape` so they get their own landscape page when printing.
- Mermaid's own DOMPurify pass (via `securityLevel: "strict"` at init) is trusted; the rendered SVG is NOT re-sanitised. A second pass strips `<foreignObject>` HTML labels (XHTML namespace) and produces empty shapes.
- Render errors become a visible `<pre class="mermaid-error">` containing the source — not a silent fail.

## Print to PDF

Two entry points, one pipeline:

1. **Modal Print button** — visible on any non-image preview. Calls `doPrint()` which dispatches to `printRendered()` (markdown) or `printSource()` (text/code).
2. **File-list context menu → Print** — sets `autoPrint=true` on the modal, opens it, and the modal fires `doPrint()` automatically once content loads. No manual Render → Print step needed.

**Markdown print** uses `printRendered()`:

- Auto-toggles render mode if needed.
- Awaits the markdown render + mermaid renders + image inlining before snapshotting the DOM.
- Opens a popup, writes scaffolding HTML, then clones the live rendered DOM via `importNode` (NOT innerHTML stringify — that drops SVG/XHTML namespace info and breaks `<foreignObject>` labels in complex mermaid diagrams).
- Final 250ms `setTimeout` after the print window's `load` event gives SVGs time to lay out before `win.print()` fires.

**Source print** (`printSource()`) opens the same kind of popup and dumps the raw content into `<pre><code>` with the file path shown above it as `.file-header`. Uses the shared `buildPrintStyles()` so it visually matches markdown print output.

**Color handling — load-bearing:**

- `* { print-color-adjust: exact !important; -webkit-print-color-adjust: exact !important }` forces browsers to actually print background colours. The default `print-color-adjust: economy` silently drops backgrounds to save toner — that turns inline-HTML banners with light text on a coloured background (e.g. `<div style="background:#b91c1c;color:#fff">`) into invisible white-on-white. The `*` selector is intentional — we want every element to participate, including author-supplied inline-styled blocks.
- The print palette is hard-coded light (`fg #1a1a1a`, `bg #fff`, `muted #f3f3f3`, `stroke #ddd`) regardless of the app's theme prop. An earlier version derived these from theme and produced grey-on-grey print output when the app was in dark mode (light text on dark muted background → both visible but unreadable on paper).

**Layout:**

- A4 portrait, 18mm margins.
- Tall mermaid diagrams capped at `max-height: 230mm` so they fit one page.
- Wide mermaid diagrams (`.mermaid-wide`) get their own landscape page via `@page wide-landscape`.
- `page-break-inside: avoid` on code blocks, blockquotes, mermaid diagrams, and tables.

## Server-side PDF Export (Playwright)

Separate pipeline from "Print to PDF", same visual output. Where the print path opens a popup and asks the user to click "Save as PDF", the export path POSTs a fully-rendered HTML document to the backend, which converts it to a PDF blob via headless Chromium. One click, no print dialog.

**Why two pipelines:** the print popup works without any backend dep — it's the universal fallback. The export path is optional (Playwright + Chromium not always installed) but gives single-click downloads, multi-file batch export, and works in environments where popups are blocked.

**Files:**

- `sshler/pdf.py` — `PDF_RENDERER` singleton; one long-lived Chromium launched in FastAPI lifespan; per-request fresh `BrowserContext` + `Page`; module-level `asyncio.Lock` serializes page creation (single-user localhost tool, no parallelism benefit).
- `sshler/api/pdf.py` — `POST /api/v1/pdf/render`. Accepts `{html, filename}`. Returns 503 when renderer is unavailable. Filename is sanitized (path separators / quotes stripped, `.pdf` appended). HTML capped at 20 MB.
- `frontend/src/composables/usePrintableHtml.ts` — `toHtml({box, path, token, theme})` returns a complete `<!DOCTYPE html>...</html>` string. Internally: fetch preview → parse markdown → render mermaid → inline images → wrap in the same print stylesheet. Mirrors the print-window pipeline but produces a string instead of writing to a popup.
- `frontend/src/composables/usePdfExport.ts` — `exportOne(box, path)` and `exportMany(box, paths[])`. Top-of-page loading bar (`NLoadingBarProvider` mounted in `App.vue`), stage-aware spinner (`fetching` → `rendering content` → `generating PDF Ns` ticking elapsed seconds → `downloading`).

**Availability signal:** `/api/v1/bootstrap` returns `pdf_available: bool`. Frontend `useBootstrapStore().pdfAvailable` gates every UI entry point — modal button, right-click context menu entry, multi-select toolbar button. When `false`, hide entries entirely (don't show disabled placeholders).

**Optional install:** `pip install "sshler[pdf]"` adds Playwright; `playwright install chromium` downloads the browser; on Linux you may also need `playwright install-deps chromium` for libnspr4 etc. The base sshler install does not require any of this.

**When extending:** if you want PDF export from a new entry point (e.g., search results, recent files), call `usePdfExport().exportOne/exportMany`. Don't re-implement the HTML pipeline. If you need a new request shape (e.g., custom margins, landscape default), pass options through `usePrintableHtml().toHtml()` and let the backend continue to receive a single fully-rendered HTML string.

## Things that look like bugs but aren't

| Symptom | Cause | Don't "fix" |
|---|---|---|
| Mermaid SVG looks fine in modal but prints as raw text | Caller stringified DOM via innerHTML | Keep `importNode` — preserves namespaces |
| `<` in mermaid source rejected | Author wrote `&lt;` to dodge markdown | `decodeHtmlEntities` already handles it |
| Tall flowchart spans 3 pages | SVG had inline `width="800" height="3000"` | Already stripped at render time |
| Image broken in print but fine in modal | Image was an `http://` URL, not relative | Expected — see Images section |
| Image broken in both | Above backend preview byte limit | Expected — `image_too_large` |
| Print mode looks light when app is dark | Forced by print CSS | Intentional — dark-mode greys print as charcoal-on-charcoal |
| Inline-HTML banner prints as invisible white-on-white | Browser default `print-color-adjust: economy` strips background colours | The `* { print-color-adjust: exact }` rule handles this — keep it |
| Headers / table headers look grey-on-grey when printing from dark mode | Print CSS previously derived `fg`/`muted` from app theme | Print palette is now hard-coded to light values regardless of theme |

## Files

- `frontend/src/components/FilePreviewModal.vue` — render + print + image inlining + dispatch
- `frontend/src/views/FilesView.vue` — file-list context menu and `autoPrint` wiring
- `sshler/api/files.py` — `api_file_preview` returns `image_data` / `image_mime` / `image_too_large`
- `sshler/api/helpers.py` — `_read_file_bytes` enforces the size limit
- `sshler/api/models.py` — `FilePreview` shape

## After changing anything in this pipeline

Build the frontend and restart sshler. Whatever your project convention is — a `just` recipe, a manual `pnpm build && pkill / restart`, a dev-server reload — make sure the new bundle is what's being served before you test. Built dist is what production loads; the dev server is what HMR uses; they can diverge.

Test matrix to verify nothing regressed:

1. A markdown file with at least one relative image — renders inline AND prints with the image embedded.
2. A markdown file with a mermaid diagram containing HTML entities (`&lt;`, `&amp;`) — diagram renders without lexer errors.
3. A markdown file with a wide flowchart — prints on a landscape page.
4. A markdown file with a tall sequence diagram — fits one portrait page.
5. A markdown file with an inline-styled banner (`<div style="background:#xxx;color:#fff">`) — banner prints with its colours intact.
6. A non-markdown text/code file — Print button is visible in the modal AND printing produces a monospace source PDF with the file path in the header.
7. Right-click on a file → Print — the print dialog appears automatically once the modal loads.
8. Switch the app to dark mode and re-run cases 5 and 6 — print output is still light-palette, no muddy greys.

## Installing this skill

This skill is meant to be loadable by Claude Code. Copy the directory into your skills folder:

```bash
cp -r docs/skills/markdown-preview ~/.claude/skills/
```

Then restart Claude Code. The skill name is `sshler-markdown-preview` and it will be matched when you're working on the files listed above.
