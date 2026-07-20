<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NIcon, NSpace, useMessage } from "naive-ui";
import { PhPlus, PhCopySimple, PhFloppyDisk, PhFolderOpen } from "@phosphor-icons/vue";

import CommandBar from "@/components/diff/CommandBar.vue";
import DiffCell from "@/components/diff/DiffCell.vue";
import DiffHelpOverlay from "@/components/diff/DiffHelpOverlay.vue";
import DiffNotebookDrawer from "@/components/diff/DiffNotebookDrawer.vue";
import { useDiffStore } from "@/stores/diff";
import type { DiffSide } from "@/stores/diff";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useI18n } from "@/i18n";
import { useDiffHistory } from "@/composables/useDiffHistory";
import type { Command, SideSpec } from "@/utils/diffCommandParser";
import { createDiffNotebook, getDiffNotebook } from "@/api/http";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const diffStore = useDiffStore();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const message = useMessage();
const history = useDiffHistory();

const helpOpen = ref(false);
const drawerOpen = ref(false);
const saving = ref(false);
const commandBarRef = ref<{ focus: () => void } | null>(null);

// Debounced auto-fetch per cell. Keep one timeout per cell id so editing one cell
// doesn't reset another's timer.
const fetchTimers = new Map<string, number>();
const FETCH_DEBOUNCE_MS = 400;
const URL_DEBOUNCE_MS = 400;

function scheduleFetch(idx: number) {
  const cell = diffStore.cells[idx];
  if (!cell) return;
  const prev = fetchTimers.get(cell.id);
  if (prev) window.clearTimeout(prev);
  const id = window.setTimeout(() => {
    fetchTimers.delete(cell.id);
    diffStore.fetchCell(idx, bootstrapStore.token);
  }, FETCH_DEBOUNCE_MS);
  fetchTimers.set(cell.id, id);
}

function fetchAll() {
  for (let i = 0; i < diffStore.cells.length; i++) {
    diffStore.fetchCell(i, bootstrapStore.token);
  }
}

// URL sync — debounced write so each keystroke doesn't push history.
let urlTimer: number | null = null;
function scheduleUrlWrite() {
  if (urlTimer) window.clearTimeout(urlTimer);
  urlTimer = window.setTimeout(() => {
    urlTimer = null;
    // When viewing a server-saved notebook, stay on /app/diff/n/:id; the store
    // returns an empty query so we don't echo ?n=. When the user edits, the
    // store clears serverId — and we drop back to /diff?n=<base64>.
    if (diffStore.serverId) {
      router.replace({ name: "diff-share", params: { id: diffStore.serverId }, query: {} });
      return;
    }
    const query = diffStore.toQuery();
    router.replace({ name: "diff", query });
    if (query.n) {
      history.record(query.n, summarizeNotebook());
    }
  }, URL_DEBOUNCE_MS);
}

function summarizeNotebook(): string {
  const c = diffStore.cells.length;
  const first = diffStore.cells[0];
  const sample = first?.right.config.path || first?.left.config.path || "(empty)";
  return c === 1 ? sample : t("diff.history.summary", { n: String(c), sample });
}

// Picker change → set side, schedule fetch + URL write.
function onPickerChange(idx: number, which: "left" | "right", side: DiffSide) {
  diffStore.setSide(idx, which, side);
  scheduleFetch(idx);
  scheduleUrlWrite();
}

function onRemove(idx: number) {
  diffStore.removeCell(idx);
  scheduleUrlWrite();
}

function onMoveUp(idx: number) {
  diffStore.swapCells(idx, idx - 1);
  scheduleUrlWrite();
}

function onMoveDown(idx: number) {
  diffStore.swapCells(idx, idx + 1);
  scheduleUrlWrite();
}

function onSwapSides(idx: number) {
  diffStore.swapSides(idx);
  scheduleFetch(idx);
  scheduleUrlWrite();
}

function addDiffButton() {
  const idx = diffStore.addCell();
  scheduleFetch(idx);
  scheduleUrlWrite();
}

async function copyLink() {
  const url = window.location.href;
  try {
    await navigator.clipboard.writeText(url);
    message.success(t("diff.toast.link_copied"));
  } catch {
    message.error(t("diff.toast.link_copy_failed"));
  }
}

async function saveAndShare() {
  if (saving.value) return;
  saving.value = true;
  try {
    const envelope = diffStore.currentEnvelope();
    const label = summarizeNotebook();
    const saved = await createDiffNotebook(envelope, label, bootstrapStore.token);
    diffStore.markServerSaved(saved.id);
    await router.replace({ name: "diff-share", params: { id: saved.id }, query: {} });
    // Copy the new short URL to clipboard for instant sharing.
    try {
      await navigator.clipboard.writeText(window.location.href);
      message.success(t("diff.toast.shared_and_copied"));
    } catch {
      message.success(t("diff.toast.shared"));
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    saving.value = false;
  }
}

function onLoadSavedFromDrawer(id: string) {
  router.replace({ name: "diff-share", params: { id }, query: {} });
}

function onLoadRecentFromDrawer(b64: string) {
  router.replace({ name: "diff", query: { n: b64 } });
}

// Command bar dispatch
function applyCommand(cmd: Command) {
  switch (cmd.type) {
    case "add": {
      const prefill: { left?: Partial<DiffSide> | null; right?: Partial<DiffSide> | null } = {};
      if (cmd.left) prefill.left = sideSpecToDiffSide(cmd.left);
      if (cmd.right) prefill.right = sideSpecToDiffSide(cmd.right);
      const idx = diffStore.addCell(prefill);
      scheduleFetch(idx);
      scheduleUrlWrite();
      break;
    }
    case "rm": {
      const idx = cmd.index - 1;
      if (idx < 0 || idx >= diffStore.cells.length) {
        message.error(t("diff.command.error_out_of_range", { n: String(cmd.index) }));
        return;
      }
      diffStore.removeCell(idx);
      scheduleUrlWrite();
      break;
    }
    case "swap": {
      const i = cmd.index - 1;
      if (i < 0 || i >= diffStore.cells.length) {
        message.error(t("diff.command.error_out_of_range", { n: String(cmd.index) }));
        return;
      }
      if (cmd.other === null) {
        diffStore.swapSides(i);
        scheduleFetch(i);
      } else {
        const j = cmd.other - 1;
        if (j < 0 || j >= diffStore.cells.length) {
          message.error(t("diff.command.error_out_of_range", { n: String(cmd.other) }));
          return;
        }
        diffStore.swapCells(i, j);
      }
      scheduleUrlWrite();
      break;
    }
    case "repo": {
      diffStore.setDefaultRepo({ box: cmd.box, directory: cmd.directory });
      scheduleUrlWrite();
      break;
    }
    case "clear": {
      diffStore.clearAll();
      scheduleUrlWrite();
      break;
    }
    case "help": {
      helpOpen.value = true;
      break;
    }
  }
}

function sideSpecToDiffSide(s: SideSpec): Partial<DiffSide> {
  // Empty-string segments mean "leave for prefill"; convert to undefined so
  // addCell()'s seed logic can layer on top.
  const out: Partial<DiffSide> = {};
  if (s.box) out.box = s.box;
  if (s.directory) out.directory = s.directory;
  if (s.ref) out.ref = s.ref;
  if (s.path) out.path = s.path;
  return out;
}

const defaultRepoLabel = computed<string | null>(() => {
  const def = diffStore.defaultRepo;
  if (!def) return null;
  return `${def.box || "?"}:${def.directory || "?"}`;
});

// Keyboard
function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

function onKeydown(e: KeyboardEvent) {
  if (isTypingTarget(e.target)) return;
  // Ignore modifier-chord keys; we want bare `c`, `j`, `k`, `?`.
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === "c" || e.key === ":") {
    e.preventDefault();
    commandBarRef.value?.focus();
    return;
  }
  if (e.key === "?") {
    e.preventDefault();
    helpOpen.value = !helpOpen.value;
    return;
  }
  if (e.key === "j" || e.key === "k") {
    const dir = e.key === "j" ? 1 : -1;
    e.preventDefault();
    scrollToCellNear(dir);
  }
}

function scrollToCellNear(dir: 1 | -1) {
  const items = Array.from(document.querySelectorAll<HTMLElement>("[data-cell-index]"));
  if (items.length === 0) return;
  const viewportMid = window.innerHeight / 2;
  let currentIdx = 0;
  let bestDist = Infinity;
  for (let i = 0; i < items.length; i++) {
    const r = items[i]!.getBoundingClientRect();
    const d = Math.abs(r.top + r.height / 2 - viewportMid);
    if (d < bestDist) {
      bestDist = d;
      currentIdx = i;
    }
  }
  const targetIdx = Math.min(items.length - 1, Math.max(0, currentIdx + dir));
  items[targetIdx]!.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function hydrateForCurrentRoute() {
  const params = route.params as { id?: string };
  // /app/diff/n/:id — server-loaded notebook. Path wins over query.
  if (params.id) {
    try {
      const nb = await getDiffNotebook(params.id, bootstrapStore.token);
      diffStore.hydrateFromEnvelope(nb.envelope, nb.id);
      return;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      message.error(t("diff.toast.notebook_not_found", { id: params.id }));
      // Fall through to /diff with no query so the user has a clean slate.
      await router.replace({ name: "diff", query: {} });
      void msg; // already surfaced
      return;
    }
  }
  // /app/diff?n=... or legacy ?lb=&rb=... — hydrate from query.
  const ok = diffStore.hydrateFromQuery(route.query as Record<string, string | string[] | undefined>);
  if (!ok) {
    message.error(t("diff.toast.url_decode_failed"));
    await router.replace({ name: "diff", query: {} });
  }
}

onMounted(async () => {
  if (!bootstrapStore.token) {
    await bootstrapStore.bootstrap();
  }
  if (boxesStore.items.length === 0) {
    await boxesStore.load(bootstrapStore.token);
  }
  await hydrateForCurrentRoute();
  await nextTick();
  fetchAll();
  window.addEventListener("keydown", onKeydown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown);
  for (const id of fetchTimers.values()) window.clearTimeout(id);
  fetchTimers.clear();
  if (urlTimer) window.clearTimeout(urlTimer);
});

watch(
  () => [route.name, route.params, route.query],
  async () => {
    if (route.name !== "diff" && route.name !== "diff-share") return;
    const incomingId = (route.params as { id?: string }).id ?? null;
    if (incomingId && incomingId === diffStore.serverId) return; // already loaded
    const incoming = (route.query as any).n as string | undefined;
    const current = diffStore.toQuery().n;
    if (!incomingId && incoming === current) return; // no-op
    await hydrateForCurrentRoute();
    fetchAll();
  },
  { deep: true },
);

const historyEntries = computed(() => history.list());
</script>

<template>
  <div class="diff-view">
    <header class="view-header">
      <div class="title-row">
        <h1 class="view-title">{{ t("diff.title") }}</h1>
        <p class="view-subtitle">{{ t("diff.subtitle") }}</p>
      </div>
      <NSpace>
        <NButton
          type="primary"
          size="small"
          :loading="saving"
          @click="saveAndShare"
          data-testid="diff-save-share"
          :title="t('diff.share.save_and_share_tooltip')"
        >
          <template #icon>
            <NIcon><PhFloppyDisk weight="duotone" /></NIcon>
          </template>
          {{ diffStore.serverId ? t("diff.share.save_new_version") : t("diff.share.save_and_share") }}
        </NButton>
        <NButton @click="copyLink" data-testid="diff-copy-link" size="small">
          <template #icon>
            <NIcon><PhCopySimple weight="duotone" /></NIcon>
          </template>
          {{ t("diff.action.copy_link") }}
        </NButton>
        <NButton
          size="small"
          quaternary
          @click="drawerOpen = true"
          data-testid="diff-open-saved"
          :title="t('diff.saved.title')"
        >
          <template #icon>
            <NIcon><PhFolderOpen weight="duotone" /></NIcon>
          </template>
          {{ historyEntries.length > 0 ? t("diff.saved.button_with_recent", { n: String(historyEntries.length) }) : t("diff.saved.button") }}
        </NButton>
      </NSpace>
    </header>

    <CommandBar
      ref="commandBarRef"
      :default-repo-label="defaultRepoLabel"
      @exec="applyCommand"
      @help="helpOpen = true"
    />

    <section class="cells">
      <DiffCell
        v-for="(c, i) in diffStore.cells"
        :key="c.id"
        :state="c"
        :index="i"
        :total="diffStore.cells.length"
        :language="diffStore.cellLanguage(i)"
        @update:side="(which, side) => onPickerChange(i, which, side)"
        @remove="onRemove(i)"
        @move-up="onMoveUp(i)"
        @move-down="onMoveDown(i)"
        @swap-sides="onSwapSides(i)"
      />
    </section>

    <div class="add-row">
      <NButton type="primary" ghost @click="addDiffButton" data-testid="diff-add">
        <template #icon>
          <NIcon><PhPlus weight="bold" /></NIcon>
        </template>
        {{ t("diff.action.add_diff") }}
      </NButton>
    </div>

    <DiffHelpOverlay v-model="helpOpen" />
    <DiffNotebookDrawer
      v-model="drawerOpen"
      @load-saved="onLoadSavedFromDrawer"
      @load-recent="onLoadRecentFromDrawer"
    />
  </div>
</template>

<style scoped>
.diff-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  max-width: 1600px;
  margin: 0 auto;
  width: 100%;
}

.view-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.title-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.view-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.view-subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.cells {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.add-row {
  display: flex;
  justify-content: center;
  padding: 4px 0 16px 0;
}
</style>
