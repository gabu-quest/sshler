<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import { fetchFilePreview } from "@/api/http";
import DiffViewer from "@/components/DiffViewer.vue";
import { useI18n } from "@/i18n";
import { NSpin } from "naive-ui";

type MatchedPair = {
  leftPath: string;
  rightPath: string;
  name: string;
};

const props = withDefaults(defineProps<{
  show: boolean;
  leftBox: string;
  rightBox: string;
  leftPath: string;
  rightPath: string;
  token: string | null;
  matchedPairs?: MatchedPair[];
  currentPairIndex?: number;
}>(), {
  matchedPairs: () => [],
  currentPairIndex: 0,
});

const emit = defineEmits<{
  "update:show": [value: boolean];
  "navigate-pair": [index: number];
}>();

const { t } = useI18n();

const leftContent = ref("");
const rightContent = ref("");
const lang = ref("text");
const loading = ref(false);
const error = ref<string | null>(null);

let loadRequestId = 0;

const totalPairs = computed(() => props.matchedPairs.length);
const hasMatchedPairs = computed(() => totalPairs.value > 0);
const currentIndex = computed(() => props.currentPairIndex ?? 0);
const isIdentical = computed(() => !loading.value && !error.value && leftContent.value === rightContent.value);
const leftFilename = computed(() => getFilename(props.leftPath));
const rightFilename = computed(() => getFilename(props.rightPath));

function getFilename(path: string) {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function getLanguageFromFilename(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    js: "javascript",
    jsx: "javascript",
    ts: "javascript",
    tsx: "javascript",
    py: "python",
    html: "html",
    htm: "html",
    css: "css",
    scss: "css",
    sass: "css",
    json: "json",
    md: "markdown",
    xml: "xml",
    svg: "xml",
  };
  return langMap[ext || ""] || "text";
}

function closeOverlay() {
  emit("update:show", false);
}

function navigatePair(index: number) {
  if (!hasMatchedPairs.value || index < 0 || index >= totalPairs.value) return;
  emit("navigate-pair", index);
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.show) return;

  switch (event.key) {
    case "Escape":
      event.preventDefault();
      closeOverlay();
      break;
    case "ArrowLeft":
      if (!hasMatchedPairs.value) return;
      event.preventDefault();
      navigatePair(currentIndex.value - 1);
      break;
    case "ArrowRight":
      if (!hasMatchedPairs.value) return;
      event.preventDefault();
      navigatePair(currentIndex.value + 1);
      break;
  }
}

async function loadDiff() {
  const requestId = ++loadRequestId;

  if (!props.show || !props.leftPath || !props.rightPath) {
    leftContent.value = "";
    rightContent.value = "";
    lang.value = "text";
    error.value = null;
    loading.value = false;
    return;
  }

  loading.value = true;
  error.value = null;
  leftContent.value = "";
  rightContent.value = "";

  try {
    const [leftPayload, rightPayload] = await Promise.all([
      fetchFilePreview(props.leftBox, props.leftPath, props.token),
      fetchFilePreview(props.rightBox, props.rightPath, props.token),
    ]);

    if (requestId !== loadRequestId) return;

    leftContent.value = leftPayload.content ?? "";
    rightContent.value = rightPayload.content ?? "";

    const leftLang = getLanguageFromFilename(props.leftPath);
    const rightLang = getLanguageFromFilename(props.rightPath);
    lang.value = leftLang === rightLang ? leftLang : "text";
  } catch (err) {
    if (requestId !== loadRequestId) return;
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestId === loadRequestId) {
      loading.value = false;
    }
  }
}

watch(
  () => props.show,
  (showing) => {
    if (showing) {
      document.addEventListener("keydown", handleKeydown);
      return;
    }
    document.removeEventListener("keydown", handleKeydown);
  },
  { immediate: true },
);

watch(
  () => [props.show, props.leftBox, props.rightBox, props.leftPath, props.rightPath, props.token] as const,
  async ([showing]) => {
    if (!showing) {
      loadRequestId += 1;
    }
    await loadDiff();
  },
  { immediate: true },
);

onUnmounted(() => {
  document.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <div v-if="show" class="diff-overlay">
    <div class="diff-header">
      <div class="diff-paths">
        <div class="diff-file" :title="leftPath">
          <span class="diff-box-badge">{{ leftBox }}</span>
          <span class="diff-file-name">{{ leftFilename }}</span>
        </div>
        <div class="diff-file" :title="rightPath">
          <span class="diff-box-badge">{{ rightBox }}</span>
          <span class="diff-file-name">{{ rightFilename }}</span>
        </div>
      </div>

      <div v-if="hasMatchedPairs" class="diff-nav">
        <button type="button" :disabled="currentIndex <= 0" @click="navigatePair(currentIndex - 1)">Prev</button>
        <span class="diff-counter">{{ currentIndex + 1 }} of {{ totalPairs }}</span>
        <button type="button" :disabled="currentIndex >= totalPairs - 1" @click="navigatePair(currentIndex + 1)">Next</button>
      </div>

      <button type="button" class="diff-close" :title="t('common.close')" @click="closeOverlay">X</button>
    </div>

    <div class="diff-body">
      <div v-if="loading" class="diff-loading">
        <NSpin size="large" />
      </div>

      <div v-else-if="error" class="diff-empty diff-error">
        {{ error }}
      </div>

      <div v-else class="diff-content">
        <div v-if="isIdentical" class="diff-identical">
          {{ t("commander.files_identical") }}
        </div>
        <div class="diff-viewer-wrap">
          <DiffViewer :original="leftContent" :modified="rightContent" :language="lang" style="height: 100%" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.diff-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: #0a0a0f;
  display: flex;
  flex-direction: column;
  font-family: var(--font-mono);
}

.diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 12px;
  background: #0d0d14;
  border-bottom: 1px solid #1a1a24;
  font-size: 13px;
  color: #e0e0e6;
}

.diff-paths {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}

.diff-file {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.diff-box-badge {
  border: 1px solid #2a2a3a;
  color: #67e8f9;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  flex-shrink: 0;
}

.diff-file-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diff-nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.diff-nav button {
  background: none;
  border: 1px solid #2a2a3a;
  color: #a78bfa;
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 12px;
}

.diff-nav button:hover {
  border-color: #a78bfa;
}

.diff-nav button:disabled {
  opacity: 0.3;
  cursor: default;
}

.diff-counter {
  color: #888;
  font-size: 12px;
  white-space: nowrap;
}

.diff-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.diff-loading,
.diff-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #888;
}

.diff-error {
  color: #f87171;
  padding: 24px;
  text-align: center;
}

.diff-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.diff-identical {
  padding: 6px 12px;
  border-bottom: 1px solid #1a1a24;
  color: #4ade80;
  font-size: 12px;
}

.diff-viewer-wrap {
  flex: 1;
  min-height: 0;
  padding: 12px;
}

.diff-close {
  background: none;
  border: none;
  color: #f87171;
  cursor: pointer;
  font-size: 16px;
  font-family: var(--font-mono);
  padding: 4px 8px;
}
</style>
