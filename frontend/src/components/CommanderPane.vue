<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { fetchDirectory, gitInfo } from "@/api/http";
import type { DirectoryEntry, DirectoryListing, GitInfo } from "@/api/types";
import GitBadge from "@/components/GitBadge.vue";
import { useI18n } from "@/i18n";
import { getEmojiForBox } from "@/utils/emoji-favicon";
import { PhArrowBendUpLeft, PhFile, PhFolderSimple } from "@phosphor-icons/vue";
import { NIcon, NSelect } from "naive-ui";

type PaneRow = DirectoryEntry & {
  key: string;
  isParent?: boolean;
};

type BoxSelectOption = {
  label: string;
  value: string;
};

type BoxSelectGroupOption = {
  type: "group";
  label: string;
  key: string;
  children: BoxSelectOption[];
};

const props = defineProps<{
  box: string;
  directory: string;
  selected: string[];
  active: boolean;
  token: string | null;
  boxOptions: Array<{ name: string; host: string }>;
  favorites?: Map<string, string[]>;
  matchedFiles?: Set<string>;
  branchChangedFiles?: Set<string>;
  matchedSizes?: Map<string, number>;
}>();

const emit = defineEmits<{
  "update:box": [value: string];
  "update:directory": [value: string];
  "update:selected": [value: string[]];
  focus: [];
  "open-git-log": [];
  "open-blame": [path: string];
}>();

const { t } = useI18n();

const loading = ref(false);
const error = ref<string | null>(null);
const listing = ref<DirectoryListing | null>(null);
const currentGitInfo = ref<GitInfo | null>(null);
const cursorIndex = ref(0);
const lastClickIndex = ref<number | null>(null);
const listRef = ref<HTMLDivElement | null>(null);
const filterQuery = ref("");
const showFilter = ref(false);
const filterInputRef = ref<HTMLInputElement | null>(null);

let listingRequestId = 0;
let gitRequestId = 0;

const selectOptions = computed(() => {
  const options: Array<BoxSelectOption | BoxSelectGroupOption> = [];

  for (const box of props.boxOptions) {
    const emoji = getEmojiForBox(box.name);
    const boxFavorites = props.favorites?.get(box.name) ?? [];

    if (boxFavorites.length === 0) {
      options.push({
        label: `${emoji} ${box.name}`,
        value: box.name,
      });
      continue;
    }

    options.push({
      type: "group",
      label: `${emoji} ${box.name}`,
      key: box.name,
      children: [
        { label: `${emoji} ${box.name} (home)`, value: box.name },
        ...boxFavorites.map((favorite) => {
          const shortName = favorite.split("/").filter(Boolean).pop() || favorite;
          return {
            label: `  📁 ${shortName}`,
            value: `${box.name}::${favorite}`,
          };
        }),
      ],
    });
  }

  return options;
});

const rows = computed<PaneRow[]>(() => {
  const parentPath = getParentDirectory(props.directory);
  const parent: PaneRow = {
    key: "__parent__",
    name: "..",
    path: parentPath,
    is_directory: true,
    size: null,
    modified: null,
    mode: null,
    isParent: true,
  };

  const entries = [...(listing.value?.entries ?? [])].sort((a, b) => {
    if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return [parent, ...entries.map((entry) => ({ ...entry, key: entry.path }))];
});

const filteredRows = computed(() => {
  if (!filterQuery.value) return rows.value;
  const q = filterQuery.value.toLowerCase();
  return rows.value.filter((row) => row.isParent || row.name.toLowerCase().includes(q));
});

const fileCountLabel = computed(() => {
  const count = filteredRows.value.filter(r => !r.isParent).length;
  return t("commander.files_count", { count });
});
const totalSizeLabel = computed(() => {
  const total = filteredRows.value.filter(r => !r.isParent).reduce((sum, entry) => sum + (entry.size ?? 0), 0);
  return formatSize(total);
});
const crumbSegments = computed(() => buildBreadcrumbs(props.directory));

function normalizeDirectory(directory: string) {
  return directory || "~";
}

function getParentDirectory(directory: string) {
  const normalized = normalizeDirectory(directory).replace(/\/+$/, "");
  if (!normalized || normalized === "/" || normalized === "~") return normalized || "~";
  if (normalized.startsWith("~/")) {
    const parent = normalized.replace(/\/[^/]+$/, "");
    return parent || "~";
  }
  const index = normalized.lastIndexOf("/");
  if (index <= 0) return "/";
  return normalized.slice(0, index) || "/";
}

function buildBreadcrumbs(directory: string) {
  const normalized = normalizeDirectory(directory);

  if (normalized === "~") {
    return [{ label: "~", path: "~" }];
  }

  if (normalized === "/") {
    return [{ label: "/", path: "/" }];
  }

  if (normalized.startsWith("~/")) {
    const parts = normalized.slice(2).split("/").filter(Boolean);
    const segments = [{ label: "~", path: "~" }];
    let current = "~";
    for (const part of parts) {
      current = `${current}/${part}`;
      segments.push({ label: part, path: current });
    }
    return segments;
  }

  const parts = normalized.split("/").filter(Boolean);
  const segments = [{ label: "/", path: "/" }];
  let current = "";
  for (const part of parts) {
    current = `${current}/${part}`;
    segments.push({ label: part, path: current });
  }
  return segments;
}

function formatSize(bytes: number | null | undefined) {
  if (bytes == null) return "-";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)}G`;
}

function formatDate(timestamp: number | null | undefined) {
  if (!timestamp) return "-";

  const value = new Date(timestamp * 1000);
  const now = new Date();
  const isToday = value.toDateString() === now.toDateString();
  const isThisYear = value.getFullYear() === now.getFullYear();

  if (isToday) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(value);
  }

  if (isThisYear) {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "2-digit",
    }).format(value);
  }

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
  }).format(value);
}

function formatPerms(mode: number | null | undefined) {
  if (mode == null) return "---------";
  const bits = mode & 0o777;
  const flags = [
    0o400, 0o200, 0o100,
    0o040, 0o020, 0o010,
    0o004, 0o002, 0o001,
  ];
  const chars = ["r", "w", "x", "r", "w", "x", "r", "w", "x"];
  return flags.map((flag, index) => (bits & flag ? chars[index] : "-")).join("");
}

function rowIcon(row: PaneRow) {
  if (row.isParent) return PhArrowBendUpLeft;
  return row.is_directory ? PhFolderSimple : PhFile;
}

function rowSize(row: PaneRow) {
  if (row.isParent) return "<UP>";
  if (row.is_directory) return "<DIR>";
  return formatSize(row.size);
}

function sizeDeltaClass(row: PaneRow): string {
  const otherSize = props.matchedSizes?.get(row.name);
  if (otherSize == null || row.size == null) return "";
  if (row.size < otherSize) return "size-smaller";
  if (row.size > otherSize) return "size-larger";
  return "size-equal";
}

function sizeDeltaLabel(row: PaneRow): string {
  const otherSize = props.matchedSizes?.get(row.name);
  if (otherSize == null || row.size == null) return "";
  const diff = row.size - otherSize;
  if (diff === 0) return "=";
  return diff > 0 ? `+${formatSize(diff)}` : `-${formatSize(Math.abs(diff))}`;
}

function openSearch() {
  showFilter.value = true;
  filterQuery.value = "";
  nextTick(() => filterInputRef.value?.focus());
}

function closeSearch() {
  showFilter.value = false;
  filterQuery.value = "";
}

function isSelected(row: PaneRow) {
  return !row.isParent && props.selected.includes(row.path);
}

function emitFocus() {
  emit("focus");
}

function setCursor(index: number) {
  cursorIndex.value = Math.max(0, Math.min(rows.value.length - 1, index));
  scrollCursorIntoView();
}

function scrollCursorIntoView() {
  nextTick(() => {
    const container = listRef.value;
    if (!container) return;
    const row = container.querySelector<HTMLElement>(`[data-index="${cursorIndex.value}"]`);
    row?.scrollIntoView({ block: "nearest" });
  });
}

function moveCursor(delta: number) {
  setCursor(cursorIndex.value + delta);
}

function navigateTo(path: string) {
  emit("update:directory", path);
  emit("update:selected", []);
  lastClickIndex.value = null;
  cursorIndex.value = 0;
}

function activateCurrent() {
  const row = rows.value[cursorIndex.value];
  if (!row) return;

  if (row.isParent || row.is_directory) {
    navigateTo(row.path);
    return;
  }

  emit("update:selected", [row.path]);
}

function goUp() {
  navigateTo(getParentDirectory(props.directory));
}

function goHome() {
  navigateTo("~");
}

function toggleSelectCurrent() {
  const row = rows.value[cursorIndex.value];
  if (!row || row.isParent) return;

  if (props.selected.includes(row.path)) {
    emit(
      "update:selected",
      props.selected.filter((path) => path !== row.path),
    );
    return;
  }

  emit("update:selected", [...props.selected, row.path]);
}

function selectAll() {
  const allPaths = rows.value.filter((row) => !row.isParent).map((row) => row.path);
  const allSelected = allPaths.length > 0 && allPaths.every((path) => props.selected.includes(path));
  if (allSelected) {
    emit("update:selected", []);
  } else {
    emit("update:selected", allPaths);
  }
}

function getCursorFile(): PaneRow | null {
  return rows.value[cursorIndex.value] ?? null;
}

async function loadListing() {
  const requestId = ++listingRequestId;
  loading.value = true;
  error.value = null;

  try {
    const result = await fetchDirectory(props.box, normalizeDirectory(props.directory), props.token);
    if (requestId !== listingRequestId) return;
    listing.value = result;
    cursorIndex.value = 0;
    scrollCursorIntoView();
  } catch (err) {
    if (requestId !== listingRequestId) return;
    listing.value = null;
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestId === listingRequestId) {
      loading.value = false;
    }
  }
}

async function loadGitInfo() {
  const requestId = ++gitRequestId;

  try {
    const result = await gitInfo(props.box, normalizeDirectory(props.directory), props.token);
    if (requestId !== gitRequestId) return;
    currentGitInfo.value = result;
  } catch {
    if (requestId !== gitRequestId) return;
    currentGitInfo.value = null;
  }
}

async function reload() {
  await loadListing();
  loadGitInfo();
}

function clickRow(row: PaneRow, index: number, event: MouseEvent) {
  emitFocus();
  setCursor(index);

  if (row.isParent) {
    emit("update:selected", []);
    lastClickIndex.value = null;
    return;
  }

  if (event.shiftKey && lastClickIndex.value !== null) {
    const lo = Math.min(lastClickIndex.value, index);
    const hi = Math.max(lastClickIndex.value, index);
    const rangePaths = rows.value
      .slice(lo, hi + 1)
      .filter((rangeRow) => !rangeRow.isParent)
      .map((rangeRow) => rangeRow.path);
    const merged = new Set([...props.selected, ...rangePaths]);
    emit("update:selected", [...merged]);
  } else if (event.ctrlKey || event.metaKey) {
    if (props.selected.includes(row.path)) {
      emit("update:selected", props.selected.filter((path) => path !== row.path));
    } else {
      emit("update:selected", [...props.selected, row.path]);
    }
  } else {
    emit("update:selected", []);
  }

  lastClickIndex.value = index;
}

function handleRowClick(row: PaneRow, index: number, event: MouseEvent) {
  clickRow(row, index, event);
}

function doubleClickRow(_row: PaneRow, index: number) {
  setCursor(index);
  activateCurrent();
}

function onBoxChange(value: string | null) {
  emitFocus();
  if (!value) return;

  if (value.includes("::")) {
    const [boxName, directory] = value.split("::", 2);
    if (!boxName || !directory) return;
    if (boxName !== props.box) {
      emit("update:box", boxName);
    }
    if (directory !== props.directory) {
      emit("update:directory", directory);
    }
    return;
  }

  if (value !== props.box) {
    emit("update:box", value);
  }
  if (props.directory !== "~") {
    emit("update:directory", "~");
  }
}

function onBreadcrumbClick(path: string) {
  emitFocus();
  navigateTo(path);
}

function onGitBadgeClick() {
  if (currentGitInfo.value?.is_repo) {
    emit("open-git-log");
  }
}

watch(
  () => [props.box, props.directory, props.token] as const,
  async () => {
    emit("update:selected", []);
    lastClickIndex.value = null;
    await Promise.all([loadListing(), loadGitInfo()]);
  },
  { immediate: true },
);

watch(
  () => rows.value.length,
  () => {
    if (cursorIndex.value >= rows.value.length) {
      cursorIndex.value = Math.max(0, rows.value.length - 1);
    }
  },
);

defineExpose({ moveCursor, activateCurrent, goUp, goHome, toggleSelectCurrent, rows, cursorIndex, getCursorFile, selectAll, reload, openSearch });
</script>

<template>
  <section
    class="commander-pane"
    :class="{ active, inactive: !active }"
    tabindex="0"
    @focusin="emitFocus"
    @mousedown="emitFocus"
  >
    <div class="pane-header">
      <NSelect
        :value="box"
        size="tiny"
        :options="selectOptions"
        class="box-select"
        @update:value="onBoxChange"
      />

      <div class="breadcrumb" :title="directory">
        <span
          v-for="(segment, index) in crumbSegments"
          :key="segment.path"
          class="crumb"
          @click="onBreadcrumbClick(segment.path)"
        >
          <span class="crumb-text">{{ segment.label }}</span>
          <span v-if="index < crumbSegments.length - 1" class="crumb-separator">/</span>
        </span>
      </div>

      <div class="git-wrap" @click.stop="onGitBadgeClick">
        <GitBadge v-if="currentGitInfo?.is_repo" :info="currentGitInfo" compact />
      </div>
    </div>

    <div class="column-header">
      <span class="name">Name</span>
      <span class="size">Size</span>
      <span class="date">Modified</span>
      <span class="perms">Perms</span>
    </div>

    <div v-if="showFilter" class="filter-bar">
      <input
        ref="filterInputRef"
        v-model="filterQuery"
        class="filter-input"
        type="text"
        :placeholder="t('commander.filter_placeholder')"
        @keydown.escape.stop="closeSearch"
        @keydown.enter.stop
      />
      <button class="filter-close" @click="closeSearch">×</button>
    </div>

    <div ref="listRef" class="file-list" :key="`${box}-${directory}`">
      <div v-if="loading" class="empty-state">{{ t("common.loading") }}</div>
      <div v-else-if="error" class="empty-state error-state">{{ error }}</div>
      <template v-else>
        <div
          v-for="(row, index) in filteredRows"
          :key="row.key"
          class="file-row"
          :class="{ cursor: index === cursorIndex, selected: isSelected(row) }"
          :data-index="index"
          :title="row.isParent ? t('commander.parent_dir') : row.path"
          @click="(event: MouseEvent) => handleRowClick(row, index, event)"
          @dblclick="doubleClickRow(row, index)"
        >
          <NIcon size="14" class="row-icon" :class="{ 'dir-icon': row.is_directory || row.isParent }">
            <component :is="rowIcon(row)" weight="duotone" />
          </NIcon>
          <span class="name" :class="{ 'dir-name': row.is_directory || row.isParent }">{{ row.name }}</span>
          <span v-if="matchedFiles?.has(row.name)" class="match-indicator" title="Has match in other pane">≡</span>
          <span v-if="branchChangedFiles?.has(row.name)" class="branch-changed-indicator" title="Changed between branches">~</span>
          <span class="size">
            {{ rowSize(row) }}
            <span v-if="!row.isParent && !row.is_directory && matchedSizes?.has(row.name)" class="size-delta" :class="sizeDeltaClass(row)">
              {{ sizeDeltaLabel(row) }}
            </span>
          </span>
          <span class="date">{{ formatDate(row.modified) }}</span>
          <span class="perms">{{ formatPerms(row.mode) }}</span>
        </div>

        <div v-if="rows.length === 1" class="empty-state">
          {{ t("commander.no_files") }}
        </div>
      </template>
    </div>

    <div class="pane-footer">
      <span>{{ fileCountLabel }}</span>
      <span>Total {{ totalSizeLabel }}</span>
      <span>Free --</span>
    </div>
  </section>
</template>

<style scoped>
.commander-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  background: #0d0d14;
  border: 1px solid #2a2a3a;
  color: #e0e0e6;
  font-family: var(--font-mono);
  outline: none;
}

.commander-pane.active {
  border-color: #a78bfa;
}

.pane-header {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 4px 6px;
  border-bottom: 1px solid #1a1a24;
  background: #11111a;
}

.box-select {
  min-width: 0;
}

.pane-header :deep(.n-base-selection) {
  background: #0a0a0f;
  border: 1px solid #2a2a3a;
  border-radius: 0;
  box-shadow: none;
  min-height: 20px;
}

.pane-header :deep(.n-base-selection-label) {
  color: #e0e0e6;
  font-family: var(--font-mono);
  font-size: 12px;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  font-size: 12px;
}

.crumb {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  cursor: pointer;
}

.crumb:hover .crumb-text {
  color: #fbbf24;
}

.crumb-text {
  overflow: hidden;
  text-overflow: ellipsis;
}

.crumb-separator {
  color: #54546b;
  margin: 0 2px;
}

.git-wrap {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
  cursor: pointer;
}

.git-wrap:hover {
  opacity: 0.8;
}

.git-wrap :deep(.git-badge) {
  background: rgba(74, 222, 128, 0.08);
  border-color: rgba(74, 222, 128, 0.35);
  border-radius: 0;
  color: #4ade80;
}

.git-wrap :deep(.git-badge.dirty) {
  background: rgba(248, 113, 113, 0.08);
  border-color: rgba(248, 113, 113, 0.35);
  color: #f87171;
}

.git-wrap :deep(.dirty-indicator) {
  color: #f87171;
}

.column-header,
.pane-footer {
  display: flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-bottom: 1px solid #171721;
  color: #76768a;
  font-size: 11px;
  text-transform: uppercase;
}

.pane-footer {
  justify-content: space-between;
  border-top: 1px solid #171721;
  border-bottom: 0;
  color: #88889b;
  text-transform: none;
}

.file-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  color: #77778d;
  font-size: 12px;
}

.error-state {
  color: #f87171;
  padding: 12px;
  text-align: center;
}

.file-row {
  display: flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  font-size: 13px;
  font-family: var(--font-mono);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  border-left: 2px solid transparent;
}

.file-row:hover {
  background: #111118;
}

.file-row.cursor {
  background: #1a1a2e;
}

.file-row.selected {
  background: rgba(136, 58, 234, 0.25);
  border-left: 2px solid #a78bfa;
}

.file-row .name,
.column-header .name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-row .size,
.column-header .size {
  width: 80px;
  text-align: right;
  color: #888;
}

.match-indicator {
  color: #fbbf24;
  font-weight: bold;
  margin-left: 4px;
  font-size: 11px;
  flex-shrink: 0;
}

.branch-changed-indicator {
  color: #fbbf24;
  font-weight: bold;
  margin-left: 4px;
  font-size: 11px;
  flex-shrink: 0;
}

.file-row .date,
.column-header .date {
  width: 90px;
  text-align: right;
  color: #666;
}

.file-row .perms,
.column-header .perms {
  width: 80px;
  text-align: right;
  color: #555;
  font-size: 11px;
}

.row-icon {
  margin-right: 6px;
  color: #9ca3af;
  flex-shrink: 0;
}

.dir-name,
.dir-icon {
  color: #67e8f9;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: #111118;
  border-bottom: 1px solid #1a1a24;
}

.filter-input {
  flex: 1;
  background: #0a0a0f;
  border: 1px solid #2a2a3a;
  border-radius: 3px;
  color: #e0e0e6;
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 3px 8px;
  outline: none;
}

.filter-input:focus {
  border-color: #a78bfa;
}

.filter-close {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 14px;
  padding: 2px 4px;
}

.filter-close:hover {
  color: #f87171;
}

.size-delta {
  margin-left: 4px;
  font-size: 10px;
}

.size-smaller {
  color: #4ade80;
}

.size-larger {
  color: #f87171;
}

.size-equal {
  color: #555;
}

.file-list {
  animation: fadeIn 0.15s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .file-list { animation: none; }
}

@media (max-width: 767px) {
  .commander-pane { font-size: 11px; }
  .file-row .date, .column-header .date { display: none; }
  .file-row .perms, .column-header .perms { display: none; }
  .file-row .size, .column-header .size { width: 50px; }
}
</style>
