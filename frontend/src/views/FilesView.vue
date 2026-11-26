<script setup lang="ts">
import { computed, h, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useResponsive } from "@/composables/useResponsive";
import {
  NAlert, NButton, NDataTable, NIcon, NInput, NModal, NPopover, NProgress, NSpace, NSpin, NTag, NTooltip, useDialog, useMessage,
} from "naive-ui";
import {
  PhClockCounterClockwise, PhFile, PhFilePdf, PhFolderSimple, PhPlus, PhStar, PhUploadSimple, PhEye, PhPencil, PhPrinter, PhDownloadSimple, PhTextAa, PhCopy, PhClipboard, PhTrash, PhFolder, PhMagnifyingGlass, PhGear, PhTerminalWindow, PhArrowBendUpLeft, PhCaretRight, PhCaretDown, PhArrowsOutSimple, PhArrowsInSimple, PhArchive,
} from "@phosphor-icons/vue";

import type { ApiBox } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { usePdfExport } from "@/composables/usePdfExport";
import { useBoxAnswer } from "@/composables/useBoxAnswer";
import { useGitInfo } from "@/composables/useGitInfo";
import { useReplacePrompt } from "@/composables/useReplacePrompt";
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
import { ApiError, failureText, errorText } from "@/api/apiError";
import { touchFile, createFolder, boxStatus, downloadFile, downloadDirectory, directorySize, chmodFile, createArchive, extractArchive } from "@/api/http";
import { setEmojiFavicon, resetFavicon, getEmojiForBox } from "@/utils/emoji-favicon";
import { useI18n } from "@/i18n";
import { parentPath } from "@/utils/pathParts";

// Persisted view state (sort order + all/files/dirs filter) — survives page reloads.
const SORT_STATE_KEY = "sshler:files:sort";
const VIEW_FILTER_KEY = "sshler:files:viewFilter";

type SortState = { columnKey: string; order: 'ascend' | 'descend' };
const DEFAULT_SORT_STATE: SortState = { columnKey: 'name', order: 'ascend' };

const readSortState = (): SortState => {
  if (typeof localStorage === "undefined") return DEFAULT_SORT_STATE;
  const stored = localStorage.getItem(SORT_STATE_KEY);
  if (!stored) return DEFAULT_SORT_STATE;
  try {
    const parsed = JSON.parse(stored);
    if (
      parsed && typeof parsed === "object" &&
      typeof parsed.columnKey === "string" &&
      (parsed.order === "ascend" || parsed.order === "descend")
    ) {
      return { columnKey: parsed.columnKey, order: parsed.order };
    }
  } catch {
    // malformed JSON — fall through to default
  }
  return DEFAULT_SORT_STATE;
};

const readViewFilter = (): "all" | "files" | "dirs" => {
  if (typeof localStorage === "undefined") return "all";
  const stored = localStorage.getItem(VIEW_FILTER_KEY);
  if (stored === "all" || stored === "files" || stored === "dirs") return stored;
  return "all";
};

const route = useRoute();
const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const directoryStore = useDirectoryStore();
const favoritesStore = useFavoritesStore();
const filesStore = useFilesStore();
const appStore = useAppStore();
const dialog = useDialog();
const { askReplace, askReplaceMany, withReplace, leave: leavePrompt } = useReplacePrompt();
const message = useMessage();
const { exportOne: exportOnePdf, exportMany: exportManyPdf, busy: pdfBusy } = usePdfExport();
const { isMobile } = useResponsive();
const { t } = useI18n();

// State
const selectedBox = ref<string | null>(null);
const currentDir = ref("/");
const newName = ref("");
const newOpen = ref(false);
const newNameRef = ref<{ focus?: () => void } | null>(null);
// The popover draws its content after it opens, so focus the name field on the next tick.
watch(newOpen, async (open) => {
  if (!open) return;
  await nextTick();
  newNameRef.value?.focus?.();
});
const showGrep = ref(false);
const uploadInputRef = ref<HTMLInputElement>();
const actionBusy = ref(false);
const uploadTarget = ref<File | null>(null);
const renameTarget = ref<string | null>(null);
const renameValue = ref("");
const copyDestination = ref("");
const copyNewName = ref("");
const status = ref<string>("unknown");
// Why the box is not online, shown after the status words.
const statusText = ref<string | null>(null);
const viewFilter = ref<"all" | "files" | "dirs">(readViewFilter());
const previewing = ref(false);
const previewPath = ref("");
const autoPrint = ref(false);
const editing = ref(false);
const editPath = ref("");
const editorTheme = computed(() => appStore.isDark ? 'dark' : 'light');
const contextMenu = ref({ visible: false, x: 0, y: 0, targetFile: null as any });
const previewModalRef = ref<InstanceType<typeof FilePreviewModal>>();
const expandedDirs = ref<Set<string>>(new Set());
const childrenCache = ref<Map<string, any[]>>(new Map());
const expandingDir = ref<string | null>(null);
const refreshing = ref(false);
const sortState = ref<SortState>(readSortState());
const batchModal = ref<{ visible: boolean; mode: "move" | "copy" }>({ visible: false, mode: "move" });
const chmodModalVisible = ref(false);
const chmodTarget = ref<{ path: string; currentMode: number | null }>({ path: '', currentMode: null });
const chmodValue = ref("");
const diffModalVisible = ref(false);
const diffInitialFileA = ref("");
const diffInitialFileB = ref("");

// Scroll indicators for file table
const fileTableWrapRef = ref<HTMLDivElement>();
const canScrollUp = ref(false);
const canScrollDown = ref(false);
let scrollObserver: MutationObserver | null = null;

function updateScrollIndicators() {
  const wrap = fileTableWrapRef.value;
  if (!wrap) return;
  // NDataTable's scroll container is the .n-scrollbar-container inside it
  const scrollEl = wrap.querySelector('.n-scrollbar-container') as HTMLElement | null;
  if (!scrollEl) return;
  canScrollUp.value = scrollEl.scrollTop > 8;
  canScrollDown.value = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight > 8;
}

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
  boxesStore.items.map((box) => ({ label: `${getEmojiForBox(box.name)} ${box.name} (${box.host})`, value: box.name })),
);

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null);
// Which box the address means (one answer for every page) and the git state of the folder shown.
const { answer: boxAnswer, unknownText } = useBoxAnswer(() => route.query.box, () => boxesStore.items);
const { info: currentGitInfo, reload: reloadGit } = useGitInfo(() => selectedBox.value, () => currentDir.value, () => tokenValue.value);
// The slim alert is for a box that is not online; while the first answer is out ("unknown") it stays away.
const offlineText = computed(() => {
  if (!selectedBox.value || status.value === "online" || status.value === "unknown") return "";
  return `${selectedBox.value} · ${t("files.status_offline")}${statusText.value ? `: ${statusText.value}` : ""}`;
});
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
  const upPath = parentPath(currentDir.value);
  if (upPath !== null) {
    result.push({
      name: '..', path: upPath, is_directory: true,
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
        const aType = a.is_directory ? t('files.type_directory') : getFileType(a.name);
        const bType = b.is_directory ? t('files.type_directory') : getFileType(b.name);
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
// Ordered list of currently-visible non-directory file paths — feeds the preview
// modal's prev/next navigation so reviewing a folder doesn't require close/reopen.
const previewSiblings = computed(() => filteredRows.value.filter((r: any) => !r.is_directory).map((r: any) => r.path));

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
  // Split on both POSIX (/) and Windows (\) separators.
  const parts = currentDir.value.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] || 'Root';
});

// Persist sort order + view filter across reloads
watch(viewFilter, (val) => {
  if (typeof localStorage !== "undefined") localStorage.setItem(VIEW_FILTER_KEY, val);
});

watch(sortState, (val) => {
  if (typeof localStorage !== "undefined") localStorage.setItem(SORT_STATE_KEY, JSON.stringify(val));
}, { deep: true });

// Update browser tab title and favicon
watch([selectedBox, currentDir], () => {
  appStore.activeBox = selectedBox.value;
  appStore.activeDir = currentDir.value;
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
  // Names that are the same in every language stay as they are; the rest are text keys.
  const types: Record<string, string> = {
    'js': 'JavaScript', 'ts': 'TypeScript', 'py': 'Python', 'html': 'HTML', 'css': 'CSS', 'json': 'JSON', 'md': 'Markdown', 'yml': 'YAML', 'yaml': 'YAML', 'xml': 'XML', 'svg': 'SVG', 'pdf': 'PDF',
    'txt': t('files.type_text'), 'log': t('files.type_log'),
    'png': t('files.type_image'), 'jpg': t('files.type_image'), 'jpeg': t('files.type_image'), 'gif': t('files.type_image'),
    'zip': t('files.type_archive'), 'tar': t('files.type_archive'), 'gz': t('files.type_archive'),
  };
  return types[ext || ''] || t('files.type_file');
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
  // A folder whose listing did not come stays closed, so the next click asks again.
  if (listing?.entries) {
    childrenCache.value.set(path, listing.entries);
    childrenCache.value = new Map(childrenCache.value);
    expandedDirs.value.add(path);
    expandedDirs.value = new Set(expandedDirs.value);
  }
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
        expandedDirs.value.add(dir.path);
      }
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

const handleSearchSelect = async (path: string, isDirectory: boolean) => {
  if (isDirectory) {
    await navigateToDirectory(path);
    return;
  }
  // File hit: jump to its parent directory and open the preview
  const parentDir = parentPath(path) ?? '/';
  if (parentDir !== currentDir.value) {
    await navigateToDirectory(parentDir);
  }
  previewPath.value = path;
  previewing.value = true;
};

const navigateUp = async () => {
  const parentDir = parentPath(currentDir.value);
  if (parentDir !== null) await navigateToDirectory(parentDir);
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
    items.push({ id: 'open', label: t('common.open'), icon: PhFolder }, { id: 'download-dir', label: t('files.download_as_zip'), icon: PhDownloadSimple });
  } else {
    items.push({ id: 'preview', label: t('common.preview'), icon: PhEye }, { id: 'print', label: t('files.print'), icon: PhPrinter }, { id: 'edit', label: t('common.edit'), icon: PhPencil }, { id: 'download', label: t('common.download'), icon: PhDownloadSimple });
    if (bootstrapStore.pdfAvailable) {
      items.push({ id: 'pdf', label: t('files.download_pdf'), icon: PhFilePdf });
    }
  }
  // Check if file is an extractable archive
  const archiveExts = ['.tar.gz', '.tgz', '.zip'];
  const isArchive = !file.is_directory && archiveExts.some((ext: string) => file.name.toLowerCase().endsWith(ext));

  items.push({ separator: true, id: 'sep1', label: '' }, { id: 'rename', label: t('common.rename'), icon: PhTextAa }, { id: 'chmod', label: t('files.chmod'), icon: PhGear }, { id: 'copy', label: t('common.copy'), icon: PhCopy }, { id: 'copy-path', label: t('files.copy_path'), icon: PhClipboard });
  if (bootstrapStore.wslDistro && selectedBox.value === 'local') {
    items.push({ id: 'copy-wsl-path', label: t('files.copy_windows_path'), icon: PhClipboard });
  }
  items.push({ id: 'favorite', label: file.is_directory ? t('files.add_to_favorites') : t('files.favorite_directory'), icon: PhStar });
  if (!file.is_directory) {
    items.push({ id: 'compare', label: t('files.compare_with'), icon: PhEye });
  }
  items.push({ separator: true, id: 'sep-archive', label: '' });
  if (isArchive) {
    items.push({ id: 'extract', label: t('files.extract_here'), icon: PhArchive });
  }
  items.push({ id: 'compress-tgz', label: t('files.compress_tgz'), icon: PhArchive }, { id: 'compress-zip', label: t('files.compress_zip'), icon: PhArchive });
  items.push({ separator: true, id: 'sep2', label: '' }, { id: 'delete', label: t('common.delete'), icon: PhTrash, danger: true });
  return items;
};

const handleContextMenuSelect = async (itemId: string) => {
  const file = contextMenu.value.targetFile;
  if (!file) return;
  switch (itemId) {
    case 'open': if (file.is_directory) await navigateToDirectory(file.path); break;
    case 'download-dir': await handleDownloadDirectory(file); break;
    case 'preview': await handlePreview(file); break;
    case 'print': await handlePrint(file); break;
    case 'pdf': await handleDownloadPdf(file); break;
    case 'edit': await handleEdit(file); break;
    case 'download': await handleDownload(file); break;
    case 'rename': handleRenamePrompt(file); break;
    case 'chmod': handleChmodPrompt(file); break;
    case 'copy': renameTarget.value = file.path; copyDestination.value = currentDir.value; copyNewName.value = `${file.name}_copy`; break;
    case 'copy-path': await navigator.clipboard.writeText(file.path); message.success(t('files.path_copied')); break;
    case 'copy-wsl-path': {
      const winPath = `\\\\wsl$\\${bootstrapStore.wslDistro}${file.path.replace(/\//g, '\\')}`
      await navigator.clipboard.writeText(winPath)
      message.success(t('files.wsl_path_copied'))
      break
    }
    case 'favorite': if (file.is_directory) await toggleFavoriteDir(file.path); else await toggleFavoriteCurrentDir(); break;
    case 'compare': openDiffModal(file.path); break;
    case 'extract': await handleArchiveExtract(file.path); break;
    case 'compress-tgz': await handleArchiveCreate('tar.gz', [file.path]); break;
    case 'compress-zip': await handleArchiveCreate('zip', [file.path]); break;
    case 'delete': confirmDelete(file.path); break;
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
    // An anchor no longer among the rows (another folder, filtered out) gives no
    // range: select just the clicked row rather than slice(-1, ...).
    if (lastIndex === -1) {
      filesStore.setSelectedFiles([row.path]);
      return;
    }
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
  style: `cursor: pointer;${row._indent ? 'opacity:0.85;' : ''}${row._isParent ? 'background:var(--surface-variant);' : ''}${row.gitignored ? 'opacity:0.4;font-style:italic;' : ''}`,
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

  // Read box and path from URL query params. A name nobody has selects no box (the page says so).
  const pathFromUrl = (route.query.dir as string | undefined) ?? (route.query.path as string | undefined);
  const answered = boxAnswer.value;

  if (answered.kind === "known") {
    selectedBox.value = answered.box.name;
    if (pathFromUrl) {
      currentDir.value = pathFromUrl;
    } else if (answered.box.default_dir) {
      currentDir.value = answered.box.default_dir;
    }
  } else if (answered.kind === "none" && !selectedBox.value && boxesStore.items.length) {
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

  // Auto-open preview if ?preview=filename is set (from terminal/markdown links)
  const previewFromUrl = route.query.preview as string | undefined;
  if (previewFromUrl && selectedBox.value) {
    const fullPath = currentDir.value.endsWith('/')
      ? currentDir.value + previewFromUrl
      : currentDir.value + '/' + previewFromUrl;
    previewPath.value = fullPath;
    previewing.value = true;
  }
}

onMounted(async () => {
  await ensureData();
  if (selectedBox.value) {
    await refreshStatus(selectedBox.value);
  }

  // Attach scroll indicator listener after DOM settles
  nextTick(() => {
    const wrap = fileTableWrapRef.value;
    if (wrap) {
      const scrollEl = wrap.querySelector('.n-scrollbar-container') as HTMLElement | null;
      if (scrollEl) {
        scrollEl.addEventListener('scroll', updateScrollIndicators, { passive: true });
      }
      // Observe DOM changes (data table re-renders) to re-check scroll state
      scrollObserver = new MutationObserver(() => nextTick(updateScrollIndicators));
      scrollObserver.observe(wrap, { childList: true, subtree: true });
      updateScrollIndicators();
    }
  });
});

onUnmounted(() => {
  leavePrompt();
  scrollObserver?.disconnect();
});

/**
 * Ask whether `box` answers. An offline answer and a failed call both show the box as
 * offline, with the text of why after the status words. An answer for a box that is no
 * longer the selected one writes nothing.
 */
async function refreshStatus(box: string) {
  try {
    const stat = await boxStatus(box, tokenValue.value || null);
    if (selectedBox.value !== box) return;
    status.value = stat.status;
    statusText.value = stat.status === "online" ? null : failureText(stat);
  } catch (err) {
    if (selectedBox.value !== box) return;
    status.value = "offline";
    statusText.value = errorText(err);
  }
}

async function reloadDir() {
  if (!selectedBox.value) return;
  refreshing.value = true;
  try {
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    void reloadGit();
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
  await refreshStatus(val);
}

async function createEmptyFile() {
  if (!selectedBox.value || !newName.value.trim()) return;
  actionBusy.value = true;
  try {
    await touchFile(selectedBox.value, currentDir.value, newName.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    newName.value = "";
    newOpen.value = false;
    message.success(t('files.file_created'));
  } catch (err) {
    directoryStore.error = errorText(err);
  } finally {
    actionBusy.value = false;
  }
}

async function createNewFolder() {
  if (!selectedBox.value || !newName.value.trim()) return;
  actionBusy.value = true;
  try {
    await createFolder(selectedBox.value, currentDir.value, newName.value.trim(), tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    newName.value = "";
    newOpen.value = false;
    message.success(t('files.folder_created'));
  } catch (err) {
    directoryStore.error = errorText(err);
  } finally {
    actionBusy.value = false;
  }
}

function confirmDelete(path: string) {
  if (!selectedBox.value) return;
  const name = path.split('/').pop() || path;
  dialog.warning({
    title: t('common.delete'),
    content: t('files.delete_confirm_single', { name }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' } as any,
    onPositiveClick: () => removePath(path),
  });
}

async function removePath(path: string) {
  if (!selectedBox.value) return;
  actionBusy.value = true;
  try {
    await filesStore.doDelete(selectedBox.value, path, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    message.success(t('files.deleted'));
  } catch (err) {
    directoryStore.error = errorText(err);
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
    message.success(t('files.chmod_changed', { mode: chmodValue.value }));
    await reloadDir();
  } catch (err) {
    message.error(errorText(err));
  } finally {
    actionBusy.value = false;
  }
}

/**
 * After a "Keep both": say the name the server used (`landed` are paths as it answered)
 * and select those entries in the listing that was just loaded.
 */
function showKeptBoth(landed: string[]) {
  if (!landed.length) return;
  const nameOf = (path: string) => path.replace(/\/+$/, "").split("/").pop() || path;
  message.success(t("files.kept_both", { name: landed.map(nameOf).join(", ") }));
  const shown = new Set((directoryStore.listing?.entries || []).map((entry: any) => entry.path));
  const entries = landed.map((path) => path.replace(/\/+$/, "")).filter((path) => shown.has(path));
  if (entries.length) filesStore.setSelectedFiles(entries);
}

async function doRename() {
  if (!selectedBox.value || !renameTarget.value || !renameValue.value.trim()) return;
  const newName = renameValue.value.trim();
  if (newName === renameTarget.value.split("/").pop()) {
    // The name is unchanged: nothing to send, nothing to replace.
    renameTarget.value = null;
    renameValue.value = "";
    return;
  }
  actionBusy.value = true;
  try {
    let landed: string | null = null;
    try {
      await filesStore.doRename(selectedBox.value, renameTarget.value, newName, tokenValue.value || null);
    } catch (err) {
      // A taken name the server would replace asks; "No" leaves the dialog open and
      // everything as it was. Any other refusal shows the server's text.
      if (!(err instanceof ApiError && err.reason === "exists")) throw err;
      const choice = await askReplace(newName);
      if (choice === "no") return;
      const answer = await filesStore.doRename(
        selectedBox.value, renameTarget.value, newName, tokenValue.value || null,
        choice === "keep_both" ? { keepBoth: true } : { overwrite: true },
      );
      if (choice === "keep_both") landed = answer.path ?? null;
    }
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    if (landed) showKeptBoth([landed]);
    else message.success(t('files.renamed'));
    renameTarget.value = null;
    renameValue.value = "";
  } catch (err) {
    message.error(errorText(err));
  } finally {
    actionBusy.value = false;
  }
}

/**
 * Upload one file: "uploaded", or "skipped" when it already exists and the user keeps
 * it. The reason "exists" asks to replace (Yes re-sends with the overwrite
 * flag); any other 4xx is final; a network or server failure is retried with backoff.
 */
async function uploadOne(file: File, landed: string[]): Promise<"uploaded" | "skipped"> {
  uploadTarget.value = file;
  const maxAttempts = 3;
  let lastError: unknown = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await filesStore.doUpload(selectedBox.value!, currentDir.value, file, tokenValue.value || null);
      return "uploaded";
    } catch (err) {
      // Only a file the server says it may replace is asked about.
      if (err instanceof ApiError && err.reason === "exists") {
        filesStore.uploadError = null; // the prompt answers this one
        const choice = await askReplace(file.name);
        if (choice === "no") return "skipped";
        const answer = await filesStore.doUpload(
          selectedBox.value!, currentDir.value, file, tokenValue.value || null,
          choice === "keep_both" ? { keepBoth: true } : { overwrite: true },
        );
        if (choice === "keep_both" && answer.path) landed.push(answer.path);
        return "uploaded";
      }
      // Whether to retry is a rule about the answer, not about what failed: a refusal
      // (4xx) is final and shows its text; no answer (status 0) or a server failure
      // (5xx) is tried again.
      const status = err instanceof ApiError ? err.status : null;
      if (status !== null && status >= 400 && status < 500) throw err;
      lastError = err;
      if (attempt < maxAttempts) {
        message.warning(t('files.upload_failed_retry') + ' ' + t('files.upload_attempt', { n: String(attempt + 1), max: String(maxAttempts) }));
        await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt - 1)));
      }
    }
  }

  throw lastError;
}

// The Upload button opens the hidden file input; the list is read before the input is cleared,
// so the same file can be picked again.
async function onUploadPicked(event: Event) {
  const input = event.target as HTMLInputElement;
  try {
    await handleUpload(input.files);
  } finally {
    try { input.value = ""; } catch { /* a read-only input has nothing to clear */ }
  }
}

async function handleUpload(files: FileList | null) {
  if (!files || !files.length || !selectedBox.value) return;
  actionBusy.value = true;

  let uploaded = 0;
  const landed: string[] = [];
  for (let i = 0; i < files.length; i++) {
    const file = files.item(i);
    if (!file) continue;
    try {
      if ((await uploadOne(file, landed)) === "uploaded") {
        uploaded++;
      } else {
        message.warning(t('files.upload_skipped', { name: file.name }), { closable: true, duration: 0 });
      }
    } catch (err) {
      const errorMsg = errorText(err);
      message.error(`${file.name}: ${errorMsg}`, { closable: true, duration: 0 });
    }
  }

  uploadTarget.value = null;
  actionBusy.value = false;

  if (uploaded > 0) {
    message.success(uploaded === 1 ? t('files.uploaded') : `${uploaded} files uploaded`);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    await filesStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    showKeptBoth(landed);
  }
}

function handlePreview(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  previewPath.value = row.path;
  autoPrint.value = false;
  previewing.value = true;
}

function handlePrint(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  previewPath.value = row.path;
  autoPrint.value = true;
  previewing.value = true;
}

async function handleDownloadPdf(row: any) {
  if (!selectedBox.value || row.is_directory) return;
  await exportOnePdf(selectedBox.value, row.path, appStore.isDark ? 'dark' : 'light');
}

async function bulkDownloadPdf() {
  if (!selectedBox.value || selectedPaths.value.length === 0) return;
  // Only operate on selected file paths (skip directories). Directory entries
  // appear in selection because the table allows mixed picks; filtering here
  // is cheaper than disabling the row checkboxes.
  const filePaths: string[] = [];
  for (const p of selectedPaths.value) {
    const row = filteredRows.value.find((r: any) => r.path === p && !r.is_directory);
    if (row) filePaths.push(p);
  }
  if (filePaths.length === 0) return;
  await exportManyPdf(selectedBox.value, filePaths, appStore.isDark ? 'dark' : 'light');
}

function onPreviewShowChange(visible: boolean) {
  previewing.value = visible;
  if (!visible) autoPrint.value = false;
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
    message.error(errorText(err));
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

const SIZE_WARN_THRESHOLD = 100 * 1024 * 1024; // 100 MB
const SIZE_HARD_LIMIT = 500 * 1024 * 1024; // 500 MB

// Toolbar: download the directory currently shown, through the same flow as a row action.
async function handleDownloadCurrentDirectory() {
  const dir = currentDir.value;
  const trimmed = dir.replace(/\/+$/, '');
  // `/` has no folder name and `~` is resolved by the server; give both a fixed file name.
  const last = trimmed.split('/').pop() || '';
  const name = last === '~' ? 'home' : last || 'root';
  await handleDownloadDirectory({ path: dir, name, is_directory: true });
}

async function handleDownloadDirectory(row: any) {
  if (!selectedBox.value || !row.is_directory) return;
  let loadingMsg: { destroy: () => void } | null = null;
  try {
    loadingMsg = message.loading(t('files.zip_calculating'), { duration: 0 });
    const sized = await directorySize(selectedBox.value, row.path, tokenValue.value || null);
    // The server sends null when neither `du -sb` nor `du -sk` worked on the box.
    const size_bytes = sized.size_bytes;
    loadingMsg.destroy();
    loadingMsg = null;

    if (size_bytes === null) {
      // Unknown size: ask instead of blocking; the server still enforces its own limit.
      const proceed = window.confirm(t('files.zip_unknown_size_confirm'));
      if (!proceed) return;
    } else if (size_bytes > SIZE_HARD_LIMIT) {
      message.error(t('files.zip_too_large', { size: formatBytes(size_bytes), max: formatBytes(SIZE_HARD_LIMIT) }));
      return;
    }

    if (size_bytes !== null && size_bytes > SIZE_WARN_THRESHOLD) {
      const proceed = window.confirm(t('files.zip_large_confirm', { size: formatBytes(size_bytes) }));
      if (!proceed) return;
    }

    loadingMsg = message.loading(t('files.zip_creating'), { duration: 0 });
    const { blob, filename } = await downloadDirectory(selectedBox.value, row.path, tokenValue.value || null);
    loadingMsg.destroy();
    loadingMsg = null;

    // The server's name wins: for `~` it is the real home folder name.
    const zipName = filename ?? `${row.name || 'download'}.zip`;
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = zipName;
    anchor.click();
    URL.revokeObjectURL(objectUrl);
    message.success(t('files.zip_downloaded', { name: zipName, size: formatBytes(blob.size) }));
  } catch (err) {
    loadingMsg?.destroy();
    message.error(errorText(err));
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

function confirmBulkDelete() {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  const count = selectedPaths.value.length;
  dialog.warning({
    title: t('files.delete_selection_title'),
    content: t('files.delete_confirm_many', { count }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' } as any,
    onPositiveClick: () => bulkDelete(),
  });
}

async function bulkDelete() {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  actionBusy.value = true;
  try {
    const result = await filesStore.doBatchDelete(selectedBox.value, selectedPaths.value, tokenValue.value || null);
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    if (result.status === "partial") {
      message.warning(t('files.batch_deleted_partial', { done: result.succeeded.length, failed: result.failed.length }));
    } else {
      message.success(t('files.deleted_selection'));
    }
  } catch (err) {
    message.error(errorText(err));
  } finally {
    actionBusy.value = false;
  }
}

function openBatchModal(mode: "move" | "copy") {
  batchModal.value = { visible: true, mode };
}

async function handleBatchConfirm(destination: string) {
  if (!selectedBox.value || !selectedPaths.value.length) return;
  const box = selectedBox.value;
  const token = tokenValue.value || null;
  const mode = batchModal.value.mode;
  const paths = [...selectedPaths.value];
  batchModal.value.visible = false;
  actionBusy.value = true;
  try {
    const place = mode === "move" ? filesStore.doBatchMove : filesStore.doBatchCopy;
    const outcome = await withReplace(paths, (toSend, options) =>
      options.overwrite || options.keepBoth ? place(box, toSend, destination, token, options) : place(box, toSend, destination, token),
    );
    await directoryStore.load(box, currentDir.value, token);
    // Names the user kept are no failure; a selection that moved nothing stays as it was.
    if (outcome.succeeded.length || outcome.failed.length) filesStore.setSelectedFiles([]);
    showKeptBoth(outcome.placed.map((item) => item.placed));
    for (const item of outcome.failed) {
      message.error(`${item.path.split('/').pop() || item.path}: ${failureText(item)}`, { closable: true, duration: 0 });
    }
    const counts = { done: outcome.succeeded.length, skipped: outcome.skipped.length, failed: outcome.failed.length };
    if (!counts.skipped && !counts.failed) {
      message.success(t(mode === "move" ? 'files.batch_moved' : 'files.batch_copied', { count: counts.done }));
    } else {
      const summary = t(mode === "move" ? 'files.batch_summary_move' : 'files.batch_summary_copy', counts);
      if (counts.failed) message.warning(summary);
      else message.info(summary);
    }
  } catch (err) {
    message.error(errorText(err));
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
  const single = archivePaths.length === 1 ? archivePaths[0] : undefined;
  const archiveName = single !== undefined
    ? single.split('/').pop() + ext
    : `archive${ext}`;
  try {
    let landed: string | null = null;
    try {
      await createArchive(selectedBox.value, archivePaths, currentDir.value, archiveName, format, tokenValue.value || null);
    } catch (err) {
      // A taken archive name the server would replace asks; "No" leaves everything as it
      // was and shows no error. Any other refusal shows the server's text.
      if (!(err instanceof ApiError && err.reason === "exists")) throw err;
      const choice = await askReplace(archiveName);
      if (choice === "no") return;
      const answer = await createArchive(
        selectedBox.value, archivePaths, currentDir.value, archiveName, format, tokenValue.value || null,
        choice === "keep_both" ? { keepBoth: true } : { overwrite: true },
      );
      if (choice === "keep_both") landed = answer.path ?? null;
    }
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    filesStore.setSelectedFiles([]);
    if (landed) showKeptBoth([landed]);
    else message.success(t('files.archive_created', { name: archiveName }));
  } catch (err) {
    message.error(errorText(err));
  } finally {
    actionBusy.value = false;
  }
}

async function handleArchiveExtract(archivePath: string) {
  if (!selectedBox.value) return;
  actionBusy.value = true;
  try {
    let landed: string | null = null;
    try {
      await extractArchive(selectedBox.value, archivePath, currentDir.value, tokenValue.value || null);
    } catch (err) {
      // Files of the archive the server would replace ask once, with its count and first
      // names; "No" leaves everything as it was and shows no error. Any other refusal
      // shows the server's text.
      if (!(err instanceof ApiError && err.reason === "exists" && err.taken)) throw err;
      const choice = await askReplaceMany(err.taken);
      if (choice === "no") return;
      const answer = await extractArchive(
        selectedBox.value, archivePath, currentDir.value, tokenValue.value || null,
        choice === "keep_both" ? { keepBoth: true } : { overwrite: true },
      );
      if (choice === "keep_both") landed = answer.path ?? null;
    }
    await directoryStore.load(selectedBox.value, currentDir.value, tokenValue.value || null);
    if (landed) showKeptBoth([landed]);
    else message.success(t('files.archive_extracted'));
  } catch (err) {
    message.error(errorText(err));
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
  // Build deduplicated download names: if two files share a basename,
  // prefix each with its parent folder name to avoid Windows zip conflicts.
  const basename = (p: string) => p.split('/').pop() || 'download';
  const counts = new Map<string, number>();
  for (const p of selectedPaths.value) counts.set(basename(p), (counts.get(basename(p)) ?? 0) + 1);
  const downloadNames = selectedPaths.value.map((path) => {
    const base = basename(path);
    if ((counts.get(base) ?? 0) > 1) {
      const parts = path.split('/');
      const parent = parts.length >= 2 ? parts[parts.length - 2] : '';
      return parent ? `${parent}_${base}` : base;
    }
    return base;
  });
  // Second pass: if parent-prefix trick still produces duplicates, append _2, _3 …
  const seen = new Map<string, number>();
  const downloads = selectedPaths.value.map((path, i) => {
    const name = downloadNames[i] ?? basename(path);
    const n = seen.get(name);
    if (n === undefined) { seen.set(name, 0); return { path, name }; }
    seen.set(name, n + 1);
    const dot = name.lastIndexOf('.');
    return {
      path,
      name: dot > 0 ? `${name.slice(0, dot)}_${n + 1}${name.slice(dot)}` : `${name}_${n + 1}`,
    };
  });
  for (const { path, name } of downloads) {
    try {
      const blob = await downloadFile(selectedBox.value, path, tokenValue.value || null);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = name;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      message.error(`Failed to download ${path}: ${errorText(err)}`);
    }
  }
}

// Mobile-friendly actions button that opens context menu
const mobileActionsButton = (row: any) => {
  if (row._isParent) return null;
  return h(NButton, {
    size: "tiny",
    quaternary: true,
    onClick: (e: MouseEvent) => showContextMenu(e, row)
  }, { default: () => '...' });
};

// The `..` row is navigation only: no checkbox, no actions.
const isParentRow = (row: any) => !!row._isParent;
const tableRowClassName = (row: any) => (row._isParent ? 'files-parent-row' : '');

// Table columns — responsive: on mobile show only name + actions
const columns = computed(() => {
  const nameCol = {
    title: t("files.col_name"), key: "name",
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
          title: isExpanded ? t('files.collapse_folder') : t('files.expand_folder'),
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
      { type: "selection" as const, disabled: isParentRow },
      nameCol,
      {
        title: "", key: "actions", width: 48,
        render(row: any) { return mobileActionsButton(row); }
      }
    ];
  }

  const sortOrder = (key: string) => sortState.value.columnKey === key ? sortState.value.order : false as const;

  return [
    { type: "selection" as const, disabled: isParentRow },
    { ...nameCol, sorter: true, sortOrder: sortOrder('name') },
    {
      title: t("files.col_type"), key: "is_directory", sorter: true, sortOrder: sortOrder('is_directory'),
      render(row: any) { return row.is_directory ? t("files.type_directory") : getFileType(row.name); },
    },
    {
      title: t("files.col_size"), key: "size", sorter: true, sortOrder: sortOrder('size'),
      render(row: any) { return row.size ? formatFileSize(row.size) : "-"; },
    },
    {
      title: t("files.col_modified"), key: "modified", sorter: true, sortOrder: sortOrder('modified'),
      render(row: any) { return row.modified ? new Date(row.modified * 1000).toLocaleDateString() : "-"; },
    },
    {
      title: t("files.col_perms"), key: "mode", width: 140,
      render(row: any) {
        if (row._isParent || row.mode == null) return "-";
        return h("span", {
          style: "font-family:var(--font-mono);font-size:12px;cursor:pointer;",
          title: t('files.chmod_tooltip', { mode: row.mode.toString(8).padStart(3, '0') }),
          onClick: () => handleChmodPrompt(row),
        }, formatMode(row.mode));
      },
    },
    {
      title: t("files.col_actions"), key: "actions",
      render(row: any) {
        if (row._isParent) return null;
        const isFav = row.is_directory && favoritesStore.isFavorite(selectedBox.value, row.path);
        return h("div", { style: "display:flex;gap:4px;align-items:center;" }, [
          h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, {
              size: "tiny", quaternary: true,
              onClick: async (e: MouseEvent) => {
                e.stopPropagation()
                await navigator.clipboard.writeText(row.path)
                message.success(t('files.path_copied'))
              }
            }, { icon: () => h(NIcon, { size: 14 }, () => h(PhClipboard, { weight: 'duotone' })) }),
            default: () => t('files.copy_path')
          }),
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
          row.is_directory && h(NTooltip, { trigger: "hover", placement: "left" }, {
            trigger: () => h(NButton, {
              size: "tiny",
              quaternary: true,
              onClick: () => handleDownloadDirectory(row)
            }, { icon: () => h(NIcon, { size: 14 }, () => h(PhDownloadSimple, { weight: 'duotone' })) }),
            default: () => "Download as .zip"
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
            trigger: () => h(NButton, { size: "tiny", quaternary: true, type: "error", onClick: () => confirmDelete(row.path) }, { icon: () => h(NIcon, { size: 14 }, () => h(PhTrash, { weight: 'duotone' })) }),
            default: () => "Delete"
          }),
        ]);
      },
    },
  ];
});
</script>
<template>
  <div class="page files-workspace" data-testid="files-workspace" :aria-label="t('files.subtitle')" role="region">
    <!-- The workspace bar: box, Home and Up, the path, the actions of this folder -->
    <FileBreadcrumb
      :current-dir="currentDir"
      :git-info="currentGitInfo"
      :selected-box="selectedBox"
      :refreshing="refreshing"
      :wsl-distro="bootstrapStore.wslDistro"
      :box-options="boxOptions"
      :boxes-loading="boxesStore.loading"
      :status="status"
      :status-text="statusText"
      :is-favorite="isCurrentDirFavorite"
      @update:box="onBoxChange"
      @navigate="navigateToDirectory"
      @navigate-home="navigateHome"
      @navigate-up="navigateUp"
      @toggle-favorite="toggleFavoriteCurrentDir"
      @reload="reloadDir"
      @open-terminal="$router.push(`/terminal?box=${encodeURIComponent(selectedBox ?? '')}&dir=${encodeURIComponent(currentDir)}`)"
    />

    <NAlert v-if="offlineText" type="error" class="status-alert">{{ offlineText }}</NAlert>
    <NAlert v-if="unknownText && !selectedBox" type="warning" class="unknown-box-alert">{{ unknownText }}</NAlert>

    <FavoritesPanel compact :box="selectedBox" @openPath="navigateToDirectory" @togglePin="handlePinToggle" />

    <!-- One toolbar row -->
    <div class="files-toolbar" data-testid="files-toolbar">
      <DirectorySearchInput
        class="toolbar-search"
        :box="selectedBox"
        :token="tokenValue"
        :root="currentDir"
        :files="true"
        @select="handleSearchSelect"
      />
      <NInput v-model:value="filterQuery" class="toolbar-filter" :placeholder="t('files.filter_placeholder')" size="small">
        <template #prefix><NIcon size="14"><PhMagnifyingGlass weight="duotone" /></NIcon></template>
      </NInput>
      <div class="toolbar-kinds">
        <NButton size="small" :type="viewFilter === 'all' ? 'primary' : 'default'" @click="viewFilter = 'all'">{{ t('common.all') }}</NButton>
        <NButton size="small" :type="viewFilter === 'files' ? 'primary' : 'default'" @click="viewFilter = 'files'">{{ t('common.files') }}</NButton>
        <NButton size="small" :type="viewFilter === 'dirs' ? 'primary' : 'default'" @click="viewFilter = 'dirs'">{{ t('common.folders') }}</NButton>
      </div>
      <NPopover v-model:show="newOpen" trigger="click" placement="bottom-start">
        <template #trigger>
          <NButton size="small" :disabled="!selectedBox">
            <NIcon size="14"><PhPlus weight="bold" /></NIcon>{{ t('files.new_item') }}
          </NButton>
        </template>
        <NSpace vertical size="small" class="new-pop">
          <NInput ref="newNameRef" v-model:value="newName" :placeholder="t('files.new_filename')" size="small" @keyup.enter="createEmptyFile" />
          <NSpace size="small">
            <NButton :disabled="actionBusy || !selectedBox || !newName.trim()" size="small" type="primary" @click="createEmptyFile">
              <NIcon size="14"><PhFile weight="duotone" /></NIcon>{{ t('files.create_file') }}
            </NButton>
            <NButton :disabled="actionBusy || !selectedBox || !newName.trim()" size="small" type="primary" @click="createNewFolder">
              <NIcon size="14"><PhFolderSimple weight="duotone" /></NIcon>{{ t('files.create_folder') }}
            </NButton>
          </NSpace>
        </NSpace>
      </NPopover>
      <NButton size="small" :disabled="!selectedBox" @click="uploadInputRef?.click()">
        <NIcon size="14"><PhUploadSimple weight="duotone" /></NIcon>{{ t('files.upload') }}
      </NButton>
      <input ref="uploadInputRef" type="file" multiple hidden @change="onUploadPicked" />
      <NButton size="small" :type="showGrep ? 'primary' : 'default'" :disabled="!selectedBox" @click="showGrep = !showGrep">
        <NIcon size="14"><PhMagnifyingGlass weight="duotone" /></NIcon>{{ t('files.grep') }}
      </NButton>
      <div class="toolbar-icons">
        <NButton size="small" quaternary class="download-current-dir" @click="handleDownloadCurrentDirectory" :disabled="!selectedBox" :title="t('files.download_current_dir')">
          <NIcon size="14"><PhDownloadSimple weight="duotone" /></NIcon>
        </NButton>
        <NButton size="small" quaternary @click="expandAllDirs" :disabled="!selectedBox || !!expandingDir" :title="t('files.expand_all')">
          <NIcon size="14"><PhArrowsOutSimple weight="duotone" /></NIcon>
        </NButton>
        <NButton size="small" quaternary @click="collapseAllDirs" :disabled="expandedDirs.size === 0" :title="t('files.collapse_all')">
          <NIcon size="14"><PhArrowsInSimple weight="duotone" /></NIcon>
        </NButton>
      </div>
    </div>

    <ContentSearchInput
      v-if="showGrep"
      :box="selectedBox || null"
      :token="tokenValue || null"
      :directory="currentDir"
      @navigate="(dir: string) => navigateToDirectory(dir)"
      @select-file="(path: string) => { previewPath = path; previewing = true }"
    />

      <!-- Bulk Actions -->
      <div v-if="selectedCount > 0" class="bulk-actions">
        <NSpace size="small" align="center">
          <span class="text-muted">{{ selectedCount }} {{ t('files.selected') }}</span>
          <NButton size="small" type="error" ghost @click="confirmBulkDelete">
            <NIcon size="14"><PhTrash weight="duotone" /></NIcon>{{ t('files.delete_selected') }}
          </NButton>
          <NButton size="small" @click="bulkDownload" :disabled="selectedCount > 10">
            <NIcon size="14"><PhDownloadSimple weight="duotone" /></NIcon>{{ t('files.download_selected') }}
          </NButton>
          <NButton
            v-if="bootstrapStore.pdfAvailable"
            size="small"
            :loading="pdfBusy"
            :disabled="pdfBusy"
            :title="t('files.download_pdf')"
            @click="bulkDownloadPdf"
          >
            <NIcon size="14"><PhFilePdf weight="duotone" /></NIcon>{{ t('files.pdf') }}
          </NButton>
          <NButton size="small" @click="openBatchModal('move')">
            <NIcon size="14"><PhArrowBendUpLeft weight="duotone" /></NIcon>{{ t('files.move_to') }}
          </NButton>
          <NButton size="small" @click="openBatchModal('copy')">
            <NIcon size="14"><PhCopy weight="duotone" /></NIcon>{{ t('files.copy_to') }}
          </NButton>
          <NButton size="small" @click="handleArchiveCreate('tar.gz')">
            <NIcon size="14"><PhArchive weight="duotone" /></NIcon>.tar.gz
          </NButton>
          <NButton size="small" @click="handleArchiveCreate('zip')">
            <NIcon size="14"><PhArchive weight="duotone" /></NIcon>.zip
          </NButton>
          <NButton v-if="selectedCount === 2" size="small" @click="openDiffModal(selectedPaths[0], selectedPaths[1])">
            <NIcon size="14"><PhEye weight="duotone" /></NIcon>{{ t('files.diff_compare') }}
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

    <!-- The table fills the rest of the height -->
    <div
      class="table-region file-browser file-table-scroll-wrap"
      :class="{ 'can-scroll-up': canScrollUp, 'can-scroll-down': canScrollDown }"
      :aria-label="t('a11y.file_list')"
      aria-live="polite"
      ref="fileTableWrapRef"
    >
      <NSpin v-if="directoryStore.loading || boxesStore.loading" size="small" class="table-loading">
        <span class="text-muted">{{ t('files.loading_dir') }}</span>
      </NSpin>
      <FileUploadZone v-else class="table-zone" :current-dir="currentDir" @upload="handleUpload">
        <NDataTable :columns="columns" :data="filteredRows" size="small" striped :row-key="(row: any) => row._isParent ? '__parent__' : row.path" :checked-row-keys="selectedPaths" @update:checked-row-keys="(keys: (string | number)[]) => filesStore.setSelectedFiles(keys.map(String))" @update:sorter="handleSortChange" :row-props="getRowProps" :row-class-name="tableRowClassName" :scroll-x="isMobile ? undefined : 800" :virtual-scroll="!isMobile" flex-height style="height: 100%" />
      </FileUploadZone>
    </div>

    <NAlert v-if="directoryStore.error || boxesStore.error" type="error" closable>{{ directoryStore.error || boxesStore.error }}</NAlert>

    <div v-if="uploading" class="upload-progress">
      <NAlert type="info">
        <template #icon><div class="spinner" /></template>
        {{ t('files.uploading') }} {{ filesStore.uploadFileName }}...
        <NProgress type="line" :percentage="filesStore.uploadProgress" status="info" style="margin-top: 8px" />
      </NAlert>
    </div>
    <NAlert v-if="filesStore.uploadError" type="error" closable>{{ filesStore.uploadError }}</NAlert>

    <!-- File Preview Modal -->
    <FilePreviewModal
      ref="previewModalRef"
      :show="previewing"
      :path="previewPath"
      :box="selectedBox"
      :token="tokenValue"
      :theme="editorTheme"
      :auto-print="autoPrint"
      :siblings="previewSiblings"
      @update:show="onPreviewShowChange"
      @edit="(path: string) => handleEdit({ path, is_directory: false })"
      @compare="(path: string) => openDiffModal(path)"
      @navigate="(path: string) => { previewPath = path; autoPrint = false }"
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
    <NModal v-model:show="renameModalVisible" preset="dialog" :title="t('common.rename')" :positive-text="t('common.rename')" :negative-text="t('common.cancel')" @positive-click="doRename" @negative-click="renameModalVisible = false" style="max-width: 400px">
      <NSpace vertical size="small">
        <p class="text-muted" style="font-size:13px;">{{ renameTarget?.split('/').pop() }}</p>
        <NInput v-model:value="renameValue" :placeholder="t('files.rename_placeholder')" size="small" @keyup.enter="doRename" />
      </NSpace>
    </NModal>

    <!-- Chmod Modal -->
    <NModal v-model:show="chmodModalVisible" preset="dialog" :title="t('files.chmod_title')" :positive-text="t('files.chmod_apply')" :negative-text="t('common.cancel')" @positive-click="applyChmod" @negative-click="chmodModalVisible = false" style="max-width: 360px">
      <NSpace vertical size="small">
        <p class="text-muted" style="font-size:13px;">{{ chmodTarget.path.split('/').pop() }}</p>
        <p v-if="chmodTarget.currentMode != null" style="font-size:12px;font-family:var(--font-mono);">{{ t('files.chmod_current') }} {{ formatMode(chmodTarget.currentMode) }} ({{ chmodTarget.currentMode.toString(8).padStart(3, '0') }})</p>
        <NInput v-model:value="chmodValue" :placeholder="t('files.chmod_placeholder')" size="small" style="font-family:var(--font-mono);" @keyup.enter="applyChmod" />
        <p v-if="chmodValue" style="font-size:12px;font-family:var(--font-mono);color:var(--muted);">{{ t('files.preview_label') }} {{ formatMode(parseInt(chmodValue, 8) || 0) }}</p>
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
/* Scroll indicators: gradient shadows showing more content above/below */
.file-table-scroll-wrap::before,
.file-table-scroll-wrap::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  height: 24px;
  pointer-events: none;
  z-index: 2;
  opacity: 0;
  transition: opacity var(--dur-base) var(--ease-out);
}

.file-table-scroll-wrap::before {
  top: 0;
  background: linear-gradient(to bottom, rgba(var(--accent-rgb), 0.15), transparent);
  border-top: 2px solid rgba(var(--accent-rgb), 0.3);
}

.file-table-scroll-wrap::after {
  bottom: 0;
  background: linear-gradient(to top, rgba(var(--accent-rgb), 0.15), transparent);
  border-bottom: 2px solid rgba(var(--accent-rgb), 0.3);
}

.file-table-scroll-wrap.can-scroll-up::before {
  opacity: 1;
}

.file-table-scroll-wrap.can-scroll-down::after {
  opacity: 1;
}

/* The workspace is the height of the content area; the table takes what the bars leave. */
.page {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
}

.page > * {
  flex: 0 0 auto;
}

.page > .table-region {
  flex: 1 1 auto;
}

.table-region {
  position: relative;
  min-height: 200px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  overflow: hidden;
}

/* The upload zone is the table's frame: it has the region's size, the table scrolls inside it. */
.table-region .table-zone {
  position: absolute;
  inset: 0;
  border-radius: 0;
}

.table-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.files-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.toolbar-search {
  flex: 1 1 220px;
  min-width: 0;
  max-width: 360px;
}

.toolbar-filter {
  flex: 0 1 150px;
  min-width: 100px;
}

.toolbar-kinds {
  display: flex;
  flex: 0 0 auto;
  gap: 4px;
}

.toolbar-icons {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}

/* The column a table is sorted by is not tinted: the arrow in its title says so. */
.file-browser :deep(.n-data-table) {
  --n-merged-td-color-sorting: transparent !important;
  --n-merged-td-color-hover-sorting: transparent !important;
}

.new-pop {
  min-width: 220px;
}

.bulk-actions {
  padding: 8px 12px;
  background: var(--surface-variant);
  border-radius: 8px;
  border: 1px solid var(--stroke);
}

.recent-paths {
  padding: 4px 12px;
  background: var(--surface-variant);
  border-radius: 6px;
  overflow-x: auto;
  white-space: nowrap;
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
  animation: spin var(--dur-loop) linear infinite;
}

.small {
  font-size: 12px;
}

.text-muted {
  color: var(--muted);
}

/* Mobile optimizations */
@media (max-width: 767px) {
  .page {
    gap: 6px;
  }

  .toolbar-search {
    flex: 1 1 100%;
    max-width: none;
  }

  .toolbar-filter {
    flex: 1 1 80px;
    min-width: 80px;
    width: auto;
  }

  .toolbar-kinds :deep(.n-button) {
    padding: 0 8px;
  }

  .bulk-actions :deep(.n-space) {
    flex-wrap: wrap;
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
  .table-region {
    border: 2px solid var(--stroke);
  }
}

/* Focus management for accessibility */
.file-browser :deep(.n-data-table-tbody .n-data-table-tr:focus-within) {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

/* `..` has no selection checkbox */
.file-browser :deep(.files-parent-row .n-data-table-td--selection .n-checkbox) {
  visibility: hidden;
}

/* Ensure proper contrast for selected rows */
.file-browser :deep(.n-data-table-tbody .n-data-table-tr--checked) {
  background: var(--accent-bg);
}
</style>
