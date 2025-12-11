<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";

import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDivider,
  NIcon,
  NInput,
  NProgress,
  NList,
  NListItem,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import {
  PhClockCounterClockwise,
  PhFile,
  PhList,
  PhFolderSimple,
  PhStar,
  PhUploadSimple,
} from "@phosphor-icons/vue";

import type { ApiBox } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useDirectoryStore } from "@/stores/directory";
import { useFilesStore } from "@/stores/files";
import { useFavoritesStore } from "@/stores/favorites";
import FavoritesPanel from "@/components/FavoritesPanel.vue";
import {
  touchFile,
  boxStatus,
} from "@/api/http";

const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const directoryStore = useDirectoryStore();
const favoritesStore = useFavoritesStore();
const filesStore = useFilesStore();
const message = useMessage();

const selectedBox = ref<string | null>(null);
const currentDir = ref("/tmp");
const newFileName = ref("");
const actionBusy = ref(false);
const uploadTarget = ref<File | null>(null);
const renameTarget = ref<string | null>(null);
const renameValue = ref("");
const moveDestination = ref("");
const copyDestination = ref("");
const copyNewName = ref("");
const status = ref<string>("unknown");
const viewFilter = ref<"all" | "files" | "dirs">("all");
const filterQuery = computed({
  get: () => directoryStore.filter,
  set: (val: string) => directoryStore.setFilter(val),
});

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({ label: `${box.name} (${box.host})`, value: box.name })),
);

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null);
const favoritesForSelection = computed(() => favoritesStore.favoritesForBox(selectedBox.value));
const isCurrentDirFavorite = computed(() =>
  favoritesStore.isFavorite(selectedBox.value, currentDir.value),
);
const filteredRows = computed(() => {
  const entries = directoryStore.listing?.entries || [];
  const q = filterQuery.value.trim().toLowerCase();
  return entries.filter((entry) => {
    if (viewFilter.value === "files" && entry.is_directory) return false;
    if (viewFilter.value === "dirs" && !entry.is_directory) return false;
    if (!q) return true;
    return entry.name.toLowerCase().includes(q);
  });
});
const recentPaths = computed(() => directoryStore.recentForBox(selectedBox.value));
const uploading = computed(() => !!filesStore.uploadFileName);

const columns = [
  {
    title: "name",
    key: "name",
    render(row: any) {
      return row.is_directory ? `📁 ${row.name}` : row.name;
    },
  },
  {
    title: "type",
    key: "is_directory",
    render(row: any) {
      return row.is_directory ? "dir" : "file";
    },
  },
  {
    title: "size",
    key: "size",
    render(row: any) {
      return row.size ?? "-";
    },
  },
  {
    title: "actions",
    key: "actions",
    render(row: any) {
      return h(
        "div",
        { style: "display:flex;gap:8px;align-items:center;" },
        [
          h(
            "button",
            {
              class: "action-link",
              onClick: () => handleRenamePrompt(row),
            },
            "rename",
          ),
          h(
            "button",
            {
              class: "action-link",
              onClick: () => removePath(row.path),
            },
            "delete",
          ),
        ],
      );
    },
  },
];

function applyBoxPatch(boxName: string, patch: Partial<ApiBox>) {
  boxesStore.setBoxes(
    boxesStore.items.map((box) => (box.name === boxName ? { ...box, ...patch } : box)),
  );
}

async function refreshFavorites(boxName: string) {
  const payload = await favoritesStore.loadBox(boxName, tokenValue.value || null);
  if (payload) {
    applyBoxPatch(boxName, { favorites: payload.favorites, pinned: payload.pinned });
  }
}

async function ensureData() {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap();
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null);
  }
  if (boxesStore.items.length) {
    favoritesStore.hydrateFromBoxes(boxesStore.items);
  }
  if (!selectedBox.value && boxesStore.items.length) {
    const firstBox = boxesStore.items[0];
    if (firstBox) {
      selectedBox.value = firstBox.name;
      await refreshFavorites(firstBox.name);
    }
  }
  if (selectedBox.value) {
    await refreshFavorites(selectedBox.value);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  }
}

onMounted(async () => {
  await ensureData();
  if (selectedBox.value) {
    const stat = await boxStatus(selectedBox.value, tokenValue.value || null);
    status.value = stat.status;
  }
});

async function reloadDir() {
  if (!selectedBox.value) return;
  await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
}

async function handlePinToggle() {
  if (!selectedBox.value) return;
  const pinned = await favoritesStore.setPinned(selectedBox.value, tokenValue.value || null);
  applyBoxPatch(selectedBox.value, { pinned });
  if (favoritesStore.error) {
    message.error(favoritesStore.error);
    return;
  }
  message.success(pinned ? "pinned box" : "unpinned box");
}

async function toggleFavoriteCurrentDir() {
  if (!selectedBox.value) return;
  const path = currentDir.value || "/";
  const desired = !favoritesStore.isFavorite(selectedBox.value, path);
  const nowFavorite = await favoritesStore.setFavorite(
    selectedBox.value,
    path,
    desired,
    tokenValue.value || null,
  );
  if (favoritesStore.error) {
    message.error(favoritesStore.error);
    return;
  }
  applyBoxPatch(selectedBox.value, {
    favorites: Array.from(favoritesForSelection.value.values()),
  });
  message.success(nowFavorite ? `favorited ${path}` : `removed ${path}`);
}

async function onBoxChange(val: string) {
  selectedBox.value = val;
  favoritesStore.hydrateFromBoxes(boxesStore.items);
  await refreshFavorites(val);
  await directoryStore.load(val, currentDir.value, tokenValue.value || null);
  await filesStore.load(val, currentDir.value, tokenValue.value || null);
  const stat = await boxStatus(val, tokenValue.value || null);
  status.value = stat.status;
}

async function createEmptyFile() {
  if (!selectedBox.value || !newFileName.value.trim()) return;
  actionBusy.value = true;
  try {
    await touchFile(
      selectedBox.value,
      currentDir.value,
      newFileName.value.trim(),
      tokenValue.value || null,
    );
    await directoryStore.load(
      selectedBox.value,
      currentDir.value,
      tokenValue.value || null,
    );
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    newFileName.value = "";
  } catch (err) {
    directoryStore.error = err instanceof Error ? err.message : String(err);
  } finally {
    actionBusy.value = false;
  }
}

async function removePath(path: string) {
  if (!selectedBox.value) return;
  actionBusy.value = true;
  try {
    await filesStore.doDelete(selectedBox.value, path, tokenValue.value || null);
    await directoryStore.load(
      selectedBox.value,
      currentDir.value,
      tokenValue.value || null,
    );
    message.success("deleted");
  } catch (err) {
    directoryStore.error = err instanceof Error ? err.message : String(err);
  } finally {
    actionBusy.value = false;
  }
}

function handleRenamePrompt(row: any) {
  renameTarget.value = row.path;
  renameValue.value = row.name;
  doRename();
}

async function doRename() {
  if (!selectedBox.value || !renameTarget.value || !renameValue.value.trim()) return;
  actionBusy.value = true;
  try {
    await filesStore.doRename(selectedBox.value, renameTarget.value, renameValue.value.trim(), tokenValue.value || null);
    await directoryStore.load(
      selectedBox.value,
      currentDir.value,
      tokenValue.value || null,
    );
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    message.success("renamed");
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function doMoveCopy(kind: "move" | "copy") {
  if (!selectedBox.value || !renameTarget.value) return;
  actionBusy.value = true;
  try {
    if (kind === "move") {
      await filesStore.doMove(selectedBox.value, renameTarget.value, moveDestination.value, tokenValue.value || null);
      message.success("moved");
    } else {
      await filesStore.doCopy(
        selectedBox.value,
        renameTarget.value,
        copyDestination.value,
        copyNewName.value || null,
        tokenValue.value || null,
      );
      message.success("copied");
    }
    await directoryStore.load(
      selectedBox.value,
      currentDir.value,
      tokenValue.value || null,
    );
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function handleUpload(files: FileList | null) {
  if (!files || !files.length || !selectedBox.value) return;
  const file = files.item(0);
  if (!file) return;
  uploadTarget.value = file;
  actionBusy.value = true;
  try {
    await filesStore.doUpload(selectedBox.value, currentDir.value, file, tokenValue.value || null);
    message.success("uploaded");
    await directoryStore.load(
      selectedBox.value,
      currentDir.value,
      tokenValue.value || null,
    );
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
    uploadTarget.value = null;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">files</p>
        <h1>File browser + editor migration</h1>
        <p class="text-muted">
          map the current htmx flows into json apis and vue views; keep upload progress, favorites, recents,
          and context menus while we phase out server-rendered templates
        </p>
      </div>
      <NButton type="primary" ghost>
        <NIcon size="16">
          <PhUploadSimple />
        </NIcon>
        <span>design api contracts</span>
      </NButton>
    </header>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18">
          <PhFolderSimple />
        </NIcon>
        <span>work items</span>
      </div>
      <NList class="list">
        <NListItem>api: /api/v1/boxes, /api/v1/boxes/{name}/ls, touch, upload, delete, rename, move, copy</NListItem>
        <NListItem>api: file preview/download endpoints that preserve current validation and error handling</NListItem>
        <NListItem>frontend: pinia store for cwd, selections, favorites, recents, filters</NListItem>
        <NListItem>frontend: dropzone + upload progress with the same chunked limits and toasts</NListItem>
        <NListItem>frontend: context menus, keyboard shortcuts, breadcrumb, and batch operations</NListItem>
      </NList>
    </NCard>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <span>live listing</span>
      </div>
      <NSpace vertical size="small">
        <NSpace size="small" align="center" wrap>
          <NSelect
            v-model:value="selectedBox"
            :options="boxOptions"
            placeholder="choose box"
            :disabled="boxesStore.loading"
            @update:value="onBoxChange"
          />
          <NInput v-model:value="currentDir" placeholder="directory" size="small" style="max-width: 320px" />
          <NButton size="small" @click="reloadDir" :disabled="!selectedBox">load</NButton>
          <NButton size="small" tertiary :disabled="!selectedBox" @click="toggleFavoriteCurrentDir">
            <NIcon size="14"><PhStar /></NIcon>
            {{ isCurrentDirFavorite ? "unfavorite dir" : "favorite dir" }}
          </NButton>
        </NSpace>
        <NSpace size="small" align="center" wrap>
          <NInput
            v-model:value="filterQuery"
            placeholder="filter names"
            size="small"
            style="max-width: 200px"
          />
          <NButton size="tiny" :type="viewFilter === 'all' ? 'primary' : 'default'" @click="viewFilter = 'all'">
            <NIcon size="14"><PhList /></NIcon>
            all
          </NButton>
          <NButton size="tiny" :type="viewFilter === 'files' ? 'primary' : 'default'" @click="viewFilter = 'files'">
            <NIcon size="14"><PhFile /></NIcon>
            files
          </NButton>
          <NButton size="tiny" :type="viewFilter === 'dirs' ? 'primary' : 'default'" @click="viewFilter = 'dirs'">
            <NIcon size="14"><PhFolderSimple /></NIcon>
            dirs
          </NButton>
          <NSpace v-if="recentPaths.length" size="small" align="center">
            <NIcon size="14"><PhClockCounterClockwise /></NIcon>
            <span class="text-muted small">recent</span>
            <NTag
              v-for="path in recentPaths"
              :key="path"
              size="small"
              checkable
              @click="currentDir = path; reloadDir();"
            >
              {{ path }}
            </NTag>
          </NSpace>
        </NSpace>
        <NAlert type="success" v-if="status === 'online'" closable>box status: {{ status }}</NAlert>
        <NAlert type="error" v-else closable>box status: {{ status }}</NAlert>
        <NSpin v-if="directoryStore.loading || boxesStore.loading" size="small">
          <span class="text-muted">loading directory…</span>
        </NSpin>
        <div
          class="drop-zone"
          @dragover.prevent
          @dragenter.prevent
          @drop.prevent="(e: DragEvent) => handleUpload(e.dataTransfer?.files || null)"
        >
          <p class="text-muted small drop-label">
            <NIcon size="14"><PhUploadSimple /></NIcon>
            <span>drop to upload into {{ currentDir }}</span>
          </p>
          <NDataTable
            :columns="columns"
            :data="filteredRows"
            size="small"
            striped
            :row-props="(row: any) => ({ onDblclick: () => removePath(row.path) })"
          />
        </div>
        <NAlert v-if="directoryStore.error || boxesStore.error" type="error" closable>
          {{ directoryStore.error || boxesStore.error }}
        </NAlert>
      </NSpace>
    </NCard>

    <FavoritesPanel
      :box="selectedBox"
      @openPath="(p: string) => { currentDir = p; reloadDir(); }"
      @togglePin="handlePinToggle"
    />

<NCard class="surface-card" size="medium">
  <div class="card-title">
    <span>actions</span>
  </div>
  <NSpace>
    <NInput v-model:value="newFileName" placeholder="new filename" size="small" style="max-width: 220px" />
    <NButton :disabled="actionBusy || !selectedBox" size="small" type="primary" ghost @click="createEmptyFile">
      <NIcon size="14"><PhUploadSimple /></NIcon>
      touch
    </NButton>
    <input type="file" @change="(e: any) => handleUpload(e.target.files)" />
  </NSpace>
      <NAlert v-if="uploading" type="info" closable>
        uploading {{ filesStore.uploadFileName }}
        <NProgress
          type="line"
          :percentage="filesStore.uploadProgress"
          status="info"
          style="margin-top: 4px; max-width: 240px"
        />
      </NAlert>
      <NAlert v-if="filesStore.uploadError" type="error" closable>{{ filesStore.uploadError }}</NAlert>
      <NSpace style="margin-top: 8px;" size="small">
        <NInput v-model:value="renameValue" placeholder="new name" size="small" style="max-width: 180px" />
        <NButton size="small" :disabled="actionBusy || !renameTarget" @click="doRename">rename target</NButton>
      </NSpace>
      <NSpace style="margin-top: 8px;" size="small">
        <NInput v-model:value="moveDestination" placeholder="move to dir" size="small" style="max-width: 200px" />
        <NButton size="small" :disabled="actionBusy || !renameTarget" @click="doMoveCopy('move')">move</NButton>
      </NSpace>
      <NSpace style="margin-top: 8px;" size="small">
        <NInput v-model:value="copyDestination" placeholder="copy to dir" size="small" style="max-width: 200px" />
        <NInput v-model:value="copyNewName" placeholder="new name (optional)" size="small" style="max-width: 200px" />
        <NButton size="small" :disabled="actionBusy || !renameTarget" @click="doMoveCopy('copy')">copy</NButton>
      </NSpace>
      <p class="text-muted small">click rename/delete in the table to set target; uploads use current directory</p>
    </NCard>

    <NDivider />

    <NAlert type="info" closable>
      preserve the resize, touch, and passive listener improvements claude shipped in file-browser.js when porting
    </NAlert>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

h1 {
  margin: 0 0 8px 0;
  font-size: 26px;
}

.surface-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--stroke);
}

.card-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 8px;
}

.list {
  color: var(--muted);
}

.action-link {
  background: transparent;
  border: 0;
  color: var(--accent);
  cursor: pointer;
  padding: 4px 6px;
}

.small {
  font-size: 12px;
}

.drop-zone {
  border: 1px dashed var(--stroke);
  border-radius: 10px;
  padding: 8px;
}

.drop-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

@media (max-width: 800px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
