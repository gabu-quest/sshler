<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NInput, NIcon, NSpin, NEmpty } from "naive-ui";
import { PhMagnifyingGlass, PhFolder, PhClockCounterClockwise } from "@phosphor-icons/vue";
import { searchDirectories } from "@/api/http";
import type { SearchResult } from "@/api/types";

const props = defineProps<{
  box: string | null;
  token: string | null;
}>();

const emit = defineEmits<{
  (e: "select", path: string): void;
}>();

const query = ref("");
const results = ref<SearchResult[]>([]);
const loading = ref(false);
const showDropdown = ref(false);
const debounceTimeout = ref<ReturnType<typeof setTimeout> | null>(null);

const hasResults = computed(() => results.value.length > 0);

watch(query, (newQuery) => {
  // Clear previous debounce
  if (debounceTimeout.value) {
    clearTimeout(debounceTimeout.value);
  }

  // Hide dropdown and clear results if query too short
  if (!newQuery || newQuery.length < 2) {
    showDropdown.value = false;
    results.value = [];
    return;
  }

  // Debounce API call by 300ms
  debounceTimeout.value = setTimeout(async () => {
    await performSearch(newQuery);
  }, 300);
});

async function performSearch(searchQuery: string) {
  if (!props.box) return;

  loading.value = true;
  showDropdown.value = true;

  try {
    const response = await searchDirectories(props.box, searchQuery, props.token);
    results.value = response.results;
  } catch (err) {
    console.error("Directory search failed:", err);
    results.value = [];
  } finally {
    loading.value = false;
  }
}

function selectResult(result: SearchResult) {
  emit("select", result.path);
  query.value = "";
  showDropdown.value = false;
  results.value = [];
}

function handleBlur() {
  // Delay to allow click on result
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
  // Show shortened path for long paths
  if (path.length > 50) {
    const parts = path.split("/");
    if (parts.length > 3) {
      return `.../${parts.slice(-3).join("/")}`;
    }
  }
  return path;
}
</script>

<template>
  <div class="directory-search">
    <NInput
      v-model:value="query"
      placeholder="Search directories..."
      size="small"
      clearable
      @focus="handleFocus"
      @blur="handleBlur"
    >
      <template #prefix>
        <NIcon size="14">
          <PhMagnifyingGlass />
        </NIcon>
      </template>
      <template #suffix>
        <NSpin v-if="loading" size="small" />
      </template>
    </NInput>

    <div v-if="showDropdown" class="search-dropdown">
      <div v-if="loading && !hasResults" class="search-loading">
        <NSpin size="small" />
        <span>Searching...</span>
      </div>

      <div v-else-if="hasResults" class="search-results">
        <div
          v-for="result in results"
          :key="result.path"
          class="search-result"
          @mousedown.prevent="selectResult(result)"
        >
          <NIcon size="14" class="result-icon">
            <PhFolder v-if="result.source === 'frecency'" />
            <PhClockCounterClockwise v-else />
          </NIcon>
          <span class="result-path" :title="result.path">{{ formatPath(result.path) }}</span>
          <span class="result-score" :class="result.source">
            {{ result.source === "frecency" ? "visited" : "found" }}
          </span>
        </div>
      </div>

      <NEmpty
        v-else
        size="small"
        description="No directories found"
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
  right: 0;
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
  transition: background-color 0.15s ease;
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

.result-score {
  flex-shrink: 0;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.result-score.frecency {
  background: var(--accent-bg);
  color: var(--accent);
}

.result-score.discovery {
  background: var(--surface-variant);
  color: var(--muted);
}

.search-empty {
  padding: 16px;
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .directory-search {
    max-width: 100%;
  }

  .search-dropdown {
    position: fixed;
    left: 8px;
    right: 8px;
    max-height: 50vh;
  }

  .search-result {
    padding: 12px 16px;
  }
}
</style>
