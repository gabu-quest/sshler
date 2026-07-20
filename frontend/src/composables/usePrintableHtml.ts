/**
 * Build a complete, self-contained HTML document representing the printable
 * version of a file (markdown rendered with mermaid SVGs + inlined images,
 * or a styled <pre> for plain-text source). The same document is used by:
 *   - the modal's Print button (fed to a popup window)
 *   - the PDF export path (POSTed to /api/v1/pdf/render)
 * keeping the two pipelines visually identical.
 *
 * Mermaid blocks are rendered to inline SVG here (in the browser), and remote
 * markdown images are fetched via the preview API and embedded as data: URLs.
 * Both happen *before* the HTML string is finalized, so the consumer (popup
 * or Playwright) sees a fully-resolved document with no network dependencies.
 */
import { marked } from "marked";
import DOMPurify from "dompurify";

import { fetchFilePreview } from "@/api/http";

export type PrintableTheme = "light" | "dark";

export interface PrintableInput {
  box: string;
  path: string;
  token: string | null;
  /** Mermaid render theme — affects diagram colors only. The page chrome
   *  is always rendered in light-on-white for legibility on paper / PDF. */
  theme?: PrintableTheme;
}

/** Build the print stylesheet (light theme, A4-tuned). Kept inline so the
 *  output HTML is fully self-contained. */
function buildPrintStyles(): string {
  const fg = "#1a1a1a";
  const bg = "#ffffff";
  const accent = "#1864ab";
  const muted = "#f3f3f3";
  const stroke = "#ddd";
  return `
    @page { margin: 18mm; size: A4; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    html, body {
      margin: 0; padding: 0; background: ${bg}; color: ${fg};
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 12pt; line-height: 1.55;
    }
    h1, h2, h3, h4 { margin: 1.2em 0 0.4em; color: ${fg}; page-break-after: avoid; }
    h1 { font-size: 22pt; border-bottom: 1pt solid ${stroke}; padding-bottom: 0.2em; }
    h2 { font-size: 17pt; border-bottom: 1pt solid ${stroke}; padding-bottom: 0.15em; }
    h3 { font-size: 14pt; }
    p { margin: 0.6em 0; }
    ul, ol { margin: 0.4em 0; padding-left: 1.6em; }
    li { margin: 0.2em 0; }
    code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 0.88em; padding: 0.15em 0.35em; background: ${muted}; border-radius: 3pt; }
    pre { margin: 1em 0; padding: 12pt; background: ${muted}; border-radius: 4pt; overflow: visible; white-space: pre-wrap; word-wrap: break-word; page-break-inside: avoid; }
    pre code { padding: 0; background: transparent; font-size: 0.85em; }
    blockquote { margin: 1em 0; padding: 0.4em 1em; border-left: 3pt solid ${accent}; background: ${muted}; }
    table { border-collapse: collapse; margin: 1em 0; width: 100%; }
    th, td { border: 1pt solid ${stroke}; padding: 6pt 10pt; text-align: left; }
    th { background: ${muted}; }
    img, svg { max-width: 100%; height: auto; page-break-inside: avoid; }
    .mermaid-rendered { margin: 1em 0; padding: 12pt; background: ${muted}; border: 1pt solid ${stroke}; border-radius: 4pt; text-align: center; page-break-inside: avoid; }
    .mermaid-rendered svg { width: auto !important; height: auto !important; max-width: 100% !important; max-height: 230mm !important; }
    @media print { .mermaid-wide { page: wide-landscape; } }
    @page wide-landscape { size: A4 landscape; margin: 14mm; }
    a { color: ${accent}; }
    .source-print { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 9.5pt; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
    .source-print .file-header { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 9pt; color: #666; border-bottom: 1pt solid ${stroke}; padding-bottom: 6pt; margin-bottom: 12pt; }
  `;
}

function isMarkdownPath(p: string): boolean {
  const lower = p.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown") || lower.endsWith(".mdx");
}

/** Resolve a markdown-relative image path against the document's directory.
 *  Returns null for URLs/data/anchors that don't need inlining. */
function resolveImagePath(src: string, parentDir: string): string | null {
  if (!src) return null;
  if (/^(data:|https?:|\/\/|blob:|mailto:|#)/i.test(src)) return null;
  if (src.startsWith("/")) return src.split("#")[0].split("?")[0];
  const base = parentDir.endsWith("/") ? parentDir : parentDir + "/";
  const parts = base.split("/").filter(Boolean);
  for (const seg of src.split("/")) {
    if (seg === "." || seg === "") continue;
    if (seg === "..") { parts.pop(); continue; }
    parts.push(seg);
  }
  return ("/" + parts.join("/")).split("#")[0].split("?")[0];
}

/** Lazy mermaid singleton. */
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
async function getMermaid(theme: PrintableTheme) {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((mod) => {
      const m = mod.default;
      m.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: theme === "dark" ? "dark" : "default",
      });
      return m;
    });
  }
  return mermaidPromise;
}

/** Decode HTML entities via a textarea — needed because mermaid's lexer
 *  doesn't decode them itself. */
const decodeHtmlEntities = (() => {
  let ta: HTMLTextAreaElement | null = null;
  return (input: string): string => {
    if (!input || !/&[a-zA-Z#0-9]+;/.test(input)) return input;
    if (!ta) ta = document.createElement("textarea");
    ta.innerHTML = input;
    return ta.value;
  };
})();

/** Render mermaid code blocks inside `container` to inline SVGs in place. */
async function renderMermaidBlocks(container: HTMLElement, theme: PrintableTheme) {
  const blocks = Array.from(
    container.querySelectorAll<HTMLElement>("code.language-mermaid, code.lang-mermaid"),
  );
  if (blocks.length === 0) return;
  const mermaid = await getMermaid(theme);
  for (let i = 0; i < blocks.length; i++) {
    const codeEl = blocks[i];
    const pre = codeEl.parentElement;
    const host = pre?.tagName === "PRE" ? pre : codeEl;
    let source = decodeHtmlEntities(codeEl.textContent ?? "");
    // Translate literal `\n` (backslash + n) to <br/> for diagram types that
    // don't natively interpret it.
    source = source.replace(/\\n/g, "<br/>");
    const id = `mermaid-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 7)}`;
    try {
      const { svg } = await mermaid.render(id, source);
      const wrapper = document.createElement("div");
      wrapper.className = "mermaid-rendered";
      wrapper.innerHTML = svg;
      const svgEl = wrapper.querySelector("svg");
      if (svgEl) {
        const viewBox = svgEl.getAttribute("viewBox");
        if (viewBox) {
          const parts = viewBox.split(/\s+/).map(Number);
          if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
            const aspect = parts[2] / parts[3];
            if (aspect > 1.4) wrapper.classList.add("mermaid-wide");
          }
        }
        svgEl.removeAttribute("width");
        svgEl.removeAttribute("height");
        svgEl.style.maxWidth = "100%";
        svgEl.style.height = "auto";
      }
      host.replaceWith(wrapper);
    } catch (err) {
      const errorBox = document.createElement("pre");
      errorBox.className = "mermaid-error";
      errorBox.textContent = `Mermaid render error: ${
        err instanceof Error ? err.message : String(err)
      }\n\n${source}`;
      host.replaceWith(errorBox);
    }
  }
}

/** Walk markdown <img> nodes, fetch each via the preview API, rewrite src to
 *  a data: URL. Same logic as in FilePreviewModal but stateless here. */
async function inlineImages(
  container: HTMLElement,
  box: string,
  parentDir: string,
  token: string | null,
) {
  const imgs = Array.from(container.querySelectorAll<HTMLImageElement>("img"));
  if (imgs.length === 0) return;

  const work: Array<{ img: HTMLImageElement; resolved: string }> = [];
  for (const img of imgs) {
    const raw = img.getAttribute("src") || "";
    if (raw.startsWith("data:")) continue;
    const resolved = resolveImagePath(raw, parentDir);
    if (!resolved) continue;
    work.push({ img, resolved });
  }
  if (work.length === 0) return;

  const cache = new Map<string, string>();
  const uniquePaths = Array.from(new Set(work.map((w) => w.resolved)));
  await Promise.all(
    uniquePaths.map(async (p) => {
      try {
        const payload = await fetchFilePreview(box, p, token);
        if (payload.image_data && payload.image_mime && !payload.image_too_large) {
          cache.set(p, `data:${payload.image_mime};base64,${payload.image_data}`);
        } else {
          cache.set(p, "");
        }
      } catch {
        cache.set(p, "");
      }
    }),
  );
  for (const { img, resolved } of work) {
    const dataUrl = cache.get(resolved);
    if (dataUrl) img.setAttribute("src", dataUrl);
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string
  ));
}

function wrapDocument(title: string, bodyHtml: string): string {
  const safeTitle = escapeHtml(title);
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${safeTitle}</title>
<style>${buildPrintStyles()}</style>
</head>
<body>${bodyHtml}</body>
</html>`;
}

/**
 * Build a complete, fully-resolved printable HTML document for the given file.
 * - Markdown: parses → sanitizes → renders mermaid SVGs → inlines images
 * - Other text: wraps in a styled <pre> with a header showing the path
 * - Images: not supported (caller should skip for image files)
 *
 * Throws if the file can't be fetched or has no usable text content.
 */
export function usePrintableHtml() {
  async function toHtml(input: PrintableInput): Promise<string> {
    const { box, path, token, theme = "light" } = input;
    const payload = await fetchFilePreview(box, path, token);
    const title = path.split("/").pop() || "preview";

    if (payload.image_data) {
      throw new Error("Cannot generate PDF for image files");
    }

    const content = payload.content ?? "";

    if (isMarkdownPath(path)) {
      // Parse markdown, sanitize, mount into a detached container so mermaid
      // and image inlining can manipulate the DOM, then serialize back out.
      const rawHtml = DOMPurify.sanitize(marked.parse(content) as string);
      const container = document.createElement("div");
      container.innerHTML = rawHtml;
      const parentDir = payload.parent || path.split("/").slice(0, -1).join("/") || "/";
      // Order matters: render mermaid first (replaces code blocks with SVG
      // <foreignObject> structures); then inline images so any <img> inside
      // mermaid-rendered HTML labels or markdown body get data: URLs.
      await renderMermaidBlocks(container, theme);
      await inlineImages(container, box, parentDir, token);
      return wrapDocument(title, container.innerHTML);
    }

    if (!content) {
      throw new Error("No text content available for this file");
    }
    // Non-markdown text: source view with header
    const body = `
      <div class="source-print">
        <div class="file-header">${escapeHtml(path)}</div>
        <pre><code>${escapeHtml(content)}</code></pre>
      </div>
    `;
    return wrapDocument(title, body);
  }

  return { toHtml };
}
