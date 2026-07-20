<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  NDrawer,
  NDrawerContent,
  NButton,
  NIcon,
  NSpace,
  NSpin,
  NTag,
  NPopconfirm,
  NEmpty,
  useMessage,
} from "naive-ui";
import {
  PhClockCounterClockwise,
  PhCloudArrowDown,
  PhArrowsClockwise,
  PhTrash,
  PhFloppyDisk,
} from "@phosphor-icons/vue";

import { useBootstrapStore } from "@/stores/bootstrap";
import { useDiffHistory } from "@/composables/useDiffHistory";
import type { HistoryEntry } from "@/composables/useDiffHistory";
import {
  listDiffNotebooks,
  deleteDiffNotebook,
} from "@/api/http";
import type { DiffNotebookMeta } from "@/api/types";
import { useI18n } from "@/i18n";

const show = defineModel<boolean>({ default: false });

const emit = defineEmits<{
  (e: "load-saved", id: string): void;
  (e: "load-recent", b64: string): void;
}>();

const { t } = useI18n();
const bootstrapStore = useBootstrapStore();
const history = useDiffHistory();
const message = useMessage();

const serverNotebooks = ref<DiffNotebookMeta[]>([]);
const serverLoading = ref(false);
const serverError = ref<string | null>(null);
const recents = ref<HistoryEntry[]>([]);

function refreshRecents() {
  recents.value = history.list();
}

async function refreshServer() {
  serverLoading.value = true;
  serverError.value = null;
  try {
    const resp = await listDiffNotebooks(bootstrapStore.token);
    serverNotebooks.value = resp.notebooks;
  } catch (err) {
    serverError.value = err instanceof Error ? err.message : String(err);
  } finally {
    serverLoading.value = false;
  }
}

watch(show, (open) => {
  if (open) {
    refreshRecents();
    refreshServer();
  }
}, { immediate: true });

function fmtRelative(ts: number, isUnixSeconds = false): string {
  const nowMs = Date.now();
  const tsMs = isUnixSeconds ? ts * 1000 : ts;
  const deltaSec = Math.max(0, Math.floor((nowMs - tsMs) / 1000));
  if (deltaSec < 60) return t("diff.saved.time_now");
  if (deltaSec < 3600) return t("diff.saved.time_min", { n: String(Math.floor(deltaSec / 60)) });
  if (deltaSec < 86400) return t("diff.saved.time_hour", { n: String(Math.floor(deltaSec / 3600)) });
  return t("diff.saved.time_day", { n: String(Math.floor(deltaSec / 86400)) });
}

function loadServer(id: string) {
  emit("load-saved", id);
  show.value = false;
}

function loadRecent(b64: string) {
  emit("load-recent", b64);
  show.value = false;
}

async function deleteServer(id: string) {
  try {
    await deleteDiffNotebook(id, bootstrapStore.token);
    serverNotebooks.value = serverNotebooks.value.filter((n) => n.id !== id);
    message.success(t("diff.saved.deleted"));
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

function deleteRecent(b64: string) {
  history.remove(b64);
  refreshRecents();
}

const hasServer = computed(() => serverNotebooks.value.length > 0);
const hasRecents = computed(() => recents.value.length > 0);
const isEmpty = computed(() => !hasServer.value && !hasRecents.value && !serverLoading.value);
</script>

<template>
  <NDrawer v-model:show="show" :width="420" placement="right">
    <NDrawerContent :title="t('diff.saved.title')" closable>
      <section class="section">
        <header class="section-header">
          <h3 class="section-heading">
            <NIcon size="14"><PhFloppyDisk weight="duotone" /></NIcon>
            {{ t("diff.saved.section_server") }}
          </h3>
          <NButton
            quaternary
            circle
            size="tiny"
            :title="t('diff.saved.refresh')"
            @click="refreshServer"
          >
            <NIcon size="14"><PhArrowsClockwise weight="duotone" /></NIcon>
          </NButton>
        </header>
        <div v-if="serverLoading" class="section-loading">
          <NSpin size="small" />
        </div>
        <div v-else-if="serverError" class="section-error">
          {{ serverError }}
        </div>
        <NEmpty
          v-else-if="!hasServer"
          :description="t('diff.saved.section_server_empty')"
          size="small"
        />
        <ul v-else class="entry-list">
          <li v-for="nb in serverNotebooks" :key="nb.id" class="entry" :data-testid="`diff-saved-server-${nb.id}`">
            <div class="entry-main">
              <div class="entry-title">
                {{ nb.label || t("diff.saved.untitled") }}
              </div>
              <div class="entry-meta">
                <NTag size="tiny" type="info">{{ t("diff.saved.cells_count", { n: String(nb.cell_count) }) }}</NTag>
                <span class="entry-time">{{ fmtRelative(nb.created_at, true) }}</span>
                <code class="entry-id">{{ nb.id }}</code>
              </div>
            </div>
            <NSpace size="small">
              <NButton size="tiny" type="primary" ghost @click="loadServer(nb.id)" :data-testid="`diff-saved-load-${nb.id}`">
                <template #icon>
                  <NIcon><PhCloudArrowDown weight="duotone" /></NIcon>
                </template>
                {{ t("diff.saved.load") }}
              </NButton>
              <NPopconfirm @positive-click="deleteServer(nb.id)">
                <template #trigger>
                  <NButton size="tiny" type="error" ghost :data-testid="`diff-saved-delete-${nb.id}`">
                    <template #icon>
                      <NIcon><PhTrash weight="duotone" /></NIcon>
                    </template>
                  </NButton>
                </template>
                {{ t("diff.saved.delete_confirm", { label: nb.label || nb.id }) }}
              </NPopconfirm>
            </NSpace>
          </li>
        </ul>
      </section>

      <section v-if="hasRecents" class="section">
        <header class="section-header">
          <h3 class="section-heading">
            <NIcon size="14"><PhClockCounterClockwise weight="duotone" /></NIcon>
            {{ t("diff.saved.section_local") }}
          </h3>
          <NTag size="tiny">{{ recents.length }}</NTag>
        </header>
        <ul class="entry-list">
          <li v-for="r in recents" :key="r.b64" class="entry" :data-testid="`diff-recent-${r.b64.slice(0, 8)}`">
            <div class="entry-main">
              <div class="entry-title">{{ r.label }}</div>
              <div class="entry-meta">
                <span class="entry-time">{{ fmtRelative(r.savedAt) }}</span>
              </div>
            </div>
            <NSpace size="small">
              <NButton size="tiny" ghost @click="loadRecent(r.b64)">
                <template #icon>
                  <NIcon><PhCloudArrowDown weight="duotone" /></NIcon>
                </template>
                {{ t("diff.saved.load") }}
              </NButton>
              <NButton size="tiny" type="error" ghost @click="deleteRecent(r.b64)">
                <template #icon>
                  <NIcon><PhTrash weight="duotone" /></NIcon>
                </template>
              </NButton>
            </NSpace>
          </li>
        </ul>
      </section>

      <NEmpty
        v-if="isEmpty"
        :description="t('diff.saved.fully_empty')"
        size="medium"
      />
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.section {
  margin-bottom: 20px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.section-heading {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--muted);
}

.section-loading,
.section-error {
  padding: 8px 0;
  color: var(--muted);
  font-size: 13px;
}

.section-error {
  color: var(--danger-color, #ef4444);
}

.entry-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.entry {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--stroke);
  border-radius: 8px;
  background: var(--panel-bg);
}

.entry-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 0;
  min-width: 0;
}

.entry-title {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
}

.entry-id {
  font-family: "JetBrains Mono", monospace;
  font-size: 10px;
  background: var(--hover-overlay);
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid var(--stroke);
}

.entry-time {
  white-space: nowrap;
}
</style>
