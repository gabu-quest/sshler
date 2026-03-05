<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useResponsive } from "@/composables/useResponsive";
import {
  NAlert, NButton, NCard, NDataTable, NIcon, NInput, NModal, NProgress, NSelect, NSpace, NSpin, NTag, NTooltip, useMessage,
} from "naive-ui";
import {
  PhClockCounterClockwise, PhFile, PhList, PhFolderSimple, PhStar, PhUploadSimple, PhEye, PhPencil, PhDownloadSimple, PhTextAa, PhCopy, PhClipboard, PhTrash, PhFolder, PhMagnifyingGlass, PhGear, PhTerminalWindow, PhArrowBendUpLeft, PhCaretRight, PhCaretDown, PhArrowsOutSimple, PhArrowsInSimple, PhArchive,
} from "@phosphor-icons/vue";

import type { ApiBox, GitInfo } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";
import { useDirectoryStore } from "@/stores/directory";
import { useFilesStore } from "@/stores/files";
import { useFavoritesStore } from "@/stores/favorites";
import { useAppStore } from "@/stores/app";
import FavoritesPanel from "@/components/FavoritesPanel.vue";
import FileBreadcrumb from "@/components/FileBreadcrumb.vue";
import FileUploadZone from "@/components/FileUploadZone.vue";
import FilePreviewModal from "@/components/FilePreviewModal.vue";
import FileEditorModal from "@/components/FileEditorModal.vue";
import FileDiffModal from "@/components/FileDiffModal.vue";
import BatchMoveModal from "@/components/BatchMoveModal.vue";
import ContentSearchInput from "@/components/ContentSearchInput.vue";
import ContextMenu from "@/components/ContextMenu.vue";
import DirectorySearchInput from "@/components/DirectorySearchInput.vue";
import { touchFile, boxStatus, downloadFile, gitInfo, chmodFile, createArchive, extractArchive } from "@/api/http";
import { setEmojiFavicon, resetFavicon } from "@/utils/emoji-favicon";
import { useI18n } from "@/i18n";

const route = useRoute();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const directoryStore = useDirectoryStore();
const favoritesStore = useFavoritesStore();
const filesStore = useFilesStore();
const appStore = useAppStore();
const message = useMessage();
const { isMobile } = useResponsive();
const { t } = useI18n();

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
const editing = ref(false);
const editPath = ref("");
const editorTheme = computed(() => appStore.isDark ? 'dark' : 'light');
const contextMenu = ref({ visible: false, x: 0, y: 0, targetFile: null as any });
const previewModalRef = ref<InstanceType<typeof FilePreviewModal>>();
const expandedDirs = ref<Set<string>>(new Set());
const childrenCache = ref<Map<string, any[]>>(new Map());
const expandingDir = ref<string | null>(null);
const refreshing = ref(false);
const sortState = ref<{ columnKey: string; order: 'ascend' | 'descend' }>({ columnKey: 'name', order: 'ascend' });
const batchModal = ref<{ visible: boolean; mode: "move" | "copy" }>({ visible: false, mode: "move" });
const currentGitInfo = ref<GitInfo | null>(null);
const chmodModalVisible = ref(false);
const chmodTarget = ref<{ path: string; currentMode: number | null }>({ path: '', currentMode: null });
const chmodValue = ref("");
const diffModalVisible = ref(false);
const diffInitialFileA = ref("");
const diffInitialFileB = ref("");

const formatMode = (mode: number | null | undefined): string => {
  if (mode == null) return "-";
  const bits = "rwxrwxrwx";
  let result = "";
  for (let i = 8; i >= 0; i--) {
    result += (mode & (1 << i)) ? bits[8 - i] : "-";
  }
  return result;
};

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
  const filtered = entries.filter((entry: any) => {
    if (viewFilter.value === "files" && entry.is_directory) return false;
    if (viewFilter.value === "dirs" && !entry.is_directory) return false;
    if (!q) return true;
    return entry.name.toLowerCase().includes(q);
  });

  // Prepend parent (..) entry when not at root
  const result: any[] = [];
  if (currentDir.value !== '/' && currentDir.value !== '~') {
    const parentPath = currentDir.value.replace(/\/[^/]+\/?$/, '') || '/';
    result.push({
      name: '..', path: parentPath, is_directory: true,
      size: null, modified: null, _isParent: true,
    });
  }

  // Sort comparator for current sort state — directories always first
  const { columnKey, order } = sortState.value;
  const dir = order === 'descend' ? -1 : 1;
  const compare = (a: any, b: any): number => {
    // Directories always before files
    if (a.is_directory && !b.is_directory) return -1;
    if (!a.is_directory && b.is_directory) return 1;
    // Then by selected column
    let cmp = 0;
    switch (columnKey) {
      case 'name': cmp = a.name.localeCompare(b.name); break;
      case 'is_directory': {
        const aType = a.is_directory ? 'directory' : getFileType(a.name);
        const bType = b.is_directory ? 'directory' : getFileType(b.name);
        cmp = aType.localeCompare(bType);
        break;
      }
      case 'size': cmp = (a.size || 0) - (b.size || 0); break;
      case 'modified': cmp = (a.modified || 0) - (b.modified || 0); break;
      default: cmp = a.name.localeCompare(b.name);
    }
    return cmp * dir;
  };

  // Sort and insert entries with expanded children inline (recursive)
  const insertWithChildren = (entries: any[], indent: number) => {
    const sorted = [...entries].sort(compare);
    for (const entry of sorted) {
      result.push(indent > 0 ? { ...entry, _indent: indent } : entry);
      if (entry.is_directory && expandedDirs.value.has(entry.path)) {
        const children = childrenCache.value.get(entry.path);
        if (children) {
          insertWithChildren(children, indent + 1);
        }
      }
    }
  };
  insertWithChildren(filtered, 0);

  return result;
});
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

const toggleExpandDir = async (path: string) => {
  if (expandedDirs.value.has(path)) {
    expandedDirs.value.delete(path);
    expandedDirs.value = new Set(expandedDirs.value);
    return;
  }
  if (!selectedBox.value) return;
  expandingDir.value = path;
  const listing = await directoryStore.fetchChildren(selectedBox.value, path, tokenValue.value || null);
  if (listing?.entries) {
    childrenCache.value.set(path, listing.entries);
    childrenCache.value = new Map(childrenCache.value);
  }
  expandedDirs.value.add(path);
  expandedDirs.value = new Set(expandedDirs.value);
  expandingDir.value = null;
};

const expandAllDirs = async () => {
  if (!selectedBox.value) return;
  const dirs = (directoryStore.listing?.entries || []).filter((e: any) => e.is_directory);
  const batch = dirs.slice(0, 20); // limit to 20
  expandingDir.value = '__all__';
  await Promise.all(batch.map(async (dir: any) => {
    if (!expandedDirs.value.has(dir.path)) {
      const listing = await directoryStore.fetchChildren(selectedBox.value!, dir.path, tokenValue.value || null);
      if (listing?.entries) {
        childrenCache.value.set(dir.path, listing.entries);
      }
      expandedDirs.value.add(dir.path);
    }
  }));
  childrenCache.value = new Map(childrenCache.value);
  expandedDirs.value = new Set(expandedDirs.value);
  expandingDir.value = null;
};

const collapseAllDirs = () => {
  expandedDirs.value = new Set();
};

const navigateToDirectory = async (path: string) => {
  currentDir.value = path;
  expandedDirs.value = new Set();
  childrenCache.value = new Map();
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
  // Check if file is an extractable archive
  const archiveExts = ['.tar.gz', '.tgz', '.zip'];
  const isArchive = !file.is_directory && archiveExts.some((ext: string) => file.name.toLowerCase().endsWith(ext));

  items.push({ separator: true, id: 'sep1', label: '' }, { id: 'rename', label: 'Rename', icon: PhTextAa }, { id: 'chmod', label: 'Chmod', icon: PhGear }, { id: 'copy', label: 'Copy', icon: PhCopy }, { id: 'copy-path', label: 'Copy Path', icon: PhClipboard }, { id: 'favorite', label: file.is_directory ? 'Add to Favorites' : 'Favorite Directory', icon: PhStar });
  if (!file.is_directory) {
    items.push({ id: 'compare', label: 'Compare with...', icon: PhEye });
  }
  items.push({ separator: true, id: 'sep-archive', label: '' });
  if (isArchive) {
    items.push({ id: 'extract', label: 'Extract Here', icon: PhArchive });
  }
  items.push({ id: 'compress-tgz', label: 'Compress as .tar.gz', icon: PhArchive }, { id: 'compress-zip', label: 'Compress as .zip', icon: PhArchive });
  items.push({ separator: true, id: 'sep2', label: '' }, { id: 'delete', label: 'Delete', icon: PhTrash, danger: true });
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
    case 'chmod': handleChmodPrompt(file); break;
    case 'copy': renameTarget.value = file.path; copyDestination.value = currentDir.value; copyNewName.value = `${file.name}_copy`; break;
    case 'copy-path': await navigator.clipboard.writeText(file.path); message.success(t('files.path_copied')); break;
    case 'favorite': if (file.is_directory) await toggleFavoriteDir(file.path); else await toggleFavoriteCurrentDir(); break;
    case 'compare': openDiffModal(file.path); break;
    case 'extract': await handleArchiveExtract(file.path); break;
    case 'compress-tgz': await handleArchiveCreate('tar.gz', [file.path]); break;
    case 'compress-zip': await handleArchiveCreate('zip', [file.path]); break;
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
  message.success(nowFavorite ? t('files.favorite') + ' ' + path : t('files.unfavorite') + ' ' + path);
};

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
const handleSortChange = (opts: { columnKey: string; order: 'ascend' | 'descend' | false }) => {
  if (!opts.order) {
    sortState.value = { columnKey: 'name', order: 'ascend' };
  } else {
    sortState.value = { columnKey: opts.columnKey, order: opts.order };
  }
};

const getRowProps = (row: any) => ({
  style: `cursor: pointer;${row._indent ? 'opacity:0.85;' : ''}${row._isParent ? 'background:var(--surface-variant);' : ''}`,
  onClick: (e: MouseEvent) => {
    if (row._isParent) { navigateToDirectory(row.path); return; }
    handleRowClick(row, e);
  }
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

async function loadGitInfo() {
  if (!selectedBox.value || !currentDir.value) {
    currentGitInfo.value = null;
    return;
  }
  try {
    currentGitInfo.value = await gitInfo(selectedBox.value, currentDir.value, tokenValue.value || null);
  } catch {
    currentGitInfo.value = null;
  }
}

async function reloadDir() {
  if (!selectedBox.value) return;
  refreshing.value = true;
  try {
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    loadGitInfo();
    // Re-fetch children for all currently expanded directories
    const expandedPaths = [...expandedDirs.value];
    if (expandedPaths.length > 0) {
      await Promise.all(expandedPaths.map(async (path) => {
        const listing = await directoryStore.fetchChildren(selectedBox.value!, path, tokenValue.value || null);
        if (listing?.entries) {
          childrenCache.value.set(path, listing.entries);
        } else {
          childrenCache.value.delete(path);
          expandedDirs.value.delete(path);
        }
      }));
      childrenCache.value = new Map(childrenCache.value);
      expandedDirs.value = new Set(expandedDirs.value);
    }
  } finally {
    refreshing.value = false;
  }
}

async function handlePinToggle() {
  if (!selectedBox.value) return;
  const pinned = await favoritesStore.setPinned(selectedBox.value, tokenValue.value || null);
  applyBoxPatch(selectedBox.value, { pinned });
  if (favoritesStore.error) { message.error(favoritesStore.error); return; }
  message.success(pinned ? t('boxes.pinned') : t('boxes.unpinned'));
}

async function toggleFavoriteCurrentDir() {
  if (!selectedBox.value) return;
  const path = currentDir.value || "/";
  const desired = !favoritesStore.isFavorite(selectedBox.value, path);
  const nowFavorite = await favoritesStore.setFavorite(selectedBox.value, path, desired, tokenValue.value || null);
  if (favoritesStore.error) { message.error(favoritesStore.error); return; }
  applyBoxPatch(selectedBox.value, { favorites: Array.from(favoritesForSelection.value.values()) });
  message.success(nowFavorite ? t('files.favorite') + ' ' + path : t('files.unfavorite') + ' ' + path);
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
  loadGitInfo();
}

async function createEmptyFile() {
  if (!selectedBox.value || !newFileName.value.trim()) return;
  actionBusy.value = true;
  try {
    await touchFile(selectedBox.value, currentDir.value, newFileName.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    newFileName.value = "";
    message.success(t('files.file_created'));
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
    message.success(t('files.deleted'));
  } catch (err) {
    directoryStore.error = err instanceof Error ? err.message : String(err);
  } finally {
    actionBusy.value = false;
  }
}

const renameModalVisible = computed({
  get: () => renameTarget.value !== null,
  set: (val: boolean) => { if (!val) { renameTarget.value = null; renameValue.value = ""; } },
});

function handleRenamePrompt(row: any) {
  renameTarget.value = row.path;
  renameValue.value = row.name;
}

function handleChmodPrompt(row: any) {
  chmodTarget.value = { path: row.path, currentMode: row.mode ?? null };
  chmodValue.value = row.mode != null ? row.mode.toString(8).padStart(3, '0') : "644";
  chmodModalVisible.value = true;
}

async function applyChmod() {
  if (!selectedBox.value || !chmodTarget.value.path || !chmodValue.value.trim()) return;
  actionBusy.value = true;
  try {
    await chmodFile(selectedBox.value, chmodTarget.value.path, chmodValue.value.trim(), tokenValue.value || null);
    chmodModalVisible.value = false;
    message.success(`Permissions changed to ${chmodValue.value}`);
    await reloadDir();
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function doRename() {
  if (!selectedBox.value || !renameTarget.value || !renameValue.value.trim()) return;
  actionBusy.value = true;
  try {
    await filesStore.doRename(selectedBox.value, renameTarget.value, renameValue.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    message.success(t('files.renamed'));
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
      message.success(t('files.moved'));
    } else {
      await filesStore.doCopy(selectedBox.value, renameTarget.value, copyDestination.value, copyNewName.value || null, tokenValue.value || null);
      message.success(t('files.copied'));
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

  const maxAttempts = 3;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await filesStore.doUpload(selectedBox.value, currentDir.value, file, tokenValue.value || null);
      message.success(t('files.uploaded'));
      await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
      await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
      actionBusy.value = false;
      uploadTarget.value = null;
      return;
    } catch (err) {
      lastError = err;
      if (attempt < maxAttempts) {
        message.warning(t('files.upload_failed_retry'));
        await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt - 1)));
      }
    }
  }

  const errorMsg = lastError instanceof Error ? lastError.message : String(lastError);
  message.error(errorMsg, {
    closable: true,
    duration: 0,
    content: t('files.upload_failed_final', { n: String(maxAttempts) }),
  });
  actionBusy.value = false;
  uploadTarget.value = null;
}

function handlePreview(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  previewPath.value = row.path;
  previewing.value = true;
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

function handleEdit(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  editPath.value = row.path;
  editing.value = true;
}

async function handleEditorSaved(path: string, content: string) {
  // Update preview content so returning to preview shows the saved edits
  if (previewing.value && previewPath.value === path) {
    previewModalRef.value?.updateContent(content);
  }
  if (selectedBox.value) {
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
  }
}

async function bulkDelete() {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  actionBusy.value = true;
  try {
    const result = await filesStore.doBatchDelete(selectedBox.value, selectedPaths.value, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    if (result.status === "partial") {
      message.warning(`Deleted ${result.succeeded.length}, failed ${result.failed.length}`);
    } else {
      message.success(t('files.deleted_selection'));
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

function openBatchModal(mode: "move" | "copy") {
  batchModal.value = { visible: true, mode };
}

async function handleBatchConfirm(destination: string) {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  batchModal.value.visible = false;
  actionBusy.value = true;
  try {
    const result = batchModal.value.mode === "move"
      ? await filesStore.doBatchMove(selectedBox.value, selectedPaths.value, destination, tokenValue.value || null)
      : await filesStore.doBatchCopy(selectedBox.value, selectedPaths.value, destination, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    const verb = batchModal.value.mode === "move" ? "Moved" : "Copied";
    if (result.status === "partial") {
      message.warning(`${verb} ${result.succeeded.length}, failed ${result.failed.length}`);
    } else {
      message.success(`${verb} ${result.succeeded.length} item(s)`);
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function handleArchiveCreate(format: string, paths?: string[]) {
  if (!selectedBox.value) return;
  const archivePaths = paths || selectedPaths.value;
  if (!archivePaths.length) return;
  actionBusy.value = true;
  const ext = format === "zip" ? ".zip" : ".tar.gz";
  const archiveName = archivePaths.length === 1
    ? archivePaths[0].split('/').pop() + ext
    : `archive${ext}`;
  try {
    await createArchive(selectedBox.value, archivePaths, currentDir.value, archiveName, format, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    message.success(`Created ${archiveName}`);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

async function handleArchiveExtract(archivePath: string) {
  if (!selectedBox.value) return;
  actionBusy.value = true;
  try {
    await extractArchive(selectedBox.value, archivePath, currentDir.value, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    message.success("Extracted archive");
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    actionBusy.value = false;
  }
}

function openDiffModal(fileA?: string, fileB?: string) {
  diffInitialFileA.value = fileA || "";
  diffInitialFileB.value = fileB || "";
  diffModalVisible.value = true;
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
      // Parent (..) entry — single click navigates
      if (row._isParent) {
        return h("div", {
          style: "display:flex;align-items:center;gap:8px;cursor:pointer;min-width:0;",
          onClick: () => navigateToDirectory(row.path),
        }, [
          h(NIcon, { size: 16 }, () => h(PhArrowBendUpLeft, { weight: 'duotone' })),
          h("span", { style: "font-weight:500;" }, '..'),
        ]);
      }

      const indent = row._indent ? row._indent * 24 : 0;
      const isFav = row.is_directory && favoritesStore.isFavorite(selectedBox.value, row.path);
      const isExpanded = expandedDirs.value.has(row.path);
      const isExpanding = expandingDir.value === row.path;

      const children: any[] = [];

      // Expand/collapse button for directories (all levels)
      if (row.is_directory) {
        children.push(h("button", {
          style: "background:none;border:none;cursor:pointer;padding:2px;color:var(--muted);display:flex;align-items:center;flex-shrink:0;",
          onClick: (e: MouseEvent) => { e.stopPropagation(); toggleExpandDir(row.path); },
          title: isExpanded ? 'Collapse' : 'Expand',
        }, [
          isExpanding
            ? h("div", { class: "spinner", style: "width:12px;height:12px;border-width:1.5px;" })
            : h(NIcon, { size: 14 }, () => h(isExpanded ? PhCaretDown : PhCaretRight, { weight: 'duotone' }))
        ]));
      }

      children.push(h(NIcon, { size: 16 }, () => h(row.is_directory ? PhFolderSimple : PhFile, { weight: 'duotone' })));
      children.push(h("div", { style: "min-width:0;flex:1;" }, [
        h("span", row.name),
        isMobile.value && !row.is_directory && row.size
          ? h("div", { style: "font-size:11px;color:var(--muted);" }, formatFileSize(row.size))
          : null,
      ]));
      if (isFav) {
        children.push(h(NIcon, { size: 14, color: "#faad14", style: "margin-left: 4px;flex-shrink:0;" }, () => h(PhStar, { weight: "fill" })));
      }

      return h("div", {
        style: `display:flex;align-items:center;gap:8px;cursor:pointer;min-width:0;padding-left:${indent}px;`,
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

  const sortOrder = (key: string) => sortState.value.columnKey === key ? sortState.value.order : false as const;

  return [
    { type: "selection" as const },
    { ...nameCol, sorter: true, sortOrder: sortOrder('name') },
    {
      title: "type", key: "is_directory", sorter: true, sortOrder: sortOrder('is_directory'),
      render(row: any) { return row.is_directory ? "directory" : getFileType(row.name); },
    },
    {
      title: "size", key: "size", sorter: true, sortOrder: sortOrder('size'),
      render(row: any) { return row.size ? formatFileSize(row.size) : "-"; },
    },
    {
      title: "modified", key: "modified", sorter: true, sortOrder: sortOrder('modified'),
      render(row: any) { return row.modified ? new Date(row.modified * 1000).toLocaleDateString() : "-"; },
    },
    {
      title: "perms", key: "mode", width: 100,
      render(row: any) {
        if (row._isParent || row.mode == null) return "-";
        return h("span", {
          style: "font-family:var(--font-mono);font-size:12px;cursor:pointer;",
          title: `${row.mode.toString(8).padStart(3, '0')} — click to chmod`,
          onClick: () => handleChmodPrompt(row),
        }, formatMode(row.mode));
      },
    },
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
            }, { icon: () => h(NIcon, { size: 14, color: isFav ? "#faad14" : undefined }, () => h(PhStar, { weight: isFav ? "fill" : "duotone" })) }),
            default: () => isFav ? "Remove from Favorites" : "Add to Favorites"
          }),
          row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, {
              size: "tiny",
              quaternary: true,
              onClick: () => window.open(`/app/terminal?box=${encodeURIComponent(selectedBox.value || '')}&dir=${encodeURIComponent(row.path)}`, '_blank')
            }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTerminalWindow, { weight: 'duotone' })) }),
            default: () => "Open Terminal Here"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handlePreview(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhEye, { weight: 'duotone' })) }),
            default: () => "Preview"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleEdit(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhPencil, { weight: 'duotone' })) }),
            default: () => "Edit"
          }),
          !row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleDownload(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhDownloadSimple, { weight: 'duotone' })) }),
            default: () => "Download"
          }),
          h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, onClick: () => handleRenamePrompt(row) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTextAa, { weight: 'duotone' })) }),
            default: () => "Rename"
          }),
          h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", onClick: () => removePath(row.path) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTrash, { weight: 'duotone' })) }),
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
    <FileBreadcrumb
      :current-dir="currentDir"
      :git-info="currentGitInfo"
      :selected-box="selectedBox"
      :refreshing="refreshing"
      @navigate-home="navigateHome"
      @navigate-up="navigateUp"
      @reload="reloadDir"
      @open-terminal="$router.push(`/terminal?box=${selectedBox}&dir=${encodeURIComponent(currentDir)}`)"
    />

    <header class="page-header">
      <div>
        <p class="eyebrow">{{ t('files.heading') }}</p>
        <h1>{{ t('files.subtitle') }}</h1>
        <p class="text-muted">{{ t('files.description') }}</p>
      </div>
    </header>

    <!-- Box Selection & Status -->
    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18"><PhFolderSimple weight="duotone" /></NIcon>
        <span>{{ t('files.connection') }}</span>
      </div>
      <NSpace vertical size="small">
        <NSpace size="small" align="center" wrap>
          <NSelect v-model:value="selectedBox" :options="boxOptions" :placeholder="t('files.choose_box')" :disabled="boxesStore.loading" @update:value="onBoxChange" style="min-width: 200px" />
          <NInput v-model:value="currentDir" :placeholder="t('files.dir_placeholder')" size="small" style="max-width: 320px" @keyup.enter="reloadDir" />
          <NButton size="small" @click="reloadDir" :disabled="!selectedBox">{{ t('files.load') }}</NButton>
          <NButton size="small" tertiary :disabled="!selectedBox" @click="toggleFavoriteCurrentDir">
            <NIcon size="14"><PhStar weight="duotone" /></NIcon>
            {{ isCurrentDirFavorite ? t('files.unfavorite') : t('files.favorite') }}
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
        <NIcon size="18"><PhList weight="duotone" /></NIcon>
        <span>{{ t('common.files') }}</span>
        <div class="card-actions">
          <NSpace size="small" :wrap="isMobile">
            <DirectorySearchInput
              :box="selectedBox"
              :token="tokenValue"
              @select="navigateToDirectory"
            />
            <NInput v-model:value="filterQuery" :placeholder="t('files.filter_placeholder')" size="small" style="width: 140px">
              <template #prefix><NIcon size="14"><PhMagnifyingGlass weight="duotone" /></NIcon></template>
            </NInput>
            <NButton size="small" :type="viewFilter === 'all' ? 'primary' : 'default'" @click="viewFilter = 'all'">{{ t('common.all') }}</NButton>
            <NButton size="small" :type="viewFilter === 'files' ? 'primary' : 'default'" @click="viewFilter = 'files'">{{ t('common.files') }}</NButton>
            <NButton size="small" :type="viewFilter === 'dirs' ? 'primary' : 'default'" @click="viewFilter = 'dirs'">{{ t('common.folders') }}</NButton>
            <NButton size="small" quaternary @click="expandAllDirs" :disabled="!selectedBox || !!expandingDir" :title="'Expand All'">
              <NIcon size="14"><PhArrowsOutSimple weight="duotone" /></NIcon>
            </NButton>
            <NButton size="small" quaternary @click="collapseAllDirs" :disabled="expandedDirs.size === 0" :title="'Collapse All'">
              <NIcon size="14"><PhArrowsInSimple weight="duotone" /></NIcon>
            </NButton>
          </NSpace>
        </div>
      </div>
      
      <NSpace vertical size="small">
        <!-- Bulk Actions -->
        <div v-if="selectedCount > 0" class="bulk-actions">
          <NSpace size="small" align="center">
            <span class="text-muted">{{ selectedCount }} {{ t('files.selected') }}</span>
            <NButton size="small" type="error" ghost @click="bulkDelete">
              <NIcon size="14"><PhTrash weight="duotone" /></NIcon>{{ t('files.delete_selected') }}
            </NButton>
            <NButton size="small" @click="bulkDownload" :disabled="selectedCount > 10">
              <NIcon size="14"><PhDownloadSimple weight="duotone" /></NIcon>{{ t('files.download_selected') }}
            </NButton>
            <NButton size="small" @click="openBatchModal('move')">
              <NIcon size="14"><PhArrowBendUpLeft weight="duotone" /></NIcon>Move To...
            </NButton>
            <NButton size="small" @click="openBatchModal('copy')">
              <NIcon size="14"><PhCopy weight="duotone" /></NIcon>Copy To...
            </NButton>
            <NButton size="small" @click="handleArchiveCreate('tar.gz')">
              <NIcon size="14"><PhArchive weight="duotone" /></NIcon>.tar.gz
            </NButton>
            <NButton size="small" @click="handleArchiveCreate('zip')">
              <NIcon size="14"><PhArchive weight="duotone" /></NIcon>.zip
            </NButton>
            <NButton v-if="selectedCount === 2" size="small" @click="openDiffModal(selectedPaths[0], selectedPaths[1])">
              <NIcon size="14"><PhEye weight="duotone" /></NIcon>Compare
            </NButton>
            <NButton size="small" @click="filesStore.setSelectedFiles([])">{{ t('files.clear_selection') }}</NButton>
          </NSpace>
        </div>

        <!-- Recent Paths -->
        <div v-if="recentPaths.length" class="recent-paths">
          <NSpace size="small" align="center">
            <NIcon size="14"><PhClockCounterClockwise weight="duotone" /></NIcon>
            <span class="text-muted small">{{ t('files.recent') }}</span>
            <NTag v-for="path in recentPaths.slice(0, 5)" :key="path" size="small" checkable @click="navigateToDirectory(path)">{{ path }}</NTag>
          </NSpace>
        </div>

        <!-- Loading State -->
        <NSpin v-if="directoryStore.loading || boxesStore.loading" size="small">
          <span class="text-muted">{{ t('files.loading_dir') }}</span>
        </NSpin>

        <!-- File Table -->
        <FileUploadZone v-else :current-dir="currentDir" @upload="handleUpload">
          <div aria-live="polite" :aria-label="t('a11y.file_list')">
            <NDataTable :columns="columns" :data="filteredRows" size="small" striped :row-key="(row: any) => row._isParent ? '__parent__' : row.path" :checked-row-keys="selectedPaths" @update:checked-row-keys="(keys: (string | number)[]) => filesStore.setSelectedFiles(keys.map(String))" @update:sorter="handleSortChange" :row-props="getRowProps" :scroll-x="isMobile ? undefined : 800" :virtual-scroll="!isMobile" :max-height="isMobile ? undefined : 'calc(100vh - 280px)'" />
          </div>
        </FileUploadZone>

        <!-- Error Display -->
        <NAlert v-if="directoryStore.error || boxesStore.error" type="error" closable>{{ directoryStore.error || boxesStore.error }}</NAlert>
      </NSpace>
    </NCard>

    <!-- File Actions -->
    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18"><PhGear weight="duotone" /></NIcon>
        <span>{{ t('files.col_actions') }}</span>
      </div>
      <NSpace vertical size="small">
        <!-- Create File -->
        <NSpace size="small" align="center">
          <NInput v-model:value="newFileName" :placeholder="t('files.new_filename')" size="small" style="max-width: 220px" @keyup.enter="createEmptyFile" />
          <NButton :disabled="actionBusy || !selectedBox || !newFileName.trim()" size="small" type="primary" @click="createEmptyFile">
            <NIcon size="14"><PhUploadSimple weight="duotone" /></NIcon>{{ t('files.create_file') }}
          </NButton>
        </NSpace>

        <!-- Content Search (grep) -->
        <ContentSearchInput
          :box="selectedBox || null"
          :token="tokenValue || null"
          :directory="currentDir"
          @navigate="(dir: string) => navigateToDirectory(dir)"
          @select-file="(path: string) => { previewPath = path; previewing = true }"
        />

        <!-- File Upload -->
        <NSpace size="small" align="center">
          <input type="file" multiple @change="(e: any) => handleUpload(e.target.files)" style="max-width: 200px" />
          <span class="text-muted small">{{ t('files.or_drag_drop') }}</span>
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
    <FilePreviewModal
      ref="previewModalRef"
      :show="previewing"
      :path="previewPath"
      :box="selectedBox"
      :token="tokenValue"
      :theme="editorTheme"
      @update:show="previewing = $event"
      @edit="(path: string) => handleEdit({ path, is_directory: false })"
      @compare="(path: string) => openDiffModal(path)"
    />

    <!-- File Editor Modal -->
    <FileEditorModal
      :show="editing"
      :path="editPath"
      :box="selectedBox"
      :token="tokenValue"
      :theme="editorTheme"
      @update:show="editing = $event"
      @saved="handleEditorSaved"
    />

    <!-- File Diff Modal -->
    <FileDiffModal
      :show="diffModalVisible"
      :box="selectedBox"
      :token="tokenValue"
      :theme="editorTheme"
      :initial-file-a="diffInitialFileA"
      :initial-file-b="diffInitialFileB"
      @update:show="diffModalVisible = $event"
    />

    <!-- Rename Modal -->
    <NModal v-model:show="renameModalVisible" preset="dialog" :title="t('common.rename')" positive-text="Rename" negative-text="Cancel" @positive-click="doRename" @negative-click="renameModalVisible = false" style="max-width: 400px">
      <NSpace vertical size="small">
        <p class="text-muted" style="font-size:13px;">{{ renameTarget?.split('/').pop() }}</p>
        <NInput v-model:value="renameValue" placeholder="New name" size="small" @keyup.enter="doRename" />
      </NSpace>
    </NModal>

    <!-- Chmod Modal -->
    <NModal v-model:show="chmodModalVisible" preset="dialog" title="Change Permissions" positive-text="Apply" negative-text="Cancel" @positive-click="applyChmod" @negative-click="chmodModalVisible = false" style="max-width: 360px">
      <NSpace vertical size="small">
        <p class="text-muted" style="font-size:13px;">{{ chmodTarget.path.split('/').pop() }}</p>
        <p v-if="chmodTarget.currentMode != null" style="font-size:12px;font-family:var(--font-mono);">Current: {{ formatMode(chmodTarget.currentMode) }} ({{ chmodTarget.currentMode.toString(8).padStart(3, '0') }})</p>
        <NInput v-model:value="chmodValue" placeholder="e.g. 755" size="small" style="font-family:var(--font-mono);" @keyup.enter="applyChmod" />
        <p v-if="chmodValue" style="font-size:12px;font-family:var(--font-mono);color:var(--muted);">Preview: {{ formatMode(parseInt(chmodValue, 8) || 0) }}</p>
      </NSpace>
    </NModal>

    <!-- Context Menu -->
    <ContextMenu :visible="contextMenu.visible" :x="contextMenu.x" :y="contextMenu.y" :items="getContextMenuItems()" @select="handleContextMenuSelect" @close="contextMenu.visible = false" />

    <!-- Batch Move/Copy Modal -->
    <BatchMoveModal
      :visible="batchModal.visible"
      :mode="batchModal.mode"
      :box="selectedBox || null"
      :token="tokenValue || null"
      :count="selectedCount"
      @confirm="handleBatchConfirm"
      @close="batchModal.visible = false"
    />
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
