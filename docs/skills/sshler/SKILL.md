---
name: sshler
description: Reference for sshler — terminal file path linking, markdown preview, and the print-to-PDF pipeline. Use when working on Terminal.vue file links, FilePreviewModal rendering, or debugging why a clicked path or a printed PDF looks wrong.
---

# sshler — Terminal File Links & Markdown Preview

## Terminal File Path Linking

Paths that appear in terminal output are automatically detected as clickable links that navigate to the sshler Files view.

### Detection (Terminal.vue `provideLinks`)

Two regex passes per terminal line (via `terminal.buffer.active.getLine(n).translateToString()`):

1. **`FILE_URL_RE`** — `file://wsl.localhost/...` URLs. Clicking copies to clipboard (browsers block `window.open('file://...')` from an http:// origin).

2. **`PATH_RE`** — Four forms of path:
   - Home-relative: `~/some/path/file.txt` (starts with `~/`; backend expands via `Path.expanduser()`)
   - Absolute: `/some/path/file.txt` (starts with `/`)
   - Explicit relative: `./foo/bar.py` or `../parent/baz.rs`
   - Implicit relative: `word/path.ext` (requires at least one `/` and a dot-extension)

   Skipped patterns: `/dev/`, `/proc/`, `/sys/`, anything preceded by a protocol like `https://`.

### Click Flow

1. `activate` callback opens `about:blank` synchronously (within the user gesture — avoids popup blocker).
2. `handleFilePathClick` resolves the path and calls `GET /api/v1/boxes/{name}/stat?path=...`.
3. If `exists: false` — closes the blank tab and shows a warning toast.
4. If `is_directory: true` — navigates to `/app/files?box=...&path=<dir>`.
5. If `is_file: true` — navigates to `/app/files?box=...&path=<parent>&preview=<filename>`.

**Why `about:blank` first?** `window.open` called after an `await` loses the user gesture context and gets blocked by popup blockers. Opening the blank tab synchronously inside the `activate` callback keeps it within the gesture; after the async stat call the tab's `location.href` is redirected.

### Relative Path Resolution

Relative paths are resolved against `props.directory` — the **initial** directory when the terminal was spawned, NOT the current working directory after `cd`. If the user has navigated away, relative-path links may fail with "Path not found". Absolute paths always work.

### Stat Endpoint (Backend)

`GET /api/v1/boxes/{name}/stat?path=` — no directory restriction, any absolute path is valid. Local boxes use `Path.exists()` / `Path.is_dir()`. Remote boxes use SFTP stat. Returns `{exists, is_directory, is_file}`.

### Known Gaps

- Paths with spaces are not detected (the regex stops at whitespace).
- Paths with special chars like `+`, `@`, `(` are partially or not detected.
- Git diff headers (`a/src/foo.py`) match as implicit relative paths — they get highlighted but usually fail stat because the `a/` prefix isn't a real path.

---

## Markdown Preview & Print Pipeline

The markdown render path in `frontend/src/components/FilePreviewModal.vue`.

### Pipeline

```
file content (string)
  → marked.parse()         → raw HTML
  → DOMPurify.sanitize()   → safe HTML (v-html into .markdown-rendered)
  → renderMermaidBlocks()  → replaces <code.language-mermaid> with inline SVG
  → inlineMarkdownImages() → rewrites <img src> from path → data: URL
```

Mermaid + image inlining run in parallel after the v-html commit. They touch disjoint DOM nodes (`code.language-mermaid` vs `img`) so they don't race.

### Images

- `![alt](./relative/path.png)` and `![alt](../sibling.jpg)` — resolved against the markdown file's directory.
- `![alt](/srv/docs/foo.png)` — absolute remote path, fetched as-is.
- `![alt](https://example.com/foo.png)` — left alone; may not load in the print window (no auth token in that context).
- Images above the backend preview byte limit (`image_too_large`) stay broken.

**Why data: URLs?** The print window is a new browser context without the auth token, so `/api/...` URLs would 403. Inlining as data URLs makes the print HTML self-contained.

### Mermaid

- Fenced blocks tagged `` ```mermaid `` are replaced with inline SVG.
- HTML entities (`&lt;` `&ge;` `&amp;`) in mermaid source are decoded before rendering.
- Literal `\n` in state-diagram descriptions is translated to `<br/>`.
- SVG width/height attrs are stripped so CSS scales via viewBox.
- Diagrams with aspect ratio > 1.4:1 get `.mermaid-wide` and a landscape print page.
- Render errors become a visible `<pre class="mermaid-error">`.

### Mermaid — colour conventions (authoring)

sshler's preview/print palette is **forced light** (dark-mode greys print as muddy charcoal), so author
mermaid for a light background and **style every node explicitly** — never rely on the default theme
(its default fills/edges are grey-ish and its label colour can render as low-contrast grey).

Hard rules (apply to every diagram):

- **Never grey text.** Set `color:` on every node/class. **White text (`color:#ffffff`) on dark fills,
  black text (`color:#000000`) on light fills.** Never leave label colour to the theme default.
- **Colour by meaning, not decoration** — e.g. start/event, "shared / runs once", "the one thing being
  highlighted", warning. Add a one-line **colour key** under the diagram when you use more than two.
- **Style the subgraph too** — the default subgraph background is grey-ish. Use a near-white tint
  (`fill:#eff6ff,color:#000000,stroke:#1d4ed8`) so the title isn't grey-on-grey.
- **Put the explainer words inside the nodes** ("one thread", "sequential", "runs once", "+1.28 ms") —
  the diagram should stand alone, not depend on surrounding prose.
- Prefer `flowchart TD` / `stateDiagram-v2`, keep it top-down and legible (wide diagrams trigger the
  landscape print path, which is fine but harder to scan).

A palette that prints cleanly (light mode, high contrast):

| Role | fill | text |
|---|---|---|
| start / event | `#1d4ed8` (dark blue) | `#ffffff` white |
| shared / neutral / "runs once" | `#bfdbfe` (light blue) | `#000000` black |
| category A (e.g. the live path) | `#bbf7d0` (light green) | `#000000` black |
| **the highlighted / new thing** | `#ea580c` (dark orange) | `#ffffff` white |
| secondary category | `#ede9fe` (light purple) | `#000000` black |
| warning / danger | `#dc2626` (red) | `#ffffff` white |
| subgraph background | `#eff6ff` (near-white blue) | `#000000` black |

Use `classDef` + `class` so the palette stays consistent across a doc, e.g.:

```
classDef event   fill:#1d4ed8,color:#ffffff,stroke:#172554,stroke-width:2px;
classDef shared  fill:#bfdbfe,color:#000000,stroke:#1d4ed8,stroke-width:1px;
classDef newwork fill:#ea580c,color:#ffffff,stroke:#7c2d12,stroke-width:3px;
class S event; class P,PUB shared; class B2 newwork;
style T fill:#eff6ff,color:#000000,stroke:#1d4ed8,stroke-width:1px;
```

### Print to PDF

Triggered by the printer icon in the preview modal OR the right-click context menu on any file.

- **Markdown print**: Opens a new window, clones live rendered DOM via `importNode` (NOT innerHTML — preserves SVG namespace).
- **Non-markdown print**: `printSource()` opens a print window with `<pre><code>` and a file-path header.
- Print palette is hard-coded light regardless of app theme (dark-mode greys print as muddy charcoal).
- A4 portrait, 18mm margins. Tall mermaid capped at 230mm; wide mermaid gets landscape pages.
- `print-color-adjust: exact !important` forces background colors to print.

### Things That Look Like Bugs But Aren't

| Symptom | Cause | Don't "fix" |
|---|---|---|
| Mermaid SVG fine in modal but prints as raw text | Caller stringified DOM via innerHTML | Keep `importNode` |
| `<` in mermaid source rejected | Author wrote `&lt;` to dodge markdown | `decodeHtmlEntities` already handles it |
| Image broken in print | `http://` URL, no auth token in print window | Expected |
| Print looks light in dark mode | Forced by print CSS | Intentional |
| Inline-HTML banner prints invisible | `print-color-adjust: exact` is already set | Keep it |
| Mermaid node text prints grey / muddy | Author left node `color:` to the theme default | Set `color:` per node — white on dark, black on light (see "colour conventions") |

### Files

- `frontend/src/components/FilePreviewModal.vue` — render + print + image inlining
- `frontend/src/components/Terminal.vue` — file path link provider + `handleFilePathClick`
- `sshler/api/files.py` — `api_file_preview` (preview), `api_stat_path` (stat)
- `sshler/api/helpers.py` — `_read_file_bytes` enforces size limit

### After Changing the Preview/Print Pipeline

Build + restart:

```bash
cd frontend && npx pnpm build
pkill -x sshler && nohup sshler serve >/dev/null 2>&1 & disown
```

Test matrix: markdown with (a) relative image, (b) mermaid with HTML entities, (c) wide flowchart, (d) tall sequence diagram. Render mode shows all four; print PDF has all four visible.
