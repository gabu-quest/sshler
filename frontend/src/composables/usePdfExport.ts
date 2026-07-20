/**
 * Orchestrates the PDF export flow:
 *   1. Build printable HTML via `usePrintableHtml`
 *   2. POST it to /api/v1/pdf/render
 *   3. Trigger a browser download of the resulting Blob
 *
 * For multi-file selection, requests are serialized (one at a time) — the
 * backend has a single chromium so parallelism would just queue anyway, and
 * serial execution gives a clean progress indicator + predictable ordering.
 *
 * The Playwright render takes a few seconds, so we provide layered feedback:
 *   - top-of-page indeterminate loading bar (universal "long thing happening")
 *   - stage-aware spinner message that mutates as we move through phases
 *   - elapsed-second counter during the backend phase so the user can see
 *     the process is alive even on slower documents.
 */
import { ref } from "vue";
import { useLoadingBar, useMessage } from "naive-ui";
import type { MessageReactive } from "naive-ui";

import { exportPdf } from "@/api/http";
import { useI18n } from "@/i18n";
import { useBootstrapStore } from "@/stores/bootstrap";

import { usePrintableHtml } from "./usePrintableHtml";

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Browsers need a moment before revoking, otherwise the download is cancelled
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function pdfFilenameFor(path: string): string {
  const base = (path.split("/").pop() || "document").replace(/\.[^.]+$/, "");
  return `${base}.pdf`;
}

/** Start a 500ms ticker that updates `msg.content` with `prefix + Ns`. Returns
 *  a stop function. Designed to make a long-running spinner feel alive. */
function startElapsedTicker(msg: MessageReactive, prefix: string): () => void {
  const start = Date.now();
  const update = () => {
    const elapsed = Math.floor((Date.now() - start) / 1000);
    msg.content = `${prefix} ${elapsed}s`;
  };
  update();
  const id = window.setInterval(update, 500);
  return () => window.clearInterval(id);
}

export function usePdfExport() {
  const message = useMessage();
  const loadingBar = useLoadingBar();
  const { t } = useI18n();
  const bootstrap = useBootstrapStore();
  const busy = ref(false);

  async function renderOne(
    box: string,
    path: string,
    theme: "light" | "dark",
    spinnerPrefix: string,
  ): Promise<void> {
    const spinner = message.loading(`${spinnerPrefix}: ${t("files.pdf_stage_fetching")}`, { duration: 0 });
    let stopTicker: (() => void) | null = null;
    try {
      const { toHtml } = usePrintableHtml();

      spinner.content = `${spinnerPrefix}: ${t("files.pdf_stage_rendering")}`;
      const html = await toHtml({ box, path, token: bootstrap.token, theme });

      // The backend Playwright render is the slow part — tick elapsed seconds
      // so the user sees the spinner is still alive on long documents.
      stopTicker = startElapsedTicker(spinner, `${spinnerPrefix}: ${t("files.pdf_stage_generating")}`);
      const filename = pdfFilenameFor(path);
      const blob = await exportPdf(html, filename, bootstrap.token);
      stopTicker();
      stopTicker = null;

      spinner.content = `${spinnerPrefix}: ${t("files.pdf_stage_downloading")}`;
      triggerDownload(blob, filename);
    } finally {
      if (stopTicker) stopTicker();
      spinner.destroy();
    }
  }

  async function exportOne(
    box: string,
    path: string,
    theme: "light" | "dark" = "light",
  ): Promise<void> {
    if (busy.value) return;
    busy.value = true;
    loadingBar.start();
    try {
      const name = path.split("/").pop() || path;
      await renderOne(box, path, theme, name);
      loadingBar.finish();
    } catch (err) {
      loadingBar.error();
      message.error(`${t("files.pdf_failed")}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      busy.value = false;
    }
  }

  async function exportMany(
    box: string,
    paths: string[],
    theme: "light" | "dark" = "light",
  ): Promise<void> {
    if (busy.value || paths.length === 0) return;
    busy.value = true;
    loadingBar.start();
    let succeeded = 0;
    try {
      for (let i = 0; i < paths.length; i++) {
        const path = paths[i];
        const name = path.split("/").pop() || path;
        const prefix = `PDF ${i + 1}/${paths.length}: ${name}`;
        try {
          await renderOne(box, path, theme, prefix);
          succeeded++;
        } catch (err) {
          message.error(`${t("files.pdf_failed")} (${name}): ${err instanceof Error ? err.message : String(err)}`);
        }
      }
      if (succeeded === paths.length) {
        loadingBar.finish();
      } else if (succeeded > 0) {
        loadingBar.error();
      } else {
        loadingBar.error();
      }
      if (succeeded > 0) {
        message.success(`PDF: ${succeeded}/${paths.length}`);
      }
    } finally {
      busy.value = false;
    }
  }

  return { busy, exportOne, exportMany };
}
