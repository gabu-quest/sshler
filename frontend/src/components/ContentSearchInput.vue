<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NInput, NIcon, NSpin, NEmpty } from "naive-ui";
import { PhMagnifyingGlass, PhFile } from "@phosphor-icons/vue";
import { grepContent } from "@/api/http";
import { useI18n } from "@/i18n";
import type { GrepMatch } from "@/api/types";

const props = defineProps<{
  box: string | null;
  token: string | null;
  directory: string;
}>();

const emit = defineEmits<{
  (e: "navigate", directory: string): void;
  (e: "select-file", filePath: string): void;
}>();

const { t } = useI18n();

const query = ref("");
const results = ref<GrepMatch[]>([]);
const loading = ref(false);
const truncated = ref(false);
const showDropdown = ref(false);
const debounceTimeout = ref<ReturnType<typeof setTimeout> | null>(null);

const hasResults = computed(() => results.value.length > 0);

// Group results by file
const groupedResults = computed(() => {
  const groups: Map<string, GrepMatch[]> = new Map();
  for (const match of results.value) {
    const existing = groups.get(match.file);
    if (existing) {
      existing.push(match);
    } else {
      groups.set(match.file, [match]);
    }
  }
  return groups;
});

watch(query, (newQuery) => {
  if (debounceTimeout.value) {
    clearTimeout(debounceTimeout.value);
  }

  if (!newQuery || newQuery.length < 2) {
    showDropdown.value = false;
    results.value = [];
    return;
  }

  debounceTimeout.value = setTimeout(async () => {
    await performSearch(newQuery);
  }, 500);
});

async function performSearch(pattern: string) {
  if (!props.box) return;

  loading.value = true;
  showDropdown.value = true;

  try {
    const response = await grepContent(props.box, pattern, props.directory, props.token);
    results.value = response.matches;
    truncated.value = response.truncated;
  } catch (err) {
    console.error("Grep search failed:", err);
    results.value = [];
  } finally {
    loading.value = false;
  }
}

function navigateToFile(filePath: string) {
  // Navigate to the file's parent directory and emit file selection
  const parts = filePath.split("/");
  parts.pop();
  const dir = parts.join("/") || "/";
  emit("navigate", dir);
  emit("select-file", filePath);
  query.value = "";
  showDropdown.value = false;
  results.value = [];
}

function handleBlur() {
  setTimeout(() => {
    showDropdown.value = false;
  }, 200);
}

function handleFocus() {
  if (query.value.length >= 2 && results.value.length > 0) {
    showDropdown.value = true;
  }
}

function formatPath(path: string): string {
  if (path.length > 60) {
    const parts = path.split("/");
    if (parts.length > 3) {
      return `.../${parts.slice(-3).join("/")}`;
    }
  }
  return path;
}

function highlightMatch(line: string, pattern: string): string {
  if (!pattern) return escapeHtml(line);
  try {
    const regex = new RegExp(`(${pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
    return escapeHtml(line).replace(
      new RegExp(`(${escapeHtml(pattern).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"),
      '<mark>$1</mark>'
    );
  } catch {
    return escapeHtml(line);
  }
}

function escapeHtml(str: string): string {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
</script>

<template>
  <div class="content-search">
    <NInput
      v-model:value="query"
      :placeholder="t('grep.placeholder')"
      size="small"
      clearable
      @focus="handleFocus"
      @blur="handleBlur"
    >
      <template #prefix>
        <NIcon size="14">
          <PhMagnifyingGlass weight="duotone" />
        </NIcon>
      </template>
      <template #suffix>
        <NSpin v-if="loading" size="small" />
      </template>
    </NInput>

    <div v-if="showDropdown" class="grep-dropdown">
      <div v-if="loading && !hasResults" class="grep-loading">
        <NSpin size="small" />
        <span>{{ t('grep.searching') }}</span>
      </div>

      <div v-else-if="hasResults" class="grep-results">
        <template v-for="[file, matches] in groupedResults" :key="file">
          <div class="grep-file-header" @mousedown.prevent="navigateToFile(file)">
            <NIcon size="12" class="file-icon">
              <PhFile weight="duotone" />
            </NIcon>
            <span class="file-path" :title="file">{{ formatPath(file) }}</span>
            <span class="match-count">{{ matches.length }}</span>
          </div>
          <div
            v-for="match in matches.slice(0, 3)"
            :key="`${file}:${match.line_number}`"
            class="grep-match"
            @mousedown.prevent="navigateToFile(file)"
          >
            <span class="line-number">{{ match.line_number }}</span>
            <span class="line-content" v-html="highlightMatch(match.line, query)" />
          </div>
        </template>
        <div v-if="truncated" class="grep-truncated">
          {{ t('grep.truncated') }}
        </div>
      </div>

      <NEmpty
        v-else
        size="small"
        :description="t('grep.no_matches')"
        class="grep-empty"
      />
    </div>
  </div>
</template>

<style scoped>
.content-search {
  position: relative;
  width: 100%;
}

.grep-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 1000;
  margin-top: 4px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  max-height: 400px;
  overflow-y: auto;
}

.grep-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  color: var(--muted);
  font-size: 13px;
}

.grep-results {
  padding: 4px 0;
}

.grep-file-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  cursor: pointer;
  background: var(--surface-variant);
  font-size: 12px;
  font-weight: 600;
  border-top: 1px solid var(--stroke);
}

.grep-file-header:first-child {
  border-top: none;
}

.grep-file-header:hover {
  background: var(--hover);
}

.file-icon {
  flex-shrink: 0;
  color: var(--muted);
}

.file-path {
  flex: 1;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.match-count {
  flex-shrink: 0;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 8px;
  background: var(--accent-bg);
  color: var(--accent);
}

.grep-match {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 12px 4px 28px;
  cursor: pointer;
  font-size: 12px;
  font-family: var(--font-mono);
  line-height: 1.5;
}

.grep-match:hover {
  background: var(--hover);
}

.line-number {
  flex-shrink: 0;
  color: var(--muted);
  min-width: 30px;
  text-align: right;
}

.line-content {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.line-content :deep(mark) {
  background: var(--warning-bg, rgba(255, 200, 0, 0.3));
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}

.grep-truncated {
  text-align: center;
  padding: 8px;
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
}

.grep-empty {
  padding: 16px;
}

@media (max-width: 768px) {
  .grep-dropdown {
    position: fixed;
    left: 8px;
    right: 8px;
    max-height: 50vh;
  }
}
</style>
