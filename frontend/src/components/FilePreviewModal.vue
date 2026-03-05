<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NIcon, NModal, NSpace, NSpin, NSwitch, useMessage } from "naive-ui";
import { PhDownloadSimple, PhEye, PhFile, PhPencil } from "@phosphor-icons/vue";
import type { FilePreview } from "@/api/types";
import { fetchFilePreview, downloadFile } from "@/api/http";
import CodeEditor from "@/components/CodeEditor.vue";
import { useI18n } from "@/i18n";
import { marked } from "marked";
import DOMPurify from "dompurify";

const props = defineProps<{
  show: boolean;
  path: string;
  box: string | null;
  token: string | null;
  theme: "light" | "dark";
}>();

const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
  (e: "edit", path: string): void;
  (e: "compare", path: string): void;
}>();

const { t } = useI18n();
const message = useMessage();

const content = ref("");
const meta = ref<FilePreview | null>(null);
const loading = ref(false);
const markdownRenderMode = ref(false);
const showLineNumbers = ref(true);
const wordWrap = ref(true);

const isImage = computed(() => !!(meta.value?.image_data && meta.value?.image_mime && !meta.value?.image_too_large));

const isMarkdownFile = computed(() => {
  const name = meta.value?.name?.toLowerCase() || props.path.split("/").pop()?.toLowerCase() || "";
  return name.endsWith(".md") || name.endsWith(".markdown") || name.endsWith(".mdx");
});

const renderedMarkdown = computed(() => {
  if (!markdownRenderMode.value || !content.value) return "";
  return DOMPurify.sanitize(marked.parse(content.value) as string);
});

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
  if (!showing || !props.box || !props.path) return;
  content.value = "";
  meta.value = null;
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
});

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
}

function updateContent(newContent: string) {
  content.value = newContent;
}

defineExpose({ updateContent });
</script>

<template>
  <NModal :show="show" preset="card" style="max-width: 90vw; max-height: 90vh" @update:show="emit('update:show', $event)">
    <template #header>
      <div class="modal-header">
        <NIcon size="16"><PhEye weight="duotone" /></NIcon>
        <span>Preview: {{ path.split('/').pop() }}</span>
        <div class="modal-actions">
          <NButton v-if="isMarkdownFile" size="small" @click="markdownRenderMode = !markdownRenderMode">
            <NIcon size="14"><PhEye v-if="!markdownRenderMode" weight="duotone" /><PhFile v-else weight="duotone" /></NIcon>
            {{ markdownRenderMode ? 'Source' : 'Render' }}
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
          <img class="preview-image" :src="`data:${meta?.image_mime};base64,${meta?.image_data}`" :alt="path" />
          <p v-if="meta?.image_too_large" class="text-muted small">Image truncated at {{ meta?.image_limit_kb }}KB</p>
        </div>
        <div v-else class="preview-text-container">
          <div v-if="markdownRenderMode && isMarkdownFile" class="markdown-rendered" v-html="renderedMarkdown" />
          <CodeEditor v-else :model-value="content" :language="getLanguageFromFilename(path)" :theme="theme" :readonly="true" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 75vh" />
        </div>
      </template>
    </div>

    <template #footer>
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

.preview-image {
  max-width: 100%;
  max-height: 70vh;
  border-radius: 8px;
  border: 1px solid var(--stroke);
  background: var(--surface);
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
</style>
