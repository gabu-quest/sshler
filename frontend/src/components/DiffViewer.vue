<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from "vue";
import { EditorView, basicSetup } from "codemirror";
import { EditorState } from "@codemirror/state";
import { MergeView } from "@codemirror/merge";
import { oneDark } from "@codemirror/theme-one-dark";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { xml } from "@codemirror/lang-xml";

const props = withDefaults(defineProps<{
  original: string;
  modified: string;
  language?: string;
  theme?: "light" | "dark";
}>(), {
  language: "text",
  theme: "dark",
});

const containerRef = ref<HTMLDivElement>();
let mergeView: MergeView | null = null;

const getLanguageExtension = (lang: string) => {
  switch (lang.toLowerCase()) {
    case "javascript": case "js": case "jsx": case "ts": case "tsx": return javascript();
    case "python": case "py": return python();
    case "html": case "htm": return html();
    case "css": case "scss": case "sass": return css();
    case "json": return json();
    case "markdown": case "md": return markdown();
    case "xml": case "svg": return xml();
    default: return [];
  }
};

const createMergeView = () => {
  if (!containerRef.value) return;
  destroyMergeView();

  const langExt = getLanguageExtension(props.language);
  const themeExts = props.theme === "dark" ? [oneDark] : [];

  mergeView = new MergeView({
    parent: containerRef.value,
    a: {
      doc: props.original,
      extensions: [basicSetup, langExt, EditorState.readOnly.of(true), EditorView.lineWrapping, ...themeExts],
    },
    b: {
      doc: props.modified,
      extensions: [basicSetup, langExt, EditorState.readOnly.of(true), EditorView.lineWrapping, ...themeExts],
    },
    collapseUnchanged: { margin: 3, minSize: 4 },
  });
};

const destroyMergeView = () => {
  if (mergeView) {
    mergeView.destroy();
    mergeView = null;
  }
};

watch([() => props.original, () => props.modified, () => props.language, () => props.theme], () => {
  createMergeView();
});

onMounted(async () => {
  await nextTick();
  createMergeView();
});

onUnmounted(() => {
  destroyMergeView();
});
</script>

<template>
  <div ref="containerRef" class="diff-viewer" />
</template>

<style scoped>
.diff-viewer {
  border: 1px solid var(--stroke);
  border-radius: 8px;
  overflow: hidden;
}

.diff-viewer :deep(.cm-editor) {
  font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;
  font-size: 14px;
  line-height: 1.5;
}

.diff-viewer :deep(.cm-focused) {
  outline: none;
}

.diff-viewer :deep(.cm-mergeView) {
  height: 100%;
}

.diff-viewer :deep(.cm-changedLine) {
  background-color: rgba(var(--accent-rgb), 0.08);
}

.diff-viewer :deep(.cm-changedText) {
  background-color: rgba(var(--accent-rgb), 0.2);
}

:global([data-theme="light"]) .diff-viewer :deep(.cm-editor) {
  background-color: #ffffff;
}

:global([data-theme="light"]) .diff-viewer :deep(.cm-gutters) {
  background-color: #f5f5f5;
  border-right: 1px solid #e0e0e0;
}
</style>
