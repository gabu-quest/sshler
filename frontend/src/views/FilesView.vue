<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useResponsive } from "@/composables/useResponsive";
import {
  NAlert, NButton, NCard, NDataTable, NIcon, NInput, NProgress, NModal, NSelect, NSpace, NSpin, NTag, NSwitch, NTooltip, useMessage,
} from "naive-ui";
import {
  PhClockCounterClockwise, PhFile, PhList, PhFolderSimple, PhStar, PhUploadSimple, PhEye, PhPencil, PhDownloadSimple, PhTextAa, PhCopy, PhClipboard, PhTrash, PhFolder, PhArrowLeft, PhHouse, PhMagnifyingGlass, PhGear, PhTerminalWindow,
} from "@phosphor-icons/vue";

import type { ApiBox, FilePreview } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useDirectoryStore } from "@/stores/directory";
import { useFilesStore } from "@/stores/files";
import { useFavoritesStore } from "@/stores/favorites";
import FavoritesPanel from "@/components/FavoritesPanel.vue";
import CodeEditor from "@/components/CodeEditor.vue";
import ContextMenu from "@/components/ContextMenu.vue";
import DirectorySearchInput from "@/components/DirectorySearchInput.vue";
import { touchFile, boxStatus, fetchFilePreview, downloadFile, writeFile } from "@/api/http";
import { setEmojiFavicon, resetFavicon } from "@/utils/emoji-favicon";

const route = useRoute();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const directoryStore = useDirectoryStore();
const favoritesStore = useFavoritesStore();
const filesStore = useFilesStore();
const message = useMessage();
const { isMobile } = useResponsive();

// State
const selectedBox = ref<string | null>(null);
const currentDir = ref("/");
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
const previewing = ref(false);
const previewPath = ref("");
const previewContent = ref("");
const previewMeta = ref<FilePreview | null>(null);
const previewLoading = ref(false);
const editing = ref(false);
const editPath = ref("");
const editContent = ref("");
const editLoading = ref(false);
const showLineNumbers = ref(true);
const wordWrap = ref(true);
const editorTheme = ref<'light' | 'dark'>('dark');
const contextMenu = ref({ visible: false, x: 0, y: 0, targetFile: null as any });
const dragCounter = ref(0);
const isDragOver = ref(false);

// Computed
const filterQuery = computed({
  get: () => directoryStore.filter,
  set: (val: string) => directoryStore.setFilter(val),
});

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({ label: `${box.name} (${box.host})`, value: box.name })),
);

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null);
const favoritesForSelection = computed(() => favoritesStore.favoritesForBox(selectedBox.value));
const isCurrentDirFavorite = computed(() => favoritesStore.isFavorite(selectedBox.value, currentDir.value));
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
const previewIsImage = computed(() => !!(previewMeta.value?.image_data && previewMeta.value?.image_mime && !previewMeta.value?.image_too_large));
const selectedPaths = computed({
  get: () => filesStore.selectedFiles,
  set: (val: string[]) => filesStore.setSelectedFiles(val),
});
const selectedCount = computed(() => selectedPaths.value.length);
const recentPaths = computed(() => directoryStore.recentForBox(selectedBox.value));
const uploading = computed(() => !!filesStore.uploadFileName);

// Display name for current directory (last component)
const displayDirName = computed(() => {
  if (!currentDir.value || currentDir.value === '/' || currentDir.value === '~') {
    return currentDir.value === '~' ? 'Home' : 'Root';
  }
  const parts = currentDir.value.split('/').filter(Boolean);
  return parts[parts.length - 1] || 'Root';
});

// Update browser tab title and favicon
watch([selectedBox, currentDir], () => {
  if (selectedBox.value) {
    document.title = `${displayDirName.value} — ${selectedBox.value}`;
    setEmojiFavicon(`${selectedBox.value}:${currentDir.value}`);
  } else {
    document.title = 'Files';
    resetFavicon();
  }
}, { immediate: true });

// Utility functions
const getFileType = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase();
  const types: Record<string, string> = {
    'js': 'JavaScript', 'ts': 'TypeScript', 'py': 'Python', 'html': 'HTML', 'css': 'CSS', 'json': 'JSON', 'md': 'Markdown', 'txt': 'Text', 'log': 'Log', 'yml': 'YAML', 'yaml': 'YAML', 'xml': 'XML', 'svg': 'SVG', 'png': 'Image', 'jpg': 'Image', 'jpeg': 'Image', 'gif': 'Image', 'pdf': 'PDF', 'zip': 'Archive', 'tar': 'Archive', 'gz': 'Archive'
  };
  return types[ext || ''] || 'File';
};

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const getLanguageFromFilename = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    'js': 'javascript', 'jsx': 'javascript', 'ts': 'javascript', 'tsx': 'javascript', 'py': 'python', 'html': 'html', 'htm': 'html', 'css': 'css', 'scss': 'css', 'sass': 'css', 'json': 'json', 'md': 'markdown', 'xml': 'xml', 'svg': 'xml'
  };
  return langMap[ext || ''] || 'text';
};

const navigateToDirectory = async (path: string) => {
  currentDir.value = path;
  await reloadDir();
};

const navigateUp = async () => {
  const parentDir = currentDir.value.split('/').slice(0, -1).join('/') || '/';
  await navigateToDirectory(parentDir);
};

const navigateHome = async () => {
  await navigateToDirectory('~');
};

// Context menu functions
const showContextMenu = (event: MouseEvent, file: any) => {
  event.preventDefault();
  event.stopPropagation();
  contextMenu.value = { visible: true, x: event.clientX, y: event.clientY, targetFile: file };
};

const getContextMenuItems = () => {
  const file = contextMenu.value.targetFile;
  if (!file) return [];
  const items = [];
  if (file.is_directory) {
    items.push({ id: 'open', label: 'Open', icon: PhFolder });
  } else {
    items.push({ id: 'preview', label: 'Preview', icon: PhEye }, { id: 'edit', label: 'Edit', icon: PhPencil }, { id: 'download', label: 'Download', icon: PhDownloadSimple });
  }
  items.push({ separator: true, id: 'sep1', label: '' }, { id: 'rename', label: 'Rename', icon: PhTextAa }, { id: 'copy', label: 'Copy', icon: PhCopy }, { id: 'copy-path', label: 'Copy Path', icon: PhClipboard }, { id: 'favorite', label: file.is_directory ? 'Add to Favorites' : 'Favorite Directory', icon: PhStar }, { separator: true, id: 'sep2', label: '' }, { id: 'delete', label: 'Delete', icon: PhTrash, danger: true });
  return items;
};

const handleContextMenuSelect = async (itemId: string) => {
  const file = contextMenu.value.targetFile;
  if (!file) return;
  switch (itemId) {
    case 'open': if (file.is_directory) await navigateToDirectory(file.path); break;
    case 'preview': await handlePreview(file); break;
    case 'edit': await handleEdit(file); break;
    case 'download': await handleDownload(file); break;
    case 'rename': handleRenamePrompt(file); break;
    case 'copy': renameTarget.value = file.path; copyDestination.value = currentDir.value; copyNewName.value = `${file.name}_copy`; break;
    case 'copy-path': await navigator.clipboard.writeText(file.path); message.success('Path copied to clipboard'); break;
    case 'favorite': if (file.is_directory) await toggleFavoriteDir(file.path); else await toggleFavoriteCurrentDir(); break;
    case 'delete': await removePath(file.path); break;
  }
  contextMenu.value.visible = false;
};

const toggleFavoriteDir = async (path: string) => {
  if (!selectedBox.value) return;
  const desired = !favoritesStore.isFavorite(selectedBox.value, path);
  const nowFavorite = await favoritesStore.setFavorite(selectedBox.value, path, desired, tokenValue.value || null);
  if (favoritesStore.error) { message.error(favoritesStore.error); return; }
  applyBoxPatch(selectedBox.value, { favorites: Array.from(favoritesForSelection.value.values()) });
  message.success(nowFavorite ? `Favorited ${path}` : `Removed ${path}`);
};

// Drag and drop functions
const handleDragEnter = (e: DragEvent) => { e.preventDefault(); dragCounter.value++; if (dragCounter.value === 1) isDragOver.value = true; };
const handleDragLeave = (e: DragEvent) => { e.preventDefault(); dragCounter.value--; if (dragCounter.value === 0) isDragOver.value = false; };
const handleDragOver = (e: DragEvent) => { e.preventDefault(); };
const handleDrop = async (e: DragEvent) => { e.preventDefault(); dragCounter.value = 0; isDragOver.value = false; const files = e.dataTransfer?.files; if (files && files.length > 0) await handleUpload(files); };

// Row click handler for file selection (extracted to prevent recreating closures on every render)
const handleRowClick = (row: any, e: MouseEvent) => {
  if (e.ctrlKey || e.metaKey) {
    const isSelected = selectedPaths.value.includes(row.path);
    if (isSelected) {
      filesStore.setSelectedFiles(selectedPaths.value.filter((p: string) => p !== row.path));
    } else {
      filesStore.setSelectedFiles([...selectedPaths.value, row.path]);
    }
  } else if (e.shiftKey && selectedPaths.value.length > 0) {
    const lastSelected = selectedPaths.value[selectedPaths.value.length - 1];
    const lastIndex = filteredRows.value.findIndex((r: any) => r.path === lastSelected);
    const currentIndex = filteredRows.value.findIndex((r: any) => r.path === row.path);
    const start = Math.min(lastIndex, currentIndex);
    const end = Math.max(lastIndex, currentIndex);
    const rangeSelection = filteredRows.value.slice(start, end + 1).map((r: any) => r.path);
    filesStore.setSelectedFiles([...new Set([...selectedPaths.value, ...rangeSelection])]);
  }
};

// Row props factory for NDataTable (stable reference to avoid recreating on every render)
const getRowProps = (row: any) => ({
  style: 'cursor: pointer;',
  onClick: (e: MouseEvent) => handleRowClick(row, e)
});

function applyBoxPatch(boxName: string, patch: Partial<ApiBox>) {
  boxesStore.setBoxes(boxesStore.items.map((box) => (box.name === boxName ? { ...box, ...patch } : box)));
}

async function refreshFavorites(boxName: string) {
  const payload = await favoritesStore.loadBox(boxName, tokenValue.value || null);
  if (payload) applyBoxPatch(boxName, { favorites: payload.favorites, pinned: payload.pinned });
}

async function ensureData() {
  if (!bootstrapStore.payload && !bootstrapStore.loading) await bootstrapStore.bootstrap();
  if (!boxesStore.items.length && !boxesStore.loading) await boxesStore.load(tokenValue.value || null);
  if (boxesStore.items.length) favoritesStore.hydrateFromBoxes(boxesStore.items);

  // Read box and path from URL query params
  const boxFromUrl = route.query.box as string | undefined;
  const pathFromUrl = route.query.path as string | undefined;

  if (boxFromUrl && boxesStore.items.some(b => b.name === boxFromUrl)) {
    selectedBox.value = boxFromUrl;
    if (pathFromUrl) {
      currentDir.value = pathFromUrl;
    } else {
      const box = boxesStore.items.find(b => b.name === boxFromUrl);
      if (box?.default_dir) currentDir.value = box.default_dir;
    }
  } else if (!selectedBox.value && boxesStore.items.length) {
    const firstBox = boxesStore.items[0];
    if (firstBox) {
      selectedBox.value = firstBox.name;
      if (firstBox.default_dir) {
        currentDir.value = firstBox.default_dir;
      }
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
  if (favoritesStore.error) { message.error(favoritesStore.error); return; }
  message.success(pinned ? "pinned box" : "unpinned box");
}

async function toggleFavoriteCurrentDir() {
  if (!selectedBox.value) return;
  const path = currentDir.value || "/";
  const desired = !favoritesStore.isFavorite(selectedBox.value, path);
  const nowFavorite = await favoritesStore.setFavorite(selectedBox.value, path, desired, tokenValue.value || null);
  if (favoritesStore.error) { message.error(favoritesStore.error); return; }
  applyBoxPatch(selectedBox.value, { favorites: Array.from(favoritesForSelection.value.values()) });
  message.success(nowFavorite ? `favorited ${path}` : `removed ${path}`);
}

async function onBoxChange(val: string) {
  selectedBox.value = val;
  const box = boxesStore.items.find((b) => b.name === val);
  if (box?.default_dir) {
    currentDir.value = box.default_dir;
  }
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
    await touchFile(selectedBox.value, currentDir.value, newFileName.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    newFileName.value = "";
    message.success("File created");
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
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
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
  // Auto-trigger rename dialog or inline editing
}

async function doRename() {
  if (!selectedBox.value || !renameTarget.value || !renameValue.value.trim()) return;
  actionBusy.value = true;
  try {
    await filesStore.doRename(selectedBox.value, renameTarget.value, renameValue.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    message.success("renamed");
    renameTarget.value = null;
    renameValue.value = "";
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
      await filesStore.doCopy(selectedBox.value, renameTarget.value, copyDestination.value, copyNewName.value || null, tokenValue.value || null);
      message.success("copied");
    }
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    renameTarget.value = null;
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
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
    uploadTarget.value = null;
  }
}

async function handlePreview(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  previewContent.value = "";
  previewMeta.value = null;
  try {
    previewing.value = true;
    previewLoading.value = true;
    previewPath.value = row.path;
    const payload = await fetchFilePreview(selectedBox.value, row.path, tokenValue.value || null);
    previewMeta.value = payload;
    previewContent.value = payload.content || "";
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
    previewing.value = false;
  } finally {
    previewLoading.value = false;
  }
}

function closePreview() {
  previewing.value = false;
  previewPath.value = "";
  previewContent.value = "";
  previewMeta.value = null;
  previewLoading.value = false;
}

async function handleDownload(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  try {
    const blob = await downloadFile(selectedBox.value, row.path, tokenValue.value || null);
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = row.name || "download";
    anchor.click();
    URL.revokeObjectURL(objectUrl);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

async function handleEdit(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  editing.value = true;
  editPath.value = row.path;
  editContent.value = "";
  editLoading.value = true;
  try {
    const payload = await fetchFilePreview(selectedBox.value, row.path, tokenValue.value || null);
    editContent.value = payload.content || "";
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
    editing.value = false;
  } finally {
    editLoading.value = false;
  }
}

async function saveEdit() {
  if (!selectedBox.value || !editPath.value) return;
  editLoading.value = true;
  try {
    await writeFile(selectedBox.value, editPath.value, editContent.value, tokenValue.value || null);
    message.success("saved");
    editing.value = false;
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    editLoading.value = false;
  }
}

async function bulkDelete() {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  actionBusy.value = true;
  try {
    for (const path of selectedPaths.value) {
      await filesStore.doDelete(selectedBox.value, path, tokenValue.value || null);
    }
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    message.success("deleted selection");
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function bulkDownload() {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  for (const path of selectedPaths.value) {
    try {
      const blob = await downloadFile(selectedBox.value, path, tokenValue.value || null);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = path.split('/').pop() || "download";
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      message.error(`Failed to download ${path}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}

// Mobile-friendly actions button that opens context menu
const mobileActionsButton = (row: any) => {
  return h(NButton, {
    size: "tiny",
    quaternary: true,
    onClick: (e: MouseEvent) => showContextMenu(e, row)
  }, { default: () => '...' });
};

// Table columns — responsive: on mobile show only name + actions
const columns = computed(() => {
  const nameCol = {
    title: "name", key: "name",
    render(row: any) {
      const isFav = row.is_directory && favoritesStore.isFavorite(selectedBox.value, row.path);
      const children = [
        h(NIcon, { size: 16 }, () => h(row.is_directory ? PhFolderSimple : PhFile)),
        h("div", { style: "min-width:0;flex:1;" }, [
          h("span", row.name),
          isMobile.value && !row.is_directory && row.size
            ? h("div", { style: "font-size:11px;color:var(--muted);" }, formatFileSize(row.size))
            : null,
        ]),
        isFav && h(NIcon, { size: 14, color: "#faad14", style: "margin-left: 4px;flex-shrink:0;" }, () => h(PhStar, { weight: "fill" }))
      ];
      return h("div", {
        style: "display:flex;align-items:center;gap:8px;cursor:pointer;min-width:0;",
        onDblclick: () => row.is_directory ? navigateToDirectory(row.path) : handlePreview(row),
        onContextmenu: (e: MouseEvent) => showContextMenu(e, row)
      }, children);
    },
  };

  if (isMobile.value) {
    return [
      { type: "selection" as const },
      nameCol,
      {
        title: "", key: "actions", width: 48,
        render(row: any) { return mobileActionsButton(row); }
      }
    ];
  }

  return [
    { type: "selection" as const },
    nameCol,
    { title: "type", key: "is_directory", render(row: any) { return row.is_directory ? "directory" : getFileType(row.name); } },
    { title: "size", key: "size", render(row: any) { return row.size ? formatFileSize(row.size) : "-"; } },
    { title: "modified", key: "modified", render(row: any) { return row.modified ? new Date(row.modified).toLocaleDateString() : "-"; } },
    {
      title: "actions", key: "actions",
      render(row: any) {
        const isFav = row.is_directory && favoritesStore.isFavorite(selectedBox.value, row.path);
        return h("div", { style: "display:flex;gap:4px;align-items:center;" }, [
          row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, {
              size: "tiny",
              quaternary: true,
              onClick: () => toggleFavoriteDir(row.path)
            }, { icon: () => h(NIcon, { size: 14, color: isFav ? "#faad14" : undefined }, () => h(PhStar, { weight: isFav ? "fill" : "regular" })) }),
            default: () => isFav ? "Remove from Favorites" : "Add to Favorites"
          }),
          row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, {
              size: "tiny",
              quaternary: true,
              onClick: () => window.open(`/app/terminal?box=${encodeURIComponent(selectedBox.value || '')}&dir=${encodeURIComponent(row.path)}`, '_blank')
            }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTerminalWindow)) }),
            default: () => "Open Terminal Here"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handlePreview(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhEye)) }),
            default: () => "Preview"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleEdit(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhPencil)) }),
            default: () => "Edit"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleDownload(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhDownloadSimple)) }),
            default: () => "Download"
          }),
          h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleRenamePrompt(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTextAa)) }),
            default: () => "Rename"
          }),
          h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", onClick: () => removePath(row.path) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTrash)) }),
            default: () => "Delete"
          }),
        ]);
      },
    },
  ];
});
</script>
<template>
  <div class="page">
    <!-- Breadcrumb Navigation -->
    <div class="breadcrumb-nav">
      <NSpace size="small" align="center">
        <NButton size="small" quaternary @click="navigateHome" title="Home">
          <NIcon size="16"><PhHouse /></NIcon>
        </NButton>
        <NButton size="small" quaternary @click="navigateUp" :disabled="currentDir === '/'" title="Up">
          <NIcon size="16"><PhArrowLeft /></NIcon>
        </NButton>
        <span class="breadcrumb-path">{{ currentDir }}</span>
      </NSpace>
      <NSpace size="small">
        <NButton size="small" @click="reloadDir" :disabled="!selectedBox" title="Refresh">
          <NIcon size="16"><PhUploadSimple /></NIcon>
        </NButton>
        <NButton size="small" @click="() => $router.push(`/terminal?box=${selectedBox}&dir=${encodeURIComponent(currentDir)}`)" :disabled="!selectedBox" title="Open Terminal">
          <NIcon size="16"><PhTerminalWindow /></NIcon>
        </NButton>
      </NSpace>
    </div>

    <header class="page-header">
      <div>
        <p class="eyebrow">files</p>
        <h1>File Browser & Editor</h1>
        <p class="text-muted">Complete file management with drag & drop, context menus, CodeMirror editor, and bulk operations.</p>
      </div>
    </header>

    <!-- Box Selection & Status -->
    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18"><PhFolderSimple /></NIcon>
        <span>Connection</span>
      </div>
      <NSpace vertical size="small">
        <NSpace size="small" align="center" wrap>
          <NSelect v-model:value="selectedBox" :options="boxOptions" placeholder="Choose box" :disabled="boxesStore.loading" @update:value="onBoxChange" style="min-width: 200px" />
          <NInput v-model:value="currentDir" placeholder="Directory path" size="small" style="max-width: 320px" @keyup.enter="reloadDir" />
          <NButton size="small" @click="reloadDir" :disabled="!selectedBox">Load</NButton>
          <NButton size="small" tertiary :disabled="!selectedBox" @click="toggleFavoriteCurrentDir">
            <NIcon size="14"><PhStar /></NIcon>
            {{ isCurrentDirFavorite ? "Unfavorite" : "Favorite" }}
          </NButton>
        </NSpace>
        <NAlert :type="status === 'online' ? 'success' : 'error'" v-if="selectedBox">
          <template #icon>
            <div class="connection-indicator" :class="{ connected: status === 'online' }" />
          </template>
          {{ selectedBox }} is {{ status }}
        </NAlert>
      </NSpace>
    </NCard>

    <!-- File Browser -->
    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18"><PhList /></NIcon>
        <span>Files</span>
        <div class="card-actions">
          <NSpace size="small" :wrap="isMobile">
            <DirectorySearchInput
              :box="selectedBox"
              :token="tokenValue"
              @select="navigateToDirectory"
            />
            <NInput v-model:value="filterQuery" placeholder="Filter..." size="small" style="width: 140px">
              <template #prefix><NIcon size="14"><PhMagnifyingGlass /></NIcon></template>
            </NInput>
            <NButton size="small" :type="viewFilter === 'all' ? 'primary' : 'default'" @click="viewFilter = 'all'">All</NButton>
            <NButton size="small" :type="viewFilter === 'files' ? 'primary' : 'default'" @click="viewFilter = 'files'">Files</NButton>
            <NButton size="small" :type="viewFilter === 'dirs' ? 'primary' : 'default'" @click="viewFilter = 'dirs'">Folders</NButton>
          </NSpace>
        </div>
      </div>
      
      <NSpace vertical size="small">
        <!-- Bulk Actions -->
        <div v-if="selectedCount > 0" class="bulk-actions">
          <NSpace size="small" align="center">
            <span class="text-muted">{{ selectedCount }} selected</span>
            <NButton size="small" type="error" ghost @click="bulkDelete">
              <NIcon size="14"><PhTrash /></NIcon>Delete Selected
            </NButton>
            <NButton size="small" @click="bulkDownload" :disabled="selectedCount > 10">
              <NIcon size="14"><PhDownloadSimple /></NIcon>Download Selected
            </NButton>
            <NButton size="small" @click="filesStore.setSelectedFiles([])">Clear Selection</NButton>
          </NSpace>
        </div>

        <!-- Recent Paths -->
        <div v-if="recentPaths.length" class="recent-paths">
          <NSpace size="small" align="center">
            <NIcon size="14"><PhClockCounterClockwise /></NIcon>
            <span class="text-muted small">Recent:</span>
            <NTag v-for="path in recentPaths.slice(0, 5)" :key="path" size="small" checkable @click="navigateToDirectory(path)">{{ path }}</NTag>
          </NSpace>
        </div>

        <!-- Loading State -->
        <NSpin v-if="directoryStore.loading || boxesStore.loading" size="small">
          <span class="text-muted">Loading directory...</span>
        </NSpin>

        <!-- File Table -->
        <div v-else class="file-browser" :class="{ 'drag-over': isDragOver }" @dragenter="handleDragEnter" @dragleave="handleDragLeave" @dragover="handleDragOver" @drop="handleDrop">
          <div v-if="isDragOver" class="drop-overlay">
            <div class="drop-message">
              <NIcon size="32"><PhUploadSimple /></NIcon>
              <span>Drop files to upload to {{ currentDir }}</span>
            </div>
          </div>
          
          <NDataTable :columns="columns" :data="filteredRows" size="small" striped :row-key="(row: any) => row.path" :checked-row-keys="selectedPaths" @update:checked-row-keys="(keys: (string | number)[]) => filesStore.setSelectedFiles(keys.map(String))" :row-props="getRowProps" :scroll-x="isMobile ? undefined : 800" />
        </div>

        <!-- Error Display -->
        <NAlert v-if="directoryStore.error || boxesStore.error" type="error" closable>{{ directoryStore.error || boxesStore.error }}</NAlert>
      </NSpace>
    </NCard>

    <!-- File Actions -->
    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18"><PhGear /></NIcon>
        <span>Actions</span>
      </div>
      <NSpace vertical size="small">
        <!-- Create File -->
        <NSpace size="small" align="center">
          <NInput v-model:value="newFileName" placeholder="New filename" size="small" style="max-width: 220px" @keyup.enter="createEmptyFile" />
          <NButton :disabled="actionBusy || !selectedBox || !newFileName.trim()" size="small" type="primary" @click="createEmptyFile">
            <NIcon size="14"><PhUploadSimple /></NIcon>Create File
          </NButton>
        </NSpace>

        <!-- File Upload -->
        <NSpace size="small" align="center">
          <input type="file" multiple @change="(e: any) => handleUpload(e.target.files)" style="max-width: 200px" />
          <span class="text-muted small">or drag & drop files above</span>
        </NSpace>

        <!-- Upload Progress -->
        <div v-if="uploading" class="upload-progress">
          <NAlert type="info">
            <template #icon><div class="spinner" /></template>
            Uploading {{ filesStore.uploadFileName }}...
            <NProgress type="line" :percentage="filesStore.uploadProgress" status="info" style="margin-top: 8px" />
          </NAlert>
        </div>

        <NAlert v-if="filesStore.uploadError" type="error" closable>{{ filesStore.uploadError }}</NAlert>
      </NSpace>
    </NCard>

    <!-- Favorites Panel -->
    <FavoritesPanel :box="selectedBox" @openPath="navigateToDirectory" @togglePin="handlePinToggle" />

    <!-- File Preview Modal -->
    <NModal v-model:show="previewing" preset="card" style="max-width: 90vw; max-height: 90vh">
      <template #header>
        <div class="modal-header">
          <NIcon size="16"><PhEye /></NIcon>
          <span>Preview: {{ previewPath.split('/').pop() }}</span>
          <div class="modal-actions">
            <NButton size="small" @click="handleEdit({ path: previewPath, name: previewPath.split('/').pop() })">
              <NIcon size="14"><PhPencil /></NIcon>Edit
            </NButton>
          </div>
        </div>
      </template>
      
      <div class="preview-container">
        <NSpin v-if="previewLoading" size="large"><span class="text-muted">Loading preview...</span></NSpin>
        <template v-else>
          <!-- Image Preview -->
          <div v-if="previewIsImage" class="preview-image-container">
            <img class="preview-image" :src="`data:${previewMeta?.image_mime};base64,${previewMeta?.image_data}`" :alt="previewPath" />
            <p v-if="previewMeta?.image_too_large" class="text-muted small">Image truncated at {{ previewMeta?.image_limit_kb }}KB</p>
          </div>
          <!-- Text Preview -->
          <div v-else class="preview-text-container">
            <CodeEditor :model-value="previewContent" :language="getLanguageFromFilename(previewPath)" :theme="editorTheme" :readonly="true" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 60vh" />
          </div>
        </template>
      </div>
      
      <template #footer>
        <NSpace justify="space-between">
          <NSpace size="small">
            <NSwitch v-model:value="showLineNumbers" size="small">
              <template #checked>Line Numbers</template>
              <template #unchecked>No Lines</template>
            </NSwitch>
            <NSwitch v-model:value="wordWrap" size="small">
              <template #checked>Word Wrap</template>
              <template #unchecked>No Wrap</template>
            </NSwitch>
            <NSelect v-model:value="editorTheme" :options="[{ label: 'Dark', value: 'dark' }, { label: 'Light', value: 'light' }]" size="small" style="width: 100px" />
          </NSpace>
          <NButton @click="closePreview">Close</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- File Editor Modal -->
    <NModal v-model:show="editing" preset="card" style="max-width: 95vw; max-height: 95vh">
      <template #header>
        <div class="modal-header">
          <NIcon size="16"><PhPencil /></NIcon>
          <span>Edit: {{ editPath.split('/').pop() }}</span>
          <div class="modal-actions">
            <NSpace size="small">
              <NSwitch v-model:value="showLineNumbers" size="small">
                <template #checked>Lines</template>
                <template #unchecked>No Lines</template>
              </NSwitch>
              <NSwitch v-model:value="wordWrap" size="small">
                <template #checked>Wrap</template>
                <template #unchecked>No Wrap</template>
              </NSwitch>
              <NSelect v-model:value="editorTheme" :options="[{ label: 'Dark', value: 'dark' }, { label: 'Light', value: 'light' }]" size="small" style="width: 80px" />
            </NSpace>
          </div>
        </div>
      </template>
      
      <div class="editor-container">
        <NSpin v-if="editLoading" size="large"><span class="text-muted">Loading file...</span></NSpin>
        <CodeEditor v-else v-model:model-value="editContent" :language="getLanguageFromFilename(editPath)" :theme="editorTheme" :line-numbers="showLineNumbers" :word-wrap="wordWrap" style="height: 70vh" placeholder="File content..." />
      </div>
      
      <template #footer>
        <NSpace justify="space-between">
          <div class="file-info"><span class="text-muted small">{{ editPath }}</span></div>
          <NSpace>
            <NButton @click="editing = false">Cancel</NButton>
            <NButton type="primary" :loading="editLoading" @click="saveEdit">
              <NIcon size="14"><PhUploadSimple /></NIcon>Save
            </NButton>
          </NSpace>
        </NSpace>
      </template>
    </NModal>

    <!-- Context Menu -->
    <ContextMenu :visible="contextMenu.visible" :x="contextMenu.x" :y="contextMenu.y" :items="getContextMenuItems()" @select="handleContextMenuSelect" @close="contextMenu.visible = false" />
  </div>
</template>
<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.breadcrumb-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-size: 14px;
}

.breadcrumb-path {
  color: var(--text);
  font-weight: 500;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 16px;
}

.card-title > span {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.connection-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--error);
  transition: background-color 0.2s ease;
}

.connection-indicator.connected {
  background: var(--success);
}

.bulk-actions {
  padding: 12px;
  background: var(--surface-variant);
  border-radius: 8px;
  border: 1px solid var(--stroke);
}

.recent-paths {
  padding: 8px 12px;
  background: var(--surface-variant);
  border-radius: 6px;
}

.file-browser {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
}

.file-browser.drag-over {
  border: 2px dashed var(--accent);
  background: var(--accent-bg);
}

.drop-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(var(--accent-rgb), 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  backdrop-filter: blur(4px);
}

.drop-message {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--accent);
  font-weight: 600;
  font-size: 16px;
}

.upload-progress {
  padding: 12px;
  background: var(--surface-variant);
  border-radius: 8px;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(var(--accent-rgb), 0.3);
  border-top: 2px solid var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.modal-header > span {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.modal-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preview-container,
.editor-container {
  min-height: 400px;
}

.preview-image-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.preview-image {
  max-width: 100%;
  max-height: 70vh;
  border-radius: 8px;
  border: 1px solid var(--stroke);
  background: var(--surface);
}

.preview-text-container {
  border-radius: 8px;
  overflow: hidden;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.small {
  font-size: 12px;
}

.text-muted {
  color: var(--muted);
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .page {
    gap: 12px;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .page-header h1 {
    font-size: 20px;
  }

  .breadcrumb-nav {
    flex-direction: column;
    gap: 8px;
  }

  .breadcrumb-path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 200px;
  }

  .card-title {
    flex-direction: column;
    align-items: flex-start;
  }

  .card-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .card-actions :deep(.n-input) {
    width: 100% !important;
  }

  .bulk-actions :deep(.n-space) {
    flex-wrap: wrap;
  }

  /* Full-width upload input */
  input[type="file"] {
    max-width: 100% !important;
    width: 100%;
  }
}

@media (max-width: 480px) {
  .breadcrumb-path {
    max-width: 140px;
  }
}

/* Touch optimizations */
@media (hover: none) and (pointer: coarse) {
  .file-browser :deep(.n-data-table-td) {
    min-height: 44px;
  }
  
  .file-browser :deep(.n-button) {
    min-height: 44px;
    min-width: 44px;
  }
}

/* High contrast mode */
@media (prefers-contrast: high) {
  .surface-card {
    border: 2px solid var(--stroke);
  }
  
  .connection-indicator {
    border: 1px solid var(--text);
  }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .file-browser,
  .connection-indicator,
  .spinner {
    transition: none;
    animation: none;
  }
}

/* Focus management for accessibility */
.file-browser :deep(.n-data-table-tbody .n-data-table-tr:focus-within) {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

/* Ensure proper contrast for selected rows */
.file-browser :deep(.n-data-table-tbody .n-data-table-tr--checked) {
  background: var(--accent-bg);
}
</style>
