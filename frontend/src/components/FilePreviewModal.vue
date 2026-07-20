<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { NButton, NIcon, NModal, NSpace, NSpin, NSwitch, useMessage } from "naive-ui";
import { PhArrowCounterClockwise, PhArrowsOut, PhCopy, PhDownloadSimple, PhEye, PhFile, PhFilePdf, PhMagnifyingGlassMinus, PhMagnifyingGlassPlus, PhPencil, PhPrinter, PhX } from "@phosphor-icons/vue";
import type { FilePreview } from "@/api/types";
import { fetchFilePreview, downloadFile } from "@/api/http";
import CodeEditor from "@/components/CodeEditor.vue";
import ExcelPreview from "@/components/ExcelPreview.vue";
import { useI18n } from "@/i18n";
import { useBootstrapStore } from "@/stores/bootstrap";
import { usePdfExport } from "@/composables/usePdfExport";
import { marked } from "marked";
import DOMPurify from "dompurify";

const props = defineProps<{
  show: boolean;
  path: string;
  box: string | null;
  token: string | null;
  theme: "light" | "dark";
  /** If true, the modal triggers print automatically once content is loaded.
   *  Used by the file list "Print" context-menu action so the user doesn't
   *  have to open the modal and click Print themselves. */
  autoPrint?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
  (e: "edit", path: string): void;
  (e: "compare", path: string): void;
}>();

const { t } = useI18n();
const message = useMessage();
const bootstrap = useBootstrapStore();
const { exportOne: exportOnePdf, busy: pdfBusy } = usePdfExport();

async function handleDownloadPdf() {
  if (!props.box || isImage.value) return;
  await exportOnePdf(props.box, props.path, props.theme);
}

const content = ref("");
const meta = ref<FilePreview | null>(null);
const loading = ref(false);
const markdownRenderMode = ref(false);
const showLineNumbers = ref(true);
const wordWrap = ref(true);
const readerMode = ref(false);

const isImage = computed(() => !!(meta.value?.image_data && meta.value?.image_mime && !meta.value?.image_too_large));

const isExcelFile = computed(() => {
  const name = meta.value?.name?.toLowerCase() || props.path.split("/").pop()?.toLowerCase() || "";
  return name.endsWith(".xlsx") || name.endsWith(".xls") || name.endsWith(".ods");
});

// ── Image zoom / pan ──────────────────────────────────────────────────────
// Scroll wheel zooms toward the cursor; drag pans when zoomed; double-click
// toggles between fit and 2×. State is a CSS transform on the <img>, reset
// whenever the modal opens or the previewed file changes.
const imageViewportRef = ref<HTMLDivElement | null>(null);
const imgZoom = ref(1);
const imgPanX = ref(0);
const imgPanY = ref(0);
const imgDragging = ref(false);
let dragStartX = 0;
let dragStartY = 0;
let panStartX = 0;
let panStartY = 0;
const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

const imageTransformStyle = computed(() => ({
  transform: `translate(${imgPanX.value}px, ${imgPanY.value}px) scale(${imgZoom.value})`,
}));

function resetImageZoom() {
  imgZoom.value = 1;
  imgPanX.value = 0;
  imgPanY.value = 0;
  imgDragging.value = false;
}

/** Set zoom, keeping the point under (clientX, clientY) pinned in place. When no
 *  origin is given (toolbar buttons) it zooms about the viewport center. */
function applyZoom(targetZoom: number, clientX?: number, clientY?: number) {
  const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, targetZoom));
  const vp = imageViewportRef.value;
  if (vp && clientX !== undefined && clientY !== undefined && z !== imgZoom.value) {
    const rect = vp.getBoundingClientRect();
    const cx = clientX - rect.left - rect.width / 2;
    const cy = clientY - rect.top - rect.height / 2;
    // Point in the image's local (pre-transform) space currently under the cursor.
    const localX = (cx - imgPanX.value) / imgZoom.value;
    const localY = (cy - imgPanY.value) / imgZoom.value;
    imgPanX.value = cx - localX * z;
    imgPanY.value = cy - localY * z;
  }
  imgZoom.value = z;
  if (z === 1) {
    imgPanX.value = 0;
    imgPanY.value = 0;
  }
}

function onImageWheel(e: WheelEvent) {
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  applyZoom(imgZoom.value * factor, e.clientX, e.clientY);
}

function onImagePointerDown(e: PointerEvent) {
  if (imgZoom.value <= 1) return;
  imgDragging.value = true;
  dragStartX = e.clientX;
  dragStartY = e.clientY;
  panStartX = imgPanX.value;
  panStartY = imgPanY.value;
  (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
}

function onImagePointerMove(e: PointerEvent) {
  if (!imgDragging.value) return;
  imgPanX.value = panStartX + (e.clientX - dragStartX);
  imgPanY.value = panStartY + (e.clientY - dragStartY);
}

function onImagePointerUp(e: PointerEvent) {
  if (!imgDragging.value) return;
  imgDragging.value = false;
  (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
}

function onImageDblClick(e: MouseEvent) {
  if (imgZoom.value > 1) resetImageZoom();
  else applyZoom(2, e.clientX, e.clientY);
}

const isMarkdownFile = computed(() => {
  const name = meta.value?.name?.toLowerCase() || props.path.split("/").pop()?.toLowerCase() || "";
  return name.endsWith(".md") || name.endsWith(".markdown") || name.endsWith(".mdx");
});

const renderedMarkdown = computed(() => {
  if (!markdownRenderMode.value || !content.value) return "";
  return DOMPurify.sanitize(marked.parse(content.value) as string);
});

const markdownContainerRef = ref<HTMLDivElement | null>(null);

// Lazy-loaded mermaid singleton — keeps initial bundle slim
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
const getMermaid = async () => {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((mod) => {
      const m = mod.default;
      m.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: props.theme === "dark" ? "dark" : "default",
      });
      return m;
    });
  }
  return mermaidPromise;
};

let mermaidRenderToken = 0;
let imageInlineToken = 0;

/**
 * Resolve a markdown-relative image path against the previewed file's directory.
 * Returns an absolute remote path, or null for URLs/data/anchors we should not touch.
 */
function resolveImagePath(src: string): string | null {
  if (!src) return null;
  if (/^(data:|https?:|\/\/|blob:|mailto:|#)/i.test(src)) return null;
  const parentDir = meta.value?.parent || props.path.split('/').slice(0, -1).join('/') || '/';
  if (src.startsWith('/')) return src.split('#')[0].split('?')[0];
  const base = parentDir.endsWith('/') ? parentDir : parentDir + '/';
  const parts = base.split('/').filter(Boolean);
  for (const seg of src.split('/')) {
    if (seg === '.' || seg === '') continue;
    if (seg === '..') { parts.pop(); continue; }
    parts.push(seg);
  }
  return ('/' + parts.join('/')).split('#')[0].split('?')[0];
}

/**
 * Walk the rendered markdown DOM, find <img> tags whose src is a remote-relative
 * path, fetch them via the preview API, and rewrite src to a data: URL. This
 * makes images render in the modal AND survive being cloned into the print window
 * (the print context has no auth token, so direct API URLs would 403).
 */
const inlinedImageCache = new Map<string, string>();
async function inlineMarkdownImages() {
  const root = markdownContainerRef.value;
  if (!root || !props.box) return;
  const imgs = Array.from(root.querySelectorAll<HTMLImageElement>('img'));
  if (imgs.length === 0) return;

  const token = ++imageInlineToken;

  // Group by resolved path so multiple references to the same image share one fetch
  const work: Array<{ img: HTMLImageElement; resolved: string }> = [];
  for (const img of imgs) {
    const raw = img.getAttribute('src') || '';
    if (raw.startsWith('data:')) continue;
    const resolved = resolveImagePath(raw);
    if (!resolved) continue;
    work.push({ img, resolved });
  }
  if (work.length === 0) return;

  const uniquePaths = Array.from(new Set(work.map((w) => w.resolved)));
  await Promise.all(
    uniquePaths.map(async (p) => {
      if (inlinedImageCache.has(p)) return;
      try {
        const payload = await fetchFilePreview(props.box as string, p, props.token);
        if (payload.image_data && payload.image_mime && !payload.image_too_large) {
          inlinedImageCache.set(p, `data:${payload.image_mime};base64,${payload.image_data}`);
        } else {
          inlinedImageCache.set(p, ''); // sentinel: known-bad, don't retry
        }
      } catch {
        inlinedImageCache.set(p, '');
      }
    }),
  );

  if (token !== imageInlineToken) return;

  for (const { img, resolved } of work) {
    const dataUrl = inlinedImageCache.get(resolved);
    if (dataUrl) img.setAttribute('src', dataUrl);
  }
}

/**
 * Decode HTML entities (&lt; &gt; &amp; &ge; &le; etc.) using the browser's parser.
 * Mermaid does not decode entities itself — if the markdown source contains `&lt;`
 * (often written to dodge markdown's `<` handling), mermaid's lexer sees four
 * literal characters and rejects them. textContent gives us a single decode
 * (so `&amp;lt;` in HTML becomes `&lt;` in the string); this second pass turns
 * `&lt;` into `<`, etc.
 */
const decodeHtmlEntities = (() => {
  let ta: HTMLTextAreaElement | null = null;
  return (input: string): string => {
    if (!input || !/&[a-zA-Z#0-9]+;/.test(input)) return input;
    if (!ta) ta = document.createElement("textarea");
    ta.innerHTML = input;
    return ta.value;
  };
})();

const renderMermaidBlocks = async () => {
  const root = markdownContainerRef.value;
  if (!root) return;
  const blocks = Array.from(
    root.querySelectorAll<HTMLElement>("code.language-mermaid, code.lang-mermaid"),
  );
  if (blocks.length === 0) return;

  const token = ++mermaidRenderToken;
  const mermaid = await getMermaid();
  // Bail if the modal closed / content changed while we were loading mermaid
  if (token !== mermaidRenderToken) return;

  for (let i = 0; i < blocks.length; i++) {
    const codeEl = blocks[i];
    const pre = codeEl.parentElement;
    const host = pre?.tagName === "PRE" ? pre : codeEl;
    // textContent already does one decode pass; decodeHtmlEntities catches the
    // case where the source itself contained encoded entities (e.g. authors who
    // wrote `&lt;` to escape markdown's `<` handling — mermaid's lexer would
    // otherwise reject them as unknown text).
    let source = decodeHtmlEntities(codeEl.textContent ?? "");
    // Translate literal `\n` (backslash + n) to <br/> for diagram types that
    // don't natively interpret it (stateDiagram-v2 state descriptions, classDef
    // attribute strings). Mermaid renders <br/> as a line break wherever
    // htmlLabels is on, which is the default. This does NOT touch actual
    // newlines in the source — only the two-character sequence `\n` that
    // diagram authors commonly write expecting a line break.
    source = source.replace(/\\n/g, "<br/>");
    const id = `mermaid-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 7)}`;
    try {
      const { svg } = await mermaid.render(id, source);
      if (token !== mermaidRenderToken) return;
      const wrapper = document.createElement("div");
      wrapper.className = "mermaid-rendered";
      // mermaid.render returns SVG; DOMPurify with SVG profile preserves it.
      // Trust mermaid's own DOMPurify pass (enabled by securityLevel: "strict" in
      // initialize() above) rather than re-sanitizing the SVG. A second pass with
      // our config strips the HTML labels inside <foreignObject> because the inner
      // <div>/<span> live in the XHTML namespace which our SVG profile rejects —
      // producing empty shapes. Mermaid already sanitized the labels with DOMPurify
      // before emitting, and the source we hand mermaid comes from local markdown
      // files (trust boundary is the file, not the SVG output).
      wrapper.innerHTML = svg;
      // Strip mermaid's fixed pixel width/height from the SVG element so CSS
      // (max-width: 100%; height: auto) can scale it to the container width
      // and the viewBox handles aspect ratio. Without this, tall diagrams keep
      // their literal pixel height when printed and overflow onto extra pages.
      const svgEl = wrapper.querySelector("svg");
      if (svgEl) {
        // Capture intrinsic aspect ratio from the viewBox BEFORE stripping size
        // attrs, so we can flag wide diagrams for landscape printing.
        const viewBox = svgEl.getAttribute("viewBox");
        if (viewBox) {
          const parts = viewBox.split(/\s+/).map(Number);
          if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
            const aspect = parts[2] / parts[3];
            // Wider than ~1.4:1 prints better in landscape than portrait at
            // any reasonable scale. The print stylesheet uses @page wide-landscape.
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
};

watch(
  [renderedMarkdown, markdownRenderMode],
  async () => {
    if (!markdownRenderMode.value) return;
    await nextTick();
    // Fire both in parallel — they touch disjoint DOM nodes (img vs code.language-mermaid)
    renderMermaidBlocks();
    inlineMarkdownImages();
  },
  { flush: "post" },
);

/** Build the shared light-theme print stylesheet. Used by both the rendered-markdown
 *  print path and the source-code print path so they stay visually consistent. */
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

/** Open a blank popup with our print scaffolding. Caller appends body content. */
function openPrintWindow(title: string): Window | null {
  const safeTitle = title.replace(/[<>&"']/g, "");
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${safeTitle}</title>
<style>${buildPrintStyles()}</style>
</head>
<body></body>
</html>`;
  const win = window.open("", "_blank", "width=900,height=1100");
  if (!win) {
    message.error(t("files.print_popup_blocked"));
    return null;
  }
  win.document.open();
  win.document.write(html);
  win.document.close();
  return win;
}

/** Print the raw source content as a `<pre>` block. Used for non-markdown text files. */
function printSource() {
  if (!content.value) {
    message.warning(t("files.print_needs_render"));
    return;
  }
  const title = props.path.split("/").pop() || "preview";
  const win = openPrintWindow(title);
  if (!win) return;
  const wrapper = win.document.createElement("div");
  wrapper.className = "source-print";
  const header = win.document.createElement("div");
  header.className = "file-header";
  header.textContent = props.path;
  wrapper.appendChild(header);
  const pre = win.document.createElement("pre");
  const code = win.document.createElement("code");
  code.textContent = content.value;
  pre.appendChild(code);
  wrapper.appendChild(pre);
  win.document.body.appendChild(wrapper);
  win.addEventListener("load", () => {
    setTimeout(() => { win.focus(); win.print(); }, 150);
  });
}

/** Unified print dispatcher. Markdown → rendered-with-diagrams path. Everything
 *  else with text content → source print path. */
async function doPrint() {
  if (isMarkdownFile.value) {
    await printRendered();
  } else if (content.value) {
    printSource();
  } else {
    message.warning(t("files.print_needs_render"));
  }
}

/**
 * Open the rendered markdown in a new window and trigger the browser's print dialog.
 * User chooses "Save as PDF" in the dialog. SVG diagrams print as vectors — stay crisp.
 * Auto-enables render mode if it isn't already on, so the button works from source view.
 */
async function printRendered() {
  if (!markdownRenderMode.value) {
    markdownRenderMode.value = true;
    // Wait for Vue DOM update + watcher (flush:post) + mermaid async renders
    await nextTick();
    await nextTick();
    await new Promise<void>(resolve => setTimeout(resolve, 400));
  }
  // Ensure all referenced images are inlined as data: URLs before we clone the
  // DOM into the print window. The print window has no API token, so anything
  // not already inlined would fail to load there.
  await inlineMarkdownImages();
  const source = markdownContainerRef.value;
  if (!source) {
    message.warning(t("files.print_needs_render"));
    return;
  }
  const title = props.path.split("/").pop() || "preview";
  // Use importNode (NOT innerHTML stringify) — preserves SVG / XHTML namespaces
  // that mermaid uses for <foreignObject> labels in complex diagrams.
  const win = openPrintWindow(title);
  if (!win) return;
  for (const child of Array.from(source.childNodes)) {
    win.document.body.appendChild(win.document.importNode(child, true));
  }
  // Wait for fonts + SVG layout before printing
  win.addEventListener("load", () => {
    // Small delay so mermaid SVGs have laid out
    setTimeout(() => {
      win.focus();
      win.print();
    }, 250);
  });
}

const getLanguageFromFilename = (filename: string) => {
  const ext = filename.split(".").pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    js: "javascript", jsx: "javascript", ts: "javascript", tsx: "javascript",
    py: "python", html: "html", htm: "html", css: "css", scss: "css", sass: "css",
    json: "json", md: "markdown", xml: "xml", svg: "xml",
  };
  return langMap[ext || ""] || "text";
};

watch(() => props.show, async (showing) => {
  if (!showing) { readerMode.value = false; return; }
  if (!props.box || !props.path) return;
  content.value = "";
  meta.value = null;
  inlinedImageCache.clear();
  resetImageZoom();
  loading.value = true;
  try {
    const payload = await fetchFilePreview(props.box, props.path, props.token);
    meta.value = payload;
    content.value = payload.content || "";
    markdownRenderMode.value = !!payload.is_markdown;
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
    emit("update:show", false);
  } finally {
    loading.value = false;
  }
  // If invoked via the file-list Print action, fire the print dialog automatically
  // once content has loaded. Skips images.
  //
  // NOTE: NModal teleports its body, and `markdownContainerRef` is only populated
  // after (a) Vue commits the v-html and (b) the post-flush watcher renders mermaid
  // blocks + inlines images. A single `nextTick()` is not enough — printRendered()
  // would see a null container and silently warn. We wait for two ticks + a small
  // buffer so the rendered markdown DOM exists before we try to clone it.
  if (props.autoPrint && !isImage.value) {
    await nextTick();
    await nextTick();
    await new Promise<void>((r) => setTimeout(r, 250));
    await doPrint();
  }
});

async function copyToClipboard(text: string, label: string) {
  if (!text) {
    message.warning(t("files.print_needs_render"));
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    message.success(`${label} copied`);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

async function handleCopySource() {
  await copyToClipboard(content.value, "Source");
}

async function handleCopyRendered() {
  const root = markdownContainerRef.value;
  if (!root) {
    message.warning(t("files.print_needs_render"));
    return;
  }
  // Build a light-themed HTML payload so OneNote / Word / Notion paste with
  // readable colors (the live modal uses dark-mode CSS variables — pasting
  // those produces grey-on-white). We inline the same print stylesheet that
  // already targets light backgrounds, then clone the rendered DOM (images +
  // mermaid SVGs already inlined) inside a body wrapper.
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${buildPrintStyles()}</style></head><body>${root.innerHTML}</body></html>`;
  const plain = root.innerText;
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ]);
      message.success("Rendered copied (with formatting)");
    } else {
      await navigator.clipboard.writeText(plain);
      message.success("Rendered copied (plain text fallback)");
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

async function handleDownload() {
  if (!props.box) return;
  try {
    const blob = await downloadFile(props.box, props.path, props.token);
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = props.path.split("/").pop() || "download";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

function close() {
  emit("update:show", false);
  content.value = "";
  meta.value = null;
  loading.value = false;
  markdownRenderMode.value = false;
  resetImageZoom();
}

function updateContent(newContent: string) {
  content.value = newContent;
}

/** Intercept clicks on links in rendered markdown */
function handleMarkdownClick(event: MouseEvent) {
  const target = (event.target as HTMLElement)?.closest('a') as HTMLAnchorElement | null;
  if (!target) return;

  const href = target.getAttribute('href');
  if (!href) return;

  // Let external URLs open normally
  if (/^https?:\/\//i.test(href) || href.startsWith('mailto:')) return;

  // It's a relative or absolute file path — resolve it
  event.preventDefault();
  event.stopPropagation();

  const parentDir = meta.value?.parent || props.path.split('/').slice(0, -1).join('/') || '/';

  let resolved: string;
  if (href.startsWith('/')) {
    resolved = href;
  } else {
    // Resolve relative path against parent directory
    const base = parentDir.endsWith('/') ? parentDir : parentDir + '/';
    const parts = base.split('/').filter(Boolean);
    for (const seg of href.split('/')) {
      if (seg === '.' || seg === '') continue;
      if (seg === '..') { parts.pop(); continue; }
      parts.push(seg);
    }
    resolved = '/' + parts.join('/');
  }

  // Strip fragment/anchor from path
  const cleanPath = resolved.split('#')[0];

  if (!props.box) return;

  // Open in Files view in a new tab
  const dir = cleanPath.split('/').slice(0, -1).join('/') || '/';
  const filename = cleanPath.split('/').pop() || '';
  const params = new URLSearchParams({ box: props.box, path: dir });
  if (filename) params.set('preview', filename);
  window.open(`/app/files?${params.toString()}`, '_blank');
}

defineExpose({ updateContent });
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :class="{ 'preview-reader-modal': readerMode }"
    :style="readerMode ? 'width:100vw;height:100dvh;max-width:100vw;max-height:100dvh;border-radius:0;margin:0' : 'max-width:90vw;max-height:90vh'"
    @update:show="emit('update:show', $event)"
  >
    <template #header>
      <!-- Minimal reader-mode chrome -->
      <div v-if="readerMode" class="reader-header">
        <span class="reader-filename">{{ path.split('/').pop() }}</span>
        <NButton text size="tiny" title="Exit reader mode" @click="readerMode = false">
          <NIcon size="14"><PhX weight="duotone" /></NIcon>
        </NButton>
      </div>
      <!-- Normal header -->
      <div v-else class="modal-header">
        <NIcon size="16"><PhEye weight="duotone" /></NIcon>
        <span>Preview: {{ path.split('/').pop() }}</span>
        <div class="modal-actions">
          <NButton v-if="isMarkdownFile && markdownRenderMode" size="small" title="Reader mode" @click="readerMode = true">
            <NIcon size="14"><PhArrowsOut weight="duotone" /></NIcon>
          </NButton>
          <NButton v-if="isMarkdownFile && !isExcelFile" size="small" @click="markdownRenderMode = !markdownRenderMode">
            <NIcon size="14"><PhEye v-if="!markdownRenderMode" weight="duotone" /><PhFile v-else weight="duotone" /></NIcon>
            {{ markdownRenderMode ? 'Source' : 'Render' }}
          </NButton>
          <NButton
            v-if="!isImage && !isExcelFile"
            size="small"
            title="Copy source to clipboard"
            @click="handleCopySource"
          >
            <NIcon size="14"><PhCopy weight="duotone" /></NIcon>
            Copy
          </NButton>
          <NButton
            v-if="isMarkdownFile && markdownRenderMode"
            size="small"
            title="Copy rendered text to clipboard"
            @click="handleCopyRendered"
          >
            <NIcon size="14"><PhCopy weight="duotone" /></NIcon>
            Copy rendered
          </NButton>
          <NButton
            v-if="!isImage && !isExcelFile"
            size="small"
            :title="t('files.print_pdf')"
            @click="doPrint"
          >
            <NIcon size="14"><PhPrinter weight="duotone" /></NIcon>
            {{ t('files.print') }}
          </NButton>
          <NButton
            v-if="!isImage && !isExcelFile && bootstrap.pdfAvailable"
            size="small"
            :loading="pdfBusy"
            :disabled="pdfBusy"
            :title="t('files.download_pdf')"
            @click="handleDownloadPdf"
          >
            <NIcon size="14"><PhFilePdf weight="duotone" /></NIcon>
            {{ t('files.pdf') }}
          </NButton>
          <NButton size="small" :title="t('common.edit')" @click="emit('edit', path)">
            <NIcon size="14"><PhPencil weight="duotone" /></NIcon>Edit
          </NButton>
          <NButton size="small" @click="emit('compare', path)">Compare</NButton>
          <NButton size="small" :title="t('common.download')" @click="handleDownload">
            <NIcon size="14"><PhDownloadSimple weight="duotone" /></NIcon>Download
          </NButton>
        </div>
      </div>
    </template>

    <div class="preview-container">
      <NSpin v-if="loading" size="large"><span class="text-muted">{{ t('files.loading_preview') }}</span></NSpin>
      <template v-else>
        <div v-if="isImage" class="preview-image-container">
          <div
            ref="imageViewportRef"
            class="image-viewport"
            :class="{ zoomed: imgZoom > 1, dragging: imgDragging }"
            @wheel.prevent="onImageWheel"
            @pointerdown="onImagePointerDown"
            @pointermove="onImagePointerMove"
            @pointerup="onImagePointerUp"
            @pointerleave="onImagePointerUp"
            @dblclick="onImageDblClick"
          >
            <img
              class="preview-image"
              :style="imageTransformStyle"
              :src="`data:${meta?.image_mime};base64,${meta?.image_data}`"
              :alt="path"
              draggable="false"
            />
          </div>
          <div class="image-zoom-bar">
            <NButton size="tiny" quaternary :disabled="imgZoom <= MIN_ZOOM" title="Zoom out" @click="applyZoom(imgZoom / 1.3)">
              <NIcon size="16"><PhMagnifyingGlassMinus weight="bold" /></NIcon>
            </NButton>
            <span class="zoom-pct text-muted small">{{ Math.round(imgZoom * 100) }}%</span>
            <NButton size="tiny" quaternary :disabled="imgZoom >= MAX_ZOOM" title="Zoom in" @click="applyZoom(imgZoom * 1.3)">
              <NIcon size="16"><PhMagnifyingGlassPlus weight="bold" /></NIcon>
            </NButton>
            <NButton size="tiny" quaternary :disabled="imgZoom === 1 && imgPanX === 0 && imgPanY === 0" title="Reset zoom" @click="resetImageZoom">
              <NIcon size="16"><PhArrowCounterClockwise weight="bold" /></NIcon>
            </NButton>
            <span class="zoom-hint text-muted small">scroll to zoom · drag to pan · double-click to reset</span>
          </div>
          <p v-if="meta?.image_too_large" class="text-muted small">Image truncated at {{ meta?.image_limit_kb }}KB</p>
        </div>
        <div v-else class="preview-text-container">
          <ExcelPreview v-if="isExcelFile" :box="box!" :path="path" :token="token" />
          <div v-else-if="markdownRenderMode && isMarkdownFile" ref="markdownContainerRef" :class="['markdown-rendered', { 'reader-mode': readerMode }]" v-html="renderedMarkdown" @click="handleMarkdownClick" />
          <CodeEditor v-else :model-value="content" :language="getLanguageFromFilename(path)" :theme="theme" :readonly="true" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 75vh" />
        </div>
      </template>
    </div>

    <template v-if="!readerMode" #footer>
      <div class="modal-footer">
        <NSpace size="small" :wrap="false">
          <NSwitch v-model:value="showLineNumbers" size="small">
            <template #checked>{{ t('files.lines') }}</template>
            <template #unchecked>{{ t('files.no_lines') }}</template>
          </NSwitch>
          <NSwitch v-model:value="wordWrap" size="small">
            <template #checked>{{ t('files.wrap') }}</template>
            <template #unchecked>{{ t('files.no_wrap') }}</template>
          </NSwitch>
        </NSpace>
        <NButton @click="close">{{ t('common.close') }}</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.modal-header > span {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.modal-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preview-container {
  min-height: 400px;
}

.preview-image-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.image-viewport {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 100%;
  max-height: 70vh;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid var(--stroke);
  background: var(--surface);
  cursor: zoom-in;
  touch-action: none;
}

.image-viewport.zoomed {
  cursor: grab;
}

.image-viewport.dragging {
  cursor: grabbing;
}

.preview-image {
  max-width: 100%;
  max-height: 70vh;
  transform-origin: center center;
  will-change: transform;
  user-select: none;
  -webkit-user-drag: none;
}

.image-zoom-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
}

.zoom-pct {
  min-width: 3.2em;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.zoom-hint {
  margin-left: 8px;
}

.preview-text-container {
  border-radius: 8px;
  overflow: hidden;
}

.markdown-rendered {
  height: 75vh;
  overflow-y: auto;
  padding: 24px 32px;
  font-size: 15px;
  line-height: 1.7;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
}

.markdown-rendered h1,
.markdown-rendered h2,
.markdown-rendered h3,
.markdown-rendered h4 {
  margin: 1.5em 0 0.5em;
  color: var(--text);
}

.markdown-rendered h1 { font-size: 1.8em; border-bottom: 1px solid var(--stroke); padding-bottom: 0.3em; }
.markdown-rendered h2 { font-size: 1.4em; border-bottom: 1px solid var(--stroke); padding-bottom: 0.2em; }
.markdown-rendered h3 { font-size: 1.2em; }

.markdown-rendered p { margin: 0.8em 0; }

.markdown-rendered ul,
.markdown-rendered ol {
  margin: 0.5em 0;
  padding-left: 2em;
}

.markdown-rendered li { margin: 0.3em 0; }

.markdown-rendered code {
  font-family: var(--font-mono);
  font-size: 0.9em;
  padding: 0.2em 0.4em;
  background: var(--surface-variant);
  border-radius: 4px;
}

.markdown-rendered pre {
  margin: 1em 0;
  padding: 16px;
  background: var(--surface-variant);
  border-radius: 8px;
  overflow-x: auto;
}

.markdown-rendered pre code {
  padding: 0;
  background: transparent;
}

.markdown-rendered :deep(.mermaid-rendered) {
  margin: 1em 0;
  padding: 16px;
  background: var(--surface-variant);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  overflow-x: auto;
  text-align: center;
}

.markdown-rendered :deep(.mermaid-rendered svg) {
  max-width: 100%;
  height: auto;
}

.markdown-rendered :deep(.mermaid-error) {
  color: #d33;
  background: var(--surface-variant);
  border: 1px solid #d33;
}

.markdown-rendered blockquote {
  margin: 1em 0;
  padding: 0.5em 1em;
  border-left: 4px solid var(--accent);
  background: var(--surface-variant);
  border-radius: 0 4px 4px 0;
}

.markdown-rendered table {
  border-collapse: collapse;
  margin: 1em 0;
  width: 100%;
}

.markdown-rendered th,
.markdown-rendered td {
  border: 1px solid var(--stroke);
  padding: 8px 12px;
  text-align: left;
}

.markdown-rendered th {
  background: var(--surface-variant);
  font-weight: 600;
}

.markdown-rendered a {
  color: var(--accent);
  text-decoration: underline;
  cursor: pointer;
}

.markdown-rendered img {
  max-width: 100%;
  border-radius: 8px;
}

.markdown-rendered hr {
  border: none;
  border-top: 1px solid var(--stroke);
  margin: 1.5em 0;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.text-muted {
  color: var(--muted);
}

.small {
  font-size: 12px;
}

/* Reader mode header */
.reader-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0 2px;
}

.reader-filename {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Reader mode content */
.markdown-rendered.reader-mode {
  height: calc(100dvh - 44px);
  max-width: 680px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 24px 64px;
  font-family: ui-serif, Georgia, Cambria, 'Times New Roman', Times, serif;
  font-size: 18px;
  line-height: 1.85;
  border: none;
  border-radius: 0;
  background: var(--surface);
}

.markdown-rendered.reader-mode h1 { font-size: 1.9em; }
.markdown-rendered.reader-mode h2 { font-size: 1.45em; }
.markdown-rendered.reader-mode h3 { font-size: 1.2em; }

.markdown-rendered.reader-mode code {
  font-family: var(--font-mono);
  font-size: 0.82em;
}

.markdown-rendered.reader-mode pre {
  font-size: 0.82em;
}
</style>

<style>
/* Non-scoped: reaches NModal content teleported outside component scope */
.preview-reader-modal .n-card {
  border-radius: 0 !important;
  height: 100dvh !important;
}

.preview-reader-modal .n-card-header {
  padding: 6px 16px !important;
  min-height: unset !important;
}

.preview-reader-modal .n-card__content {
  padding: 0 !important;
  overflow: hidden !important;
}

.preview-reader-modal .n-card__footer {
  display: none !important;
}
</style>
