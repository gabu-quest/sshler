<script setup lang="ts">
import { computed } from "vue";
import { NInput, NIcon, NSpin, NEmpty } from "naive-ui";
import { PhMagnifyingGlass, PhFolder, PhFile, PhClockCounterClockwise } from "@phosphor-icons/vue";
import type { SearchResult } from "@/api/types";
import { useFolderSearch, FOLDER_SEARCH_MIN_LENGTH } from "@/composables/useFolderSearch";
import { useI18n } from "@/i18n";

const props = defineProps<{
  box: string | null;
  token: string | null;
  /** Directory to scope filesystem discovery under (default: home) */
  root?: string | null;
  /** Include files in results, not just directories */
  files?: boolean;
}>();

const emit = defineEmits<{
  (e: "select", path: string, isDirectory: boolean): void;
}>();

const { t } = useI18n();

const { query, results, loading, showDropdown, failureLine, walkLine, reset } = useFolderSearch({
  box: () => props.box,
  token: () => props.token,
  root: () => props.root,
  files: () => props.files,
});

const hasResults = computed(() => results.value.length > 0);

function selectResult(result: SearchResult) {
  emit("select", result.path, result.is_directory !== false);
  query.value = "";
  reset();
}

function handleBlur() {
  // Delay to allow click on result
  setTimeout(() => {
    showDropdown.value = false;
  }, 200);
}

function handleFocus() {
  if (query.value.length >= FOLDER_SEARCH_MIN_LENGTH && results.value.length > 0) {
    showDropdown.value = true;
  }
}

function formatPath(path: string): string {
  // Show shortened path only for very long paths (dropdown grows past the input width)
  if (path.length > 90) {
    const parts = path.split("/");
    if (parts.length > 5) {
      return `.../${parts.slice(-5).join("/")}`;
    }
  }
  return path;
}
</script>

<template>
  <div class="directory-search">
    <NInput
      v-model:value="query"
      :placeholder="t('dirsearch.placeholder')"
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

    <div v-if="showDropdown" class="search-dropdown">
      <div v-if="loading && !hasResults" class="search-loading">
        <NSpin size="small" />
        <span>{{ t('dirsearch.searching') }}</span>
      </div>

      <div v-else-if="hasResults" class="search-results">
        <NEmpty v-if="walkLine" size="small" :description="walkLine" class="search-empty" />
        <div
          v-for="result in results"
          :key="result.path"
          class="search-result"
          @mousedown.prevent="selectResult(result)"
        >
          <NIcon size="14" class="result-icon">
            <PhFile v-if="result.is_directory === false" weight="duotone" />
            <PhClockCounterClockwise v-else-if="result.source === 'frecency'" weight="duotone" />
            <PhFolder v-else weight="duotone" />
          </NIcon>
          <span class="result-path" :title="result.path">{{ formatPath(result.path) }}</span>
        </div>
      </div>

      <NEmpty
        v-else
        size="small"
        :description="failureLine ?? walkLine ?? t('dirsearch.no_results')"
        class="search-empty"
      />
    </div>
  </div>
</template>

<style scoped>
.directory-search {
  position: relative;
  width: 100%;
  max-width: 300px;
}

.search-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 100%;
  width: max-content;
  max-width: min(90vw, 680px);
  z-index: 1000;
  margin-top: 4px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  max-height: 300px;
  overflow-y: auto;
}

.search-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  color: var(--muted);
  font-size: 13px;
}

.search-results {
  padding: 4px 0;
}

.search-result {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background-color var(--dur-fast) ease;
}

.search-result:hover {
  background: var(--surface-variant);
}

.result-icon {
  flex-shrink: 0;
  color: var(--muted);
}

.result-path {
  flex: 1;
  font-size: 13px;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-empty {
  padding: 16px;
}

/* Mobile optimizations */
@media (max-width: 767px) {
  .directory-search {
    max-width: 100%;
  }

  /* Under its input and as wide as it, like on a wide screen: the input spans the card here. */
  .search-dropdown {
    right: 0;
    min-width: 0;
    width: auto;
    max-width: none;
    max-height: 50vh;
  }

  .search-result {
    padding: 12px 16px;
  }
}
</style>
