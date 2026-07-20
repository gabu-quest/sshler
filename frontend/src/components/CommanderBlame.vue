<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import { gitBlame } from "@/api/http";
import type { GitBlameLine } from "@/api/types";
import { useI18n } from "@/i18n";
import { NSpin } from "naive-ui";

const props = defineProps<{
  show: boolean;
  box: string;
  directory: string;
  path: string;
  token: string | null;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
}>();

const { t } = useI18n();

const BLAME_COLORS = [
  "rgba(167, 139, 250, 0.08)",
  "rgba(56, 189, 248, 0.08)",
  "rgba(52, 211, 153, 0.08)",
  "rgba(251, 191, 36, 0.08)",
  "rgba(248, 113, 113, 0.08)",
  "rgba(244, 114, 182, 0.08)",
  "rgba(45, 212, 191, 0.08)",
  "rgba(163, 163, 163, 0.08)",
] as const;

type ProcessedBlameLine = GitBlameLine & {
  isBlockStart: boolean;
  color: string;
};

const blameLines = ref<GitBlameLine[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const truncated = ref(false);
const commitColorMap = ref(new Map<string, string>());

let loadRequestId = 0;

const filename = computed(() => {
  const parts = props.path.split("/");
  return parts[parts.length - 1] || props.path;
});

const displayPath = computed(() => props.path || filename.value);

const processedLines = computed<ProcessedBlameLine[]>(() => {
  return blameLines.value.map((line, i) => {
    const prev = i > 0 ? blameLines.value[i - 1] : null;
    const isBlockStart = !prev || prev.commit !== line.commit;
    return { ...line, isBlockStart, color: commitColor(line.commit) };
  });
});

function commitColor(hash: string): string {
  if (!commitColorMap.value.has(hash)) {
    commitColorMap.value.set(hash, BLAME_COLORS[commitColorMap.value.size % BLAME_COLORS.length]);
  }
  return commitColorMap.value.get(hash)!;
}

function truncateAuthor(author: string): string {
  return author.length > 12 ? `${author.slice(0, 11)}…` : author;
}

function close() {
  emit("update:show", false);
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.show) return;
  if (event.key === "Escape") {
    event.preventDefault();
    close();
  }
}

async function loadBlame() {
  const requestId = ++loadRequestId;

  if (!props.show || !props.path) {
    blameLines.value = [];
    loading.value = false;
    error.value = null;
    truncated.value = false;
    commitColorMap.value = new Map();
    return;
  }

  loading.value = true;
  error.value = null;
  blameLines.value = [];
  truncated.value = false;
  commitColorMap.value = new Map();

  try {
    const result = await gitBlame(props.box, props.directory, props.path, props.token);
    if (requestId !== loadRequestId) return;

    if (result.error) {
      error.value = result.error;
      return;
    }

    blameLines.value = result.lines ?? [];
    truncated.value = result.truncated ?? false;
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
  () => [props.show, props.box, props.directory, props.path, props.token] as const,
  async ([showing]) => {
    if (!showing) {
      loadRequestId += 1;
      loading.value = false;
      return;
    }
    await loadBlame();
  },
  { immediate: true },
);

onUnmounted(() => {
  document.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <div v-if="show" class="blame-overlay">
    <div class="blame-header">
      <div class="blame-filename">
        <span class="blame-path" :title="displayPath">{{ displayPath }}</span>
        <span v-if="truncated" class="blame-truncated">{{ t("grep.truncated") }}</span>
      </div>

      <button type="button" class="blame-close" :title="t('common.close')" :aria-label="t('common.close')" @click="close">
        X
      </button>
    </div>

    <div v-if="loading" class="blame-loading">
      <NSpin size="large" />
    </div>

    <div v-else-if="error" class="blame-error">
      {{ error }}
    </div>

    <div v-else class="blame-content">
      <div
        v-for="line in processedLines"
        :key="line.line_number"
        class="blame-line"
        :style="{ background: line.color }"
      >
        <div class="blame-gutter">
          <template v-if="line.isBlockStart">
            <span class="gutter-hash" :title="line.commit">{{ line.commit }}</span>
            <span class="gutter-author" :title="line.author">{{ truncateAuthor(line.author) }}</span>
            <span class="gutter-date" :title="line.date">{{ line.date }}</span>
          </template>
        </div>

        <div class="blame-line-no">{{ line.line_number }}</div>
        <pre class="blame-code">{{ line.content }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.blame-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: #0a0a0f;
  display: flex;
  flex-direction: column;
  font-family: var(--font-mono);
}

.blame-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  background: #0d0d14;
  border-bottom: 1px solid #1a1a24;
  font-size: 13px;
  color: #e0e0e6;
}

.blame-filename {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.blame-path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.blame-truncated {
  color: #fbbf24;
  font-size: 11px;
  white-space: nowrap;
}

.blame-close {
  background: none;
  border: none;
  color: #f87171;
  cursor: pointer;
  font-size: 16px;
  font-family: var(--font-mono);
  padding: 4px 8px;
}

.blame-loading,
.blame-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #888;
}

.blame-error {
  color: #f87171;
  padding: 24px;
  text-align: center;
}

.blame-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.blame-line {
  display: flex;
  align-items: stretch;
  min-height: 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.02);
}

.blame-gutter {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  font-size: 11px;
  color: #888;
  border-right: 1px solid #1a1a24;
  overflow: hidden;
}

.gutter-hash {
  width: 52px;
  flex-shrink: 0;
  color: #a78bfa;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gutter-author {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gutter-date {
  flex-shrink: 0;
  color: #555;
}

.blame-line-no {
  width: 44px;
  flex-shrink: 0;
  text-align: right;
  padding: 0 8px 0 4px;
  font-size: 11px;
  color: #444;
  user-select: none;
  line-height: 20px;
}

.blame-code {
  flex: 1;
  min-width: 0;
  margin: 0;
  padding: 0 8px;
  font-size: 12px;
  color: #d4d4d8;
  white-space: pre;
  overflow-x: auto;
  line-height: 20px;
}
</style>
