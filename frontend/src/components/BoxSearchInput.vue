<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { NInput, NIcon, NSpin, NEmpty } from "naive-ui";
import { PhMagnifyingGlass, PhFolder, PhClockCounterClockwise } from "@phosphor-icons/vue";
import type { ApiSession } from "@/api/types";
import { useFolderSearch, FOLDER_SEARCH_MIN_LENGTH } from "@/composables/useFolderSearch";
import { generateSessionName } from "@/utils/sessionName";
import { useI18n } from "@/i18n";

/**
 * One field for a box: its running sessions first, then the folders the folder search finds.
 * The sessions are the rows the caller already holds; the folders come from the search that
 * the Files page's input uses.
 */
const props = defineProps<{
  box: string;
  token: string | null;
  sessions: ApiSession[];
}>();

const emit = defineEmits<{
  (e: "pick-session", sessionName: string): void;
  (e: "pick-folder", path: string): void;
}>();

const { t } = useI18n();

const { query, results, loading, answered, failureLine, walkLine, reset } = useFolderSearch({
  box: () => props.box,
  token: () => props.token,
});

type SessionRow = { kind: "session"; key: string; name: string; path: string };
type FolderRow = { kind: "folder"; key: string; path: string; frecent: boolean };
type Row = SessionRow | FolderRow;

// Escape or leaving the field closes the list and keeps the text; typing opens it again.
const dismissed = ref(false);
// The highlight is kept by the row's key, so it stays on its row when rows come and go.
const highlightedKey = ref<string | null>(null);

const searching = computed(() => query.value.length >= FOLDER_SEARCH_MIN_LENGTH);
const open = computed(() => searching.value && !dismissed.value);

const sessionRows = computed<SessionRow[]>(() => {
  if (!searching.value) return [];
  const needle = query.value.toLowerCase();
  const rows: SessionRow[] = [];
  const seen = new Set<string>();
  for (const session of props.sessions) {
    if (!session.active) continue;
    const name = session.session_name ?? "";
    const path = session.working_directory ?? "";
    if (!name.toLowerCase().includes(needle) && !path.toLowerCase().includes(needle)) continue;
    const identity = `${name}\u0000${path}`;
    if (seen.has(identity)) continue;
    seen.add(identity);
    rows.push({ kind: "session", key: `s:${session.id}`, name, path });
  }
  return rows;
});

const folderRows = computed<FolderRow[]>(() => {
  const shown = new Set(sessionRows.value.map((row) => `${row.name}\u0000${row.path}`));
  const rows: FolderRow[] = [];
  for (const result of results.value) {
    if (shown.has(`${generateSessionName(result.path)}\u0000${result.path}`)) continue;
    rows.push({ kind: "folder", key: `f:${result.path}`, path: result.path, frecent: result.source === "frecency" });
  }
  return rows;
});

const rows = computed<Row[]>(() => [...sessionRows.value, ...folderRows.value]);
const line = computed(() => failureLine.value ?? walkLine.value);

// A folder row can be picked only once the search has answered the text now typed.
const selectable = computed(() => rows.value.filter((row) => row.kind === "session" || answered.value));
const current = computed<Row | null>(
  () => selectable.value.find((row) => row.key === highlightedKey.value) ?? selectable.value[0] ?? null,
);

watch(query, () => {
  dismissed.value = false;
  highlightedKey.value = null;
});
// When the highlighted row is gone, the top row is highlighted.
watch(selectable, (now) => {
  if (highlightedKey.value !== null && !now.some((row) => row.key === highlightedKey.value)) {
    highlightedKey.value = null;
  }
});

function pick(row: Row) {
  if (row.kind === "session") emit("pick-session", row.name);
  else emit("pick-folder", row.path);
  query.value = "";
  reset();
}

function hover(row: Row) {
  if (selectable.value.some((candidate) => candidate.key === row.key)) highlightedKey.value = row.key;
}

const listEl = ref<HTMLElement | null>(null);

function move(step: number) {
  const rowsNow = selectable.value;
  if (!rowsNow.length) return;
  const at = rowsNow.findIndex((row) => row.key === current.value?.key);
  highlightedKey.value = rowsNow[(at + step + rowsNow.length) % rowsNow.length]!.key;
  void nextTick(() => {
    listEl.value?.querySelector(".search-result.highlighted")?.scrollIntoView?.({ block: "nearest" });
  });
}

function onKeydown(event: KeyboardEvent) {
  // Keys of an input method composition belong to the input method, not to the list.
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === "Escape") {
    dismissed.value = true;
    return;
  }
  if (!open.value) {
    if (event.key === "ArrowDown" && searching.value) {
      dismissed.value = false;
      event.preventDefault();
    }
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    move(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    move(-1);
  } else if (event.key === "Enter") {
    if (current.value) {
      event.preventDefault();
      pick(current.value);
    }
  }
}
</script>

<template>
  <div class="box-search" @keydown="onKeydown">
    <NInput
      v-model:value="query"
      :placeholder="t('overview.search_placeholder')"
      size="small"
      clearable
      @focus="dismissed = false"
      @blur="dismissed = true"
    >
      <template #prefix>
        <NIcon size="14"><PhMagnifyingGlass weight="duotone" /></NIcon>
      </template>
      <template #suffix>
        <NSpin v-if="loading" size="small" />
      </template>
    </NInput>

    <!-- A press on the list never takes the focus from the field, so the list stays open. -->
    <div v-if="open" ref="listEl" class="search-dropdown" data-testid="box-search-list" @mousedown.prevent>
      <div v-if="rows.length === 0 && !answered" class="search-loading">
        <NSpin size="small" />
        <span>{{ t('dirsearch.searching') }}</span>
      </div>

      <div v-else-if="rows.length > 0" class="search-results">
        <div
          v-for="row in rows"
          :key="row.key"
          class="search-result"
          :class="{ highlighted: row.key === current?.key, 'is-session': row.kind === 'session' }"
          :data-kind="row.kind"
          @mousemove="hover(row)"
          @mousedown.prevent="pick(row)"
        >
          <span
            v-if="row.kind === 'session'"
            class="session-dot"
            role="img"
            :title="t('overview.search_running')"
            :aria-label="t('overview.search_running')"
          ></span>
          <NIcon v-else size="14" class="result-icon">
            <PhClockCounterClockwise v-if="row.frecent" weight="duotone" />
            <PhFolder v-else weight="duotone" />
          </NIcon>
          <span v-if="row.kind === 'session'" class="result-name">{{ row.name || t('overview.unnamed') }}</span>
          <span class="result-path" :title="row.path"><bdi>{{ row.path }}</bdi></span>
        </div>
        <NEmpty v-if="line" size="small" :description="line" class="search-empty" />
      </div>

      <NEmpty v-else size="small" :description="line ?? t('dirsearch.no_results')" class="search-empty" />
    </div>
  </div>
</template>

<style scoped>
.box-search {
  position: relative;
  width: 100%;
}

.search-dropdown {
  position: absolute;
  top: 100%;
  /* As wide as the field, which is inside its card: the list can reach neither past the
     window's right edge, in the last column, nor under the next card. A long path is cut
     (see .result-path) and shows whole in its title. */
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

.search-result.highlighted {
  background: var(--surface-variant);
}

.result-icon {
  flex-shrink: 0;
  color: var(--muted);
}

.session-dot {
  width: 8px;
  height: 8px;
  margin: 0 3px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #22c55e;
}

.result-name {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
}

.result-path {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* A path too long for its row is cut at its start, so the folder's own name stays in
     view. The text itself runs left to right (it sits in a <bdi>). */
  direction: rtl;
  text-align: left;
}

.search-empty {
  padding: 16px;
}

@media (max-width: 768px) {
  .search-dropdown {
    max-height: 50vh;
  }

  .search-result {
    padding: 12px 16px;
  }
}
</style>
