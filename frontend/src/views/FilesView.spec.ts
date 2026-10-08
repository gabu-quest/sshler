import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import FilesView from "./FilesView.vue";

const SORT_STATE_KEY = "sshler:files:sort";
const VIEW_FILTER_KEY = "sshler:files:viewFilter";

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// No boxes returned — keeps the component in its "nothing selected" state so
// we can inspect the hydrated sort/filter refs without needing a live backend.
vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "test-token", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, basic_auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => []),
  fetchBox: vi.fn(async () => ({ name: "local", host: "local", favorites: [], pinned: false })),
  fetchDirectory: vi.fn(async () => ({ box: "local", directory: "/", entries: [] })),
  toggleFavorite: vi.fn(async () => ({ favorites: [] })),
  togglePin: vi.fn(async () => ({ pinned: false })),
  boxStatus: vi.fn(async () => ({ status: "online" })),
  touchFile: vi.fn(), createFolder: vi.fn(), downloadFile: vi.fn(),
  downloadDirectory: vi.fn(), directorySize: vi.fn(), gitInfo: vi.fn(async () => null),
  chmodFile: vi.fn(), createArchive: vi.fn(), extractArchive: vi.fn(),
}));

vi.mock("@/composables/usePdfExport", () => ({
  usePdfExport: () => ({ exportOne: vi.fn(), exportMany: vi.fn(), busy: ref(false) }),
}));

// Child components are irrelevant to sort/filter hydration — stub them out.
// (vi.mock calls must stay static/literal so the hoisting transform can find them.)
vi.mock("@/components/FavoritesPanel.vue", () => ({ default: { name: "FavoritesPanel", template: '<div class="stub-FavoritesPanel" />' } }));
vi.mock("@/components/FileBreadcrumb.vue", () => ({ default: { name: "FileBreadcrumb", template: '<div class="stub-FileBreadcrumb" />' } }));
vi.mock("@/components/FileUploadZone.vue", () => ({ default: { name: "FileUploadZone", template: '<div class="stub-FileUploadZone"><slot /></div>' } }));
vi.mock("@/components/FilePreviewModal.vue", () => ({ default: { name: "FilePreviewModal", template: '<div class="stub-FilePreviewModal" />' } }));
vi.mock("@/components/FileEditorModal.vue", () => ({ default: { name: "FileEditorModal", template: '<div class="stub-FileEditorModal" />' } }));
vi.mock("@/components/FileDiffModal.vue", () => ({ default: { name: "FileDiffModal", template: '<div class="stub-FileDiffModal" />' } }));
vi.mock("@/components/BatchMoveModal.vue", () => ({ default: { name: "BatchMoveModal", template: '<div class="stub-BatchMoveModal" />' } }));
vi.mock("@/components/ContentSearchInput.vue", () => ({ default: { name: "ContentSearchInput", template: '<div class="stub-ContentSearchInput" />' } }));
vi.mock("@/components/ContextMenu.vue", () => ({ default: { name: "ContextMenu", template: '<div class="stub-ContextMenu" />' } }));
vi.mock("@/components/DirectorySearchInput.vue", () => ({ default: { name: "DirectorySearchInput", template: '<div class="stub-DirectorySearchInput" />' } }));

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :data-type="type" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["type", "disabled", "quaternary", "tertiary", "size", "loading", "title"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NDataTable: stub('<div class="stub-datatable" :data-columns="JSON.stringify(columns.map((c) => ({ key: c.key, sortOrder: c.sortOrder })))" />', ["columns", "data", "sortOrder"]),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NInput: stub('<input class="stub-input" />', ["value", "size", "placeholder"]),
    NModal: stub('<div class="stub-modal" v-if="show"><slot /></div>', ["show"]),
    NProgress: stub('<div class="stub-progress" />'),
    NSelect: stub('<select class="stub-select" />', ["value", "options"]),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<div class="stub-spin"><slot /></div>'),
    NTag: stub('<span class="stub-tag"><slot /></span>'),
    NTooltip: stub('<span class="stub-tooltip"><slot name="trigger" /></span>'),
    useDialog: () => ({ warning: vi.fn() }),
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(() => ({ destroy: vi.fn() })) }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  const names = [
    "PhClockCounterClockwise", "PhFile", "PhFilePdf", "PhList", "PhFolderSimple", "PhStar",
    "PhUploadSimple", "PhEye", "PhPencil", "PhPrinter", "PhDownloadSimple", "PhTextAa", "PhCopy",
    "PhClipboard", "PhTrash", "PhFolder", "PhMagnifyingGlass", "PhGear", "PhTerminalWindow",
    "PhArrowBendUpLeft", "PhCaretRight", "PhCaretDown", "PhArrowsOutSimple", "PhArrowsInSimple",
    "PhArchive",
  ];
  return Object.fromEntries(names.map((n) => [n, stub(n)]));
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

function mountFilesView() {
  return render(FilesView, { global: { plugins: [i18nPlugin] } });
}

describe("FilesView — persisted sort/filter hydration", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    if (!window.matchMedia) {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: (query: string) => ({
          matches: false, media: query, onchange: null,
          addEventListener: () => {}, removeEventListener: () => {},
          addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
        }),
      });
    }
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("defaults to name/ascend sort and 'all' filter when nothing is stored", async () => {
    const { container, findByText } = mountFilesView();
    await findByText("All");
    const buttons = Array.from(container.querySelectorAll(".stub-button"));
    const allButton = buttons.find((b) => b.textContent?.trim() === "All");
    expect(allButton?.getAttribute("data-type")).toBe("primary");
    const columns = JSON.parse(container.querySelector(".stub-datatable")!.getAttribute("data-columns")!);
    const nameCol = columns.find((c: any) => c.key === "name");
    expect(nameCol.sortOrder).toBe("ascend");
  });

  it("hydrates viewFilter from localStorage on mount", async () => {
    localStorage.setItem(VIEW_FILTER_KEY, "dirs");
    const { container, findByText } = mountFilesView();
    await findByText("Folders");
    const buttons = Array.from(container.querySelectorAll(".stub-button"));
    const dirsButton = buttons.find((b) => b.textContent?.trim() === "Folders");
    const allButton = buttons.find((b) => b.textContent?.trim() === "All");
    expect(dirsButton?.getAttribute("data-type")).toBe("primary");
    expect(allButton?.getAttribute("data-type")).toBe("default");
  });

  it("hydrates sortState from localStorage on mount", async () => {
    localStorage.setItem(SORT_STATE_KEY, JSON.stringify({ columnKey: "modified", order: "descend" }));
    const { container, findByText } = mountFilesView();
    await findByText("All");
    const columns = JSON.parse(container.querySelector(".stub-datatable")!.getAttribute("data-columns")!);
    const modifiedCol = columns.find((c: any) => c.key === "modified");
    expect(modifiedCol.sortOrder).toBe("descend");
    const nameCol = columns.find((c: any) => c.key === "name");
    expect(nameCol.sortOrder).toBe(false);
  });

  it("ignores malformed stored sort state and falls back to the default", async () => {
    localStorage.setItem(SORT_STATE_KEY, "not-json");
    const { container, findByText } = mountFilesView();
    await findByText("All");
    const columns = JSON.parse(container.querySelector(".stub-datatable")!.getAttribute("data-columns")!);
    const nameCol = columns.find((c: any) => c.key === "name");
    expect(nameCol.sortOrder).toBe("ascend");
  });

  it("ignores an unknown stored view filter value and falls back to 'all'", async () => {
    localStorage.setItem(VIEW_FILTER_KEY, "bogus");
    const { container, findByText } = mountFilesView();
    await findByText("All");
    const buttons = Array.from(container.querySelectorAll(".stub-button"));
    const allButton = buttons.find((b) => b.textContent?.trim() === "All");
    expect(allButton?.getAttribute("data-type")).toBe("primary");
  });

  it("persists a new filter selection to localStorage when clicked", async () => {
    const { findByText } = mountFilesView();
    const dirsButton = await findByText("Folders");
    await fireEvent.click(dirsButton);
    expect(localStorage.getItem(VIEW_FILTER_KEY)).toBe("dirs");
  });
});
