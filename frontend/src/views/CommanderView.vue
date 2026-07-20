<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { batchDelete, createFolder, crossBoxTransfer, gitDiffFiles, sameBoxTransfer } from "@/api/http";
import type { TransferProgress, TransferResult } from "@/api/http";
import type { DirectoryEntry } from "@/api/types";
import type { GitBranch, GitCommit } from "@/api/types";
import CommanderBlame from "@/components/CommanderBlame.vue";
import CommanderDiffOverlay from "@/components/CommanderDiffOverlay.vue";
import CommanderGitLog from "@/components/CommanderGitLog.vue";
import CommanderHelpOverlay from "@/components/CommanderHelpOverlay.vue";
import CommanderHotbar from "@/components/CommanderHotbar.vue";
import CommanderPane from "@/components/CommanderPane.vue";
import CommanderTransferProgress from "@/components/CommanderTransferProgress.vue";
import FileEditorModal from "@/components/FileEditorModal.vue";
import { useI18n } from "@/i18n";
import { useResponsive } from "@/composables/useResponsive";
import { useAppStore } from "@/stores/app";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useFavoritesStore } from "@/stores/favorites";
import { useMessage } from "naive-ui";

const route = useRoute();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const favoritesStore = useFavoritesStore();
const appStore = useAppStore();
const message = useMessage();
const { t } = useI18n();

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null);

const activePane = ref<"left" | "right">("left");
const leftBox = ref("local");
const leftDir = ref("~");
const rightBox = ref("local");
const rightDir = ref("~");

const leftSelected = ref<string[]>([]);
const rightSelected = ref<string[]>([]);
const showDiff = ref(false);
const diffLeftPath = ref("");
const diffRightPath = ref("");
const currentMatchIndex = ref(0);

const showTransfer = ref(false);
const transferMode = ref<"copy" | "move">("copy");
const transferFiles = ref<string[]>([]);
const transferProgress = ref<TransferProgress | null>(null);
const transferResult = ref<TransferResult | null>(null);
const transferError = ref<string | null>(null);
const transferSrcBox = ref("");
const transferDestBox = ref("");
const transferDest = ref("");
let transferAbortController: AbortController | null = null;

const showDeleteConfirm = ref(false);
const deleteTargetPaths = ref<string[]>([]);

const splitPercent = ref(50);
const isDraggingSplitter = ref(false);

const showGitLog = ref(false);
const gitLogPane = ref<"left" | "right">("left");
const showBlame = ref(false);
const blameBox = ref("");
const blameDir = ref("");
const blamePath = ref("");
const branchDiffFiles = ref<Set<string>>(new Set());

const showHelp = ref(false);
const showEdit = ref(false);
const editBox = ref("");
const editPath = ref("");

const { isMobile } = useResponsive();

const leftPaneRef = ref<InstanceType<typeof CommanderPane> | null>(null);
const rightPaneRef = ref<InstanceType<typeof CommanderPane> | null>(null);

const activeRef = computed(() => (activePane.value === "left" ? leftPaneRef.value : rightPaneRef.value));

const STORAGE_KEY = "sshler:commander:config";

type CommanderRow = DirectoryEntry & { isParent?: boolean };
type MatchedPair = { leftPath: string; rightPath: string; name: string };

function isComparableFile(row: CommanderRow | null | undefined): row is DirectoryEntry {
  return Boolean(row && !row.isParent && !row.is_directory);
}

const leftFiles = computed(() => {
  const pane = leftPaneRef.value;
  const map = new Map<string, string>();
  if (!pane) return map;

  for (const row of (pane.rows ?? []) as CommanderRow[]) {
    if (isComparableFile(row)) {
      map.set(row.name, row.path);
    }
  }

  return map;
});

const rightFiles = computed(() => {
  const pane = rightPaneRef.value;
  const map = new Map<string, string>();
  if (!pane) return map;

  for (const row of (pane.rows ?? []) as CommanderRow[]) {
    if (isComparableFile(row)) {
      map.set(row.name, row.path);
    }
  }

  return map;
});

const matchedPairs = computed<MatchedPair[]>(() => {
  const pairs: MatchedPair[] = [];

  for (const [name, leftPath] of leftFiles.value) {
    const rightPath = rightFiles.value.get(name);
    if (rightPath) {
      pairs.push({ leftPath, rightPath, name });
    }
  }

  return pairs.sort((a, b) => a.name.localeCompare(b.name));
});

const leftMatchedNames = computed(() => new Set(matchedPairs.value.map((pair) => pair.name)));
const rightMatchedNames = computed(() => new Set(matchedPairs.value.map((pair) => pair.name)));

const favoritesByBox = computed(() => {
  const map = new Map<string, string[]>();
  for (const box of boxesStore.items) {
    const favorites = favoritesStore.favoritesForBox(box.name);
    if (favorites.size > 0) {
      map.set(box.name, [...favorites]);
    }
  }
  return map;
});

function getActiveContext() {
  const isLeft = activePane.value === "left";
  return {
    box: isLeft ? leftBox.value : rightBox.value,
    dir: isLeft ? leftDir.value : rightDir.value,
    selected: isLeft ? leftSelected.value : rightSelected.value,
    paneRef: isLeft ? leftPaneRef.value : rightPaneRef.value,
    otherBox: isLeft ? rightBox.value : leftBox.value,
    otherDir: isLeft ? rightDir.value : leftDir.value,
    otherPaneRef: isLeft ? rightPaneRef.value : leftPaneRef.value,
  };
}

function refreshBothPanes() {
  leftPaneRef.value?.reload();
  rightPaneRef.value?.reload();
}

function syncActiveBox() {
  appStore.activeBox = activePane.value === "left" ? leftBox.value : rightBox.value;
  appStore.activeDir = activePane.value === "left" ? leftDir.value : rightDir.value;
}

function shouldIgnoreKeyboardTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return true;
  return Boolean(target.closest(".n-base-selection") || target.closest(".n-input"));
}

function openDiff(leftPath: string, rightPath: string, matchIndex = 0) {
  diffLeftPath.value = leftPath;
  diffRightPath.value = rightPath;
  currentMatchIndex.value = Math.max(0, matchIndex);
  showDiff.value = true;
}

function toggleHelp() {
  showHelp.value = !showHelp.value;
}

function handleEdit() {
  const ctx = getActiveContext();
  const file = ctx.paneRef?.getCursorFile();
  if (!file || file.isParent || file.is_directory) {
    message.warning(t("commander.select_file_to_edit"));
    return;
  }
  editBox.value = ctx.box;
  editPath.value = file.path;
  showEdit.value = true;
}

function onFileSaved() {
  showEdit.value = false;
  const ctx = getActiveContext();
  ctx.paneRef?.reload();
}

function compareFiles() {
  const leftSelFile = leftSelected.value.length === 1 ? leftSelected.value[0] : null;
  const rightSelFile = rightSelected.value.length === 1 ? rightSelected.value[0] : null;

  if (leftSelFile && rightSelFile) {
    const idx = matchedPairs.value.findIndex(
      (pair) => pair.leftPath === leftSelFile && pair.rightPath === rightSelFile,
    );
    openDiff(leftSelFile, rightSelFile, idx);
    return;
  }

  const leftFile = leftPaneRef.value?.getCursorFile() as CommanderRow | null | undefined;
  const rightFile = rightPaneRef.value?.getCursorFile() as CommanderRow | null | undefined;

  if (isComparableFile(leftFile) && isComparableFile(rightFile)) {
    const idx = matchedPairs.value.findIndex(
      (pair) => (pair.leftPath === leftFile.path && pair.rightPath === rightFile.path) || pair.name === leftFile.name,
    );
    openDiff(leftFile.path, rightFile.path, idx);
    return;
  }

  const activePaneFile = activeRef.value?.getCursorFile() as CommanderRow | null | undefined;
  if (isComparableFile(activePaneFile)) {
    const match = matchedPairs.value.find((pair) => pair.name === activePaneFile.name);
    if (match) {
      openDiff(match.leftPath, match.rightPath, matchedPairs.value.indexOf(match));
      return;
    }
  }

  const firstMatch = matchedPairs.value[0];
  if (firstMatch) {
    openDiff(firstMatch.leftPath, firstMatch.rightPath, 0);
    return;
  }

  message.warning(t("commander.no_matches"));
}

function handleNavigatePair(index: number) {
  const pair = matchedPairs.value[index];
  if (!pair) return;
  openDiff(pair.leftPath, pair.rightPath, index);
}

async function startTransfer(mode: "copy" | "move") {
  const ctx = getActiveContext();
  let paths = [...ctx.selected];

  if (paths.length === 0) {
    const cursorFile = ctx.paneRef?.getCursorFile() as CommanderRow | null | undefined;
    if (cursorFile && !cursorFile.isParent && !cursorFile.is_directory) {
      paths = [cursorFile.path];
    }
  }

  if (paths.length === 0) {
    message.warning(t("commander.no_selection"));
    return;
  }

  if (ctx.box === ctx.otherBox && ctx.dir === ctx.otherDir) {
    message.warning(t("commander.same_directory"));
    return;
  }

  transferMode.value = mode;
  transferFiles.value = paths;
  transferProgress.value = null;
  transferResult.value = null;
  transferError.value = null;
  transferSrcBox.value = ctx.box;
  transferDestBox.value = ctx.otherBox;
  transferDest.value = ctx.otherDir;
  transferAbortController = null;
  showTransfer.value = true;

  if (ctx.box === ctx.otherBox) {
    try {
      const result = await sameBoxTransfer(ctx.box, paths, ctx.otherDir, mode, tokenValue.value);
      transferResult.value = {
        succeeded: result.succeeded,
        failed: result.failed,
      };
      refreshBothPanes();
    } catch (err) {
      transferError.value = err instanceof Error ? err.message : String(err);
    }
    return;
  }

  transferAbortController = crossBoxTransfer(
    ctx.box,
    ctx.otherBox,
    paths,
    ctx.otherDir,
    mode,
    tokenValue.value,
    (progress) => {
      transferProgress.value = progress;
    },
    (result) => {
      transferResult.value = result;
      transferAbortController = null;
      refreshBothPanes();
    },
    (error) => {
      transferError.value = error;
      transferAbortController = null;
    },
  );
}

function cancelTransfer() {
  transferAbortController?.abort();
  transferAbortController = null;
  transferError.value = "Cancelled";
}

function closeTransfer() {
  showTransfer.value = false;
  transferAbortController = null;
}

async function handleMkdir() {
  const ctx = getActiveContext();
  const name = window.prompt(t("commander.mkdir_prompt"));
  if (!name?.trim()) return;

  try {
    await createFolder(ctx.box, ctx.dir, name.trim(), tokenValue.value);
    ctx.paneRef?.reload();
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

async function handleDelete() {
  const ctx = getActiveContext();
  let paths = [...ctx.selected];

  if (paths.length === 0) {
    const cursorFile = ctx.paneRef?.getCursorFile() as CommanderRow | null | undefined;
    if (cursorFile && !cursorFile.isParent) {
      paths = [cursorFile.path];
    }
  }

  if (paths.length === 0) return;

  deleteTargetPaths.value = paths;
  showDeleteConfirm.value = true;
  const confirmed =
    showDeleteConfirm.value && window.confirm(t("commander.delete_confirm", { count: deleteTargetPaths.value.length }));
  showDeleteConfirm.value = false;
  if (!confirmed) {
    deleteTargetPaths.value = [];
    return;
  }

  try {
    await batchDelete(ctx.box, deleteTargetPaths.value, tokenValue.value);
    if (activePane.value === "left") {
      leftSelected.value = [];
    } else {
      rightSelected.value = [];
    }
    deleteTargetPaths.value = [];
    ctx.paneRef?.reload();
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

function onOpenGitLog(pane: "left" | "right") {
  gitLogPane.value = pane;
  showGitLog.value = true;
}

function onSelectCommit(commit: GitCommit) {
  showGitLog.value = false;
  message.info(`Commit: ${commit.short_hash} — ${commit.message}`, { duration: 5000 });
}

function onSelectBranch(branch: GitBranch) {
  showGitLog.value = false;
  message.info(`Branch: ${branch.name}`, { duration: 3000 });
}

function onOpenBlame(pane: "left" | "right") {
  const isLeft = pane === "left";
  const box = isLeft ? leftBox.value : rightBox.value;
  const dir = isLeft ? leftDir.value : rightDir.value;
  const paneRef = isLeft ? leftPaneRef.value : rightPaneRef.value;
  const file = paneRef?.getCursorFile();
  if (!file || file.isParent || file.is_directory) {
    message.warning(t("commander.select_file_for_blame"));
    return;
  }
  blameBox.value = box;
  blameDir.value = dir;
  blamePath.value = file.path;
  showBlame.value = true;
}

function handleHotbarAction(key: string) {
  switch (key) {
    case "F1":
      toggleHelp();
      break;
    case "F3":
      compareFiles();
      break;
    case "F4":
      handleEdit();
      break;
    case "F5":
      void startTransfer("copy");
      break;
    case "F6":
      void startTransfer("move");
      break;
    case "F7":
      void handleMkdir();
      break;
    case "F8":
      void handleDelete();
      break;
    case "F10":
      window.history.back();
      break;
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (shouldIgnoreKeyboardTarget(event.target)) return;
  if (showDiff.value || showTransfer.value || showGitLog.value || showBlame.value || showHelp.value || showEdit.value) return;

  switch (event.key) {
    case "g":
      if (event.ctrlKey) {
        event.preventDefault();
        onOpenGitLog(activePane.value);
      }
      break;
    case "b":
      if (event.ctrlKey) {
        event.preventDefault();
        onOpenBlame(activePane.value);
      }
      break;
    case "F1":
      event.preventDefault();
      toggleHelp();
      break;
    case "Tab":
      event.preventDefault();
      activePane.value = activePane.value === "left" ? "right" : "left";
      break;
    case "ArrowUp":
      event.preventDefault();
      activeRef.value?.moveCursor(-1);
      break;
    case "ArrowDown":
      event.preventDefault();
      activeRef.value?.moveCursor(1);
      break;
    case "Enter":
      event.preventDefault();
      activeRef.value?.activateCurrent();
      break;
    case "Backspace":
      event.preventDefault();
      activeRef.value?.goUp();
      break;
    case "Home":
      event.preventDefault();
      activeRef.value?.goHome();
      break;
    case " ":
      event.preventDefault();
      activeRef.value?.toggleSelectCurrent();
      break;
    case "F3":
      event.preventDefault();
      compareFiles();
      break;
    case "F4":
      event.preventDefault();
      handleEdit();
      break;
    case "f":
      if (event.ctrlKey) {
        event.preventDefault();
        activeRef.value?.openSearch();
      }
      break;
    case "F5":
      event.preventDefault();
      void startTransfer("copy");
      break;
    case "F6":
      event.preventDefault();
      void startTransfer("move");
      break;
    case "F7":
      event.preventDefault();
      void handleMkdir();
      break;
    case "F8":
      event.preventDefault();
      void handleDelete();
      break;
    case "*":
      event.preventDefault();
      activeRef.value?.selectAll();
      break;
    case "F10":
      event.preventDefault();
      window.history.back();
      break;
  }
}

function startSplitterDrag(event: MouseEvent) {
  const splitter = event.currentTarget as HTMLElement | null;
  const container = splitter?.parentElement;
  if (!container) return;

  isDraggingSplitter.value = true;
  document.body.style.userSelect = "none";
  document.body.style.cursor = "col-resize";

  const startX = event.clientX;
  const startPercent = splitPercent.value;
  const width = container.offsetWidth;

  const onMove = (moveEvent: MouseEvent) => {
    const delta = moveEvent.clientX - startX;
    const nextPercent = startPercent + (delta / width) * 100;
    splitPercent.value = Math.max(20, Math.min(80, nextPercent));
  };

  const onUp = () => {
    isDraggingSplitter.value = false;
    document.body.style.userSelect = "";
    document.body.style.cursor = "";
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  };

  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function saveConfig() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      leftBox: leftBox.value,
      leftDir: leftDir.value,
      rightBox: rightBox.value,
      rightDir: rightDir.value,
      splitPercent: splitPercent.value,
    }),
  );
}

function loadConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Partial<{
      leftBox: string;
      leftDir: string;
      rightBox: string;
      rightDir: string;
      splitPercent: number;
    }>;

    if (parsed.leftBox) leftBox.value = parsed.leftBox;
    if (parsed.leftDir) leftDir.value = parsed.leftDir;
    if (parsed.rightBox) rightBox.value = parsed.rightBox;
    if (parsed.rightDir) rightDir.value = parsed.rightDir;
    if (typeof parsed.splitPercent === "number") splitPercent.value = parsed.splitPercent;
  } catch {
    // Ignore invalid persisted config.
  }
}

async function ensureData() {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value);
  }
  if (boxesStore.items.length) {
    favoritesStore.hydrateFromBoxes(boxesStore.items);
  }
}

watch([leftBox, leftDir, rightBox, rightDir, splitPercent], saveConfig);
watch([leftBox, leftDir, rightBox, rightDir, activePane], syncActiveBox, { immediate: true });

onMounted(async () => {
  document.title = `${t("commander.title")} - sshler`;

  await ensureData();
  loadConfig();

  const boxQuery = typeof route.query.box === "string" ? route.query.box : null;
  const dirQuery = typeof route.query.dir === "string" ? route.query.dir : null;

  if (boxQuery) leftBox.value = boxQuery;
  if (dirQuery) leftDir.value = dirQuery;

  syncActiveBox();
  document.addEventListener("keydown", handleKeydown);
});

onUnmounted(() => {
  transferAbortController?.abort();
  transferAbortController = null;
  document.removeEventListener("keydown", handleKeydown);
  document.body.style.userSelect = "";
  document.body.style.cursor = "";
});
</script>

<template>
  <div class="commander">
    <div v-if="isMobile" class="mobile-pane-toggle">
      <button :class="{ active: activePane === 'left' }" @click="activePane = 'left'">{{ t("commander.left_pane") }}</button>
      <button :class="{ active: activePane === 'right' }" @click="activePane = 'right'">{{ t("commander.right_pane") }}</button>
    </div>

    <div class="panes" :class="{ dragging: isDraggingSplitter, mobile: isMobile }">
      <div class="pane-wrapper" :style="isMobile ? {} : { width: `${splitPercent}%` }" v-show="!isMobile || activePane === 'left'">
        <CommanderPane
          ref="leftPaneRef"
          v-model:box="leftBox"
          v-model:directory="leftDir"
          v-model:selected="leftSelected"
          :active="activePane === 'left'"
          :token="tokenValue"
          :box-options="boxesStore.items"
          :favorites="favoritesByBox"
          :matched-files="leftMatchedNames"
          :branch-changed-files="branchDiffFiles"
          @focus="activePane = 'left'"
          @open-git-log="onOpenGitLog('left')"
          @open-blame="(path: string) => { blameBox = leftBox; blameDir = leftDir; blamePath = path; showBlame = true; }"
        />
      </div>

      <div v-if="!isMobile" class="splitter" @mousedown="startSplitterDrag">
        <div class="splitter-handle" />
      </div>

      <div class="pane-wrapper" :style="isMobile ? {} : { width: `${100 - splitPercent}%` }" v-show="!isMobile || activePane === 'right'">
        <CommanderPane
          ref="rightPaneRef"
          v-model:box="rightBox"
          v-model:directory="rightDir"
          v-model:selected="rightSelected"
          :active="activePane === 'right'"
          :token="tokenValue"
          :box-options="boxesStore.items"
          :favorites="favoritesByBox"
          :matched-files="rightMatchedNames"
          :branch-changed-files="branchDiffFiles"
          @focus="activePane = 'right'"
          @open-git-log="onOpenGitLog('right')"
          @open-blame="(path: string) => { blameBox = rightBox; blameDir = rightDir; blamePath = path; showBlame = true; }"
        />
      </div>
    </div>

    <CommanderHotbar @action="handleHotbarAction" />

    <CommanderDiffOverlay
      v-model:show="showDiff"
      :left-box="leftBox"
      :right-box="rightBox"
      :left-path="diffLeftPath"
      :right-path="diffRightPath"
      :token="tokenValue"
      :matched-pairs="matchedPairs"
      :current-pair-index="currentMatchIndex"
      @navigate-pair="handleNavigatePair"
    />

    <CommanderTransferProgress
      :show="showTransfer"
      :mode="transferMode"
      :src-box="transferSrcBox"
      :dest-box="transferDestBox"
      :destination="transferDest"
      :files="transferFiles"
      :progress="transferProgress"
      :result="transferResult"
      :error="transferError"
      @cancel="cancelTransfer"
      @close="closeTransfer"
    />

    <CommanderGitLog
      v-model:show="showGitLog"
      :box="gitLogPane === 'left' ? leftBox : rightBox"
      :directory="gitLogPane === 'left' ? leftDir : rightDir"
      :token="tokenValue"
      @select-commit="onSelectCommit"
      @select-branch="onSelectBranch"
    />

    <CommanderBlame
      v-model:show="showBlame"
      :box="blameBox"
      :directory="blameDir"
      :path="blamePath"
      :token="tokenValue"
    />

    <CommanderHelpOverlay v-model:show="showHelp" />

    <FileEditorModal
      :show="showEdit"
      :box="editBox"
      :path="editPath"
      :token="tokenValue"
      :theme="appStore.isDark ? 'dark' : 'light'"
      @update:show="(v) => showEdit = v"
      @saved="onFileSaved"
    />
  </div>
</template>

<style scoped>
.commander {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0a0a0f;
  font-family: var(--font-mono);
  color: #e0e0e6;
  overflow: hidden;
}

.panes {
  display: flex;
  flex: 1;
  min-height: 0;
}

.panes.dragging {
  user-select: none;
}

.pane-wrapper {
  min-width: 0;
  overflow: hidden;
  display: flex;
}

.pane-wrapper > * {
  flex: 1;
  min-width: 0;
}

.splitter {
  width: 4px;
  cursor: col-resize;
  background: #1a1a24;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.splitter-handle {
  width: 1px;
  height: 40%;
  background: #a78bfa;
  border-radius: 1px;
}

.splitter:hover .splitter-handle {
  background: #c4b5fd;
  width: 2px;
}

.mobile-pane-toggle {
  display: flex; gap: 0; padding: 0; background: #0a0a0f;
  border-bottom: 1px solid #1a1a24;
}
.mobile-pane-toggle button {
  flex: 1; padding: 6px; background: #0d0d14; border: none;
  color: #888; font-family: var(--font-mono); font-size: 12px; cursor: pointer;
}
.mobile-pane-toggle button.active {
  color: #a78bfa; border-bottom: 2px solid #a78bfa; background: #111118;
}
.panes.mobile { flex-direction: column; }
.panes.mobile .pane-wrapper { width: 100% !important; flex: 1; }
</style>
