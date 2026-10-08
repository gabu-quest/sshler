import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import * as http from "@/api/http";
import { useFilesStore } from "@/stores/files";
import FilesView from "./FilesView.vue";

const { routeState, messageSpy, tableMode } = vi.hoisted(() => ({
  routeState: { dir: "/home/u/proj" },
  messageSpy: { success: vi.fn(), error: vi.fn() },
  // "stub" renders the hand-built table below; "real" renders Naive UI's own NDataTable.
  tableMode: { value: "stub" as "stub" | "real" },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "local", dir: routeState.dir }, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, basic_auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [{ name: "local", host: "local", favorites: [], pinned: false, default_dir: "/" }]),
  fetchBox: vi.fn(async () => ({ name: "local", host: "local", favorites: [], pinned: false })),
  fetchDirectory: vi.fn(async () => ({
    box: "local", directory: "/home/u/proj",
    entries: [
      { name: "src", path: "/home/u/proj/src", is_directory: true, size: null, modified: null, mode: 493 },
      { name: "a.txt", path: "/home/u/proj/a.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
    ],
  })),
  toggleFavorite: vi.fn(async () => ({ favorites: [] })),
  togglePin: vi.fn(async () => ({ pinned: false })),
  boxStatus: vi.fn(async () => ({ status: "online" })),
  touchFile: vi.fn(), createFolder: vi.fn(), downloadFile: vi.fn(),
  downloadDirectory: vi.fn(async () => ({ blob: new Blob(["zip"]), filename: null as string | null })),
  directorySize: vi.fn(async () => ({ size_bytes: 10 })),
  gitInfo: vi.fn(async () => null),
  chmodFile: vi.fn(), createArchive: vi.fn(), extractArchive: vi.fn(),
}));

vi.mock("@/composables/usePdfExport", () => ({
  usePdfExport: () => ({ exportOne: vi.fn(), exportMany: vi.fn(), busy: ref(false) }),
}));

const { passthrough } = vi.hoisted(() => ({
  passthrough: (name: string) => ({ default: { name, template: `<div class="stub-${name}"><slot /></div>` } }),
}));
vi.mock("@/components/FavoritesPanel.vue", () => passthrough("FavoritesPanel"));
vi.mock("@/components/FileBreadcrumb.vue", () => passthrough("FileBreadcrumb"));
vi.mock("@/components/FileUploadZone.vue", () => passthrough("FileUploadZone"));
vi.mock("@/components/FilePreviewModal.vue", () => passthrough("FilePreviewModal"));
vi.mock("@/components/FileEditorModal.vue", () => passthrough("FileEditorModal"));
vi.mock("@/components/FileDiffModal.vue", () => passthrough("FileDiffModal"));
vi.mock("@/components/BatchMoveModal.vue", () => passthrough("BatchMoveModal"));
vi.mock("@/components/ContentSearchInput.vue", () => passthrough("ContentSearchInput"));
vi.mock("@/components/ContextMenu.vue", () => passthrough("ContextMenu"));
vi.mock("@/components/DirectorySearchInput.vue", () => passthrough("DirectorySearchInput"));

vi.mock("naive-ui", async () => {
  const { defineComponent, h } = await import("vue");
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  // Renders every cell the real table would: the selection checkbox unless the
  // column disables that row, and each column's render() output.
  const actual = await vi.importActual<typeof import("naive-ui")>("naive-ui");
  const NDataTable = defineComponent({
    inheritAttrs: false,
    props: ["columns", "data"],
    setup(props: any, { attrs }: any) {
      return () => {
        if (tableMode.value === "real") return h(actual.NDataTable, { ...attrs, columns: props.columns, data: props.data });
        return h("div", { class: "stub-datatable" }, props.data.map((row: any) =>
          h("div", {
            class: row._isParent ? "row row-parent" : "row row-normal",
            "data-path": row.path,
            onClick: (e: MouseEvent) => attrs["row-props"]?.(row).onClick(e),
          },
            props.columns.map((col: any) => {
              if (col.type === "selection") {
                return col.disabled?.(row) ? null : h("input", { type: "checkbox", class: "row-checkbox" });
              }
              return h("div", { class: `cell-${col.key}` }, col.render ? col.render(row) : null);
            }))));
      };
    },
  });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :class="$attrs.class" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["type", "disabled", "quaternary", "tertiary", "size", "loading", "title"],
      ["click"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NDataTable,
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
    useMessage: () => ({ success: messageSpy.success, error: messageSpy.error, warning: vi.fn(), info: vi.fn(), loading: vi.fn(() => ({ destroy: vi.fn() })) }),
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

const i18nPlugin = { install(app: App) { createI18n(app); } };

async function mountLoaded(rows = 3) {
  const utils = render(FilesView, { global: { plugins: [i18nPlugin] } });
  await waitFor(() => expect(utils.container.querySelectorAll(".row").length).toBe(rows));
  return utils;
}

function setWidth(px: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: px });
}

describe("FilesView: the `..` row is navigation only", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
    setWidth(1024);
  });

  // Mutation: remove `if (row._isParent) return null;` from the desktop actions render
  // (the pre-fix tree) and the `..` row renders its download/rename/delete buttons.
  it("desktop: the `..` row renders no action buttons and no checkbox; a normal row does", async () => {
    setWidth(1400);
    const { container } = await mountLoaded();
    const parent = container.querySelector(".row-parent")!;
    expect(parent.querySelectorAll("button").length).toBe(0);
    expect(parent.querySelectorAll("input[type=checkbox]").length).toBe(0);
    expect(parent.querySelectorAll('[data-icon="PhDownloadSimple"]').length).toBe(0);
    // Positive control: the same table gives the "src" row its checkbox and 6 directory actions
    // (copy path, favorite, terminal, download zip, rename, delete = 6 buttons).
    const src = container.querySelectorAll(".row-normal")[0]!;
    expect(src.querySelectorAll("input[type=checkbox]").length).toBe(1);
    expect(src.querySelectorAll(".cell-actions button").length).toBe(6);
  });

  // Mutation: remove `if (row._isParent) return null;` from mobileActionsButton.
  it("mobile: the `..` row renders no actions button and no checkbox; a normal row does", async () => {
    setWidth(400);
    const { container } = await mountLoaded();
    const parent = container.querySelector(".row-parent")!;
    expect(parent.querySelectorAll("button").length).toBe(0);
    expect(parent.querySelectorAll("input[type=checkbox]").length).toBe(0);
    const src = container.querySelectorAll(".row-normal")[0]!;
    expect(src.querySelectorAll(".cell-actions button").length).toBe(1);
    expect(src.querySelectorAll("input[type=checkbox]").length).toBe(1);
  });
});

describe("FilesView: download the current folder", () => {
  let anchors: { download: string; href: string }[];
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    setWidth(1400);
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
    anchors = [];
    URL.createObjectURL = vi.fn(() => "blob:zip");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push({ download: this.download, href: this.href });
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    setWidth(1024);
  });

  // Mutation: make handleDownloadCurrentDirectory a no-op, or pass the parent path.
  it("the toolbar button zips the folder being viewed as <name>.zip", async () => {
    const { container } = await mountLoaded();
    const btn = container.querySelector("button.download-current-dir") as HTMLButtonElement;
    expect(btn.querySelector('[data-icon="PhDownloadSimple"]')).not.toBeNull();
    await fireEvent.click(btn);
    await waitFor(() => expect(anchors.length).toBe(1));
    expect(http.directorySize).toHaveBeenCalledWith("local", "/home/u/proj", "tok");
    expect(http.downloadDirectory).toHaveBeenCalledWith("local", "/home/u/proj", "tok");
    expect(anchors[0]?.download).toBe("proj.zip");
  });

  // Mutation killed: changing bulkDownload's de-duplication (no parent prefix for a
  // shared basename, or no `_1` suffix when the prefixed names still collide) gives
  // two anchors the same file name, so the browser overwrites one download.
  it("bulk download names same-basename files apart: parent prefix, then _1", async () => {
    vi.mocked(http.downloadFile).mockResolvedValue(new Blob(["x"]));
    const utils = await mountLoaded();
    useFilesStore().setSelectedFiles([
      "/home/u/proj/src/a.txt",
      "/home/u/proj/lib/a.txt",
      "/srv/src/a.txt",
      "/home/u/proj/b.txt",
    ]);
    await fireEvent.click(await utils.findByText("Download Selected"));
    await waitFor(() => expect(anchors.length).toBe(4));
    expect(anchors.map((a) => a.download)).toEqual(["src_a.txt", "lib_a.txt", "src_a_1.txt", "b.txt"]);
    expect(vi.mocked(http.downloadFile).mock.calls.map((c) => c[1])).toEqual([
      "/home/u/proj/src/a.txt", "/home/u/proj/lib/a.txt", "/srv/src/a.txt", "/home/u/proj/b.txt",
    ]);
  });

  // Mutation: bypass the size confirmation (call downloadDirectory without it).
  it("it goes through the same size confirmation: declining downloads nothing", async () => {
    vi.mocked(http.directorySize).mockResolvedValueOnce({ size_bytes: 200 * 1024 * 1024 });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { container } = await mountLoaded();
    await fireEvent.click(container.querySelector("button.download-current-dir")!);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(confirm).toHaveBeenCalledWith("This directory is 200.0 MB. Download as .zip?");
    expect(http.downloadDirectory).not.toHaveBeenCalled();
    expect(anchors.length).toBe(0);
  });
});

const ENTRIES = [
  { name: "src", path: "/home/u/proj/src", is_directory: true, size: null, modified: null, mode: 493 },
  { name: "lib", path: "/home/u/proj/lib", is_directory: true, size: null, modified: null, mode: 493 },
  { name: "a.txt", path: "/home/u/proj/a.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
  { name: "b.txt", path: "/home/u/proj/b.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
];

function listFour() {
  vi.mocked(http.fetchDirectory).mockResolvedValue({ box: "local", directory: "/home/u/proj", entries: ENTRIES } as any);
}

function stubMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  });
}

describe("FilesView: selection never includes the `..` row", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    stubMatchMedia();
    routeState.dir = "/home/u/proj";
    listFour();
  });
  afterEach(() => {
    tableMode.value = "stub";
    vi.clearAllMocks();
    setWidth(1024);
  });

  // Mutation: remove `disabled: isParentRow` from the selection column. Naive UI's
  // select-all then also checks the `..` row (key "__parent__"). This uses Naive UI's real
  // table (mobile width: no virtual scroll), not the stub, because the behaviour under test
  // is Naive UI skipping disabled rows.
  it("select-all selects the four entries and not `..`", async () => {
    setWidth(400);
    tableMode.value = "real";
    const { container } = render(FilesView, { global: { plugins: [i18nPlugin] } });
    await waitFor(() => expect(container.querySelectorAll("tbody tr").length).toBe(5));
    const files = useFilesStore();
    const headerBox = container.querySelector("thead .n-checkbox") as HTMLElement;
    await fireEvent.click(headerBox);
    await waitFor(() => expect(files.selectedFiles.length).toBeGreaterThan(0));
    expect([...files.selectedFiles].sort()).toEqual(ENTRIES.map((e) => e.path).sort());
  });

  // Mutation: start the range at row 0 (`const start = 0`), which spans the `..` row;
  // its path (the parent folder) would land in the selection.
  it("shift-click range from a.txt up to lib (the first folder) selects a.txt, lib, src and not `..`", async () => {
    setWidth(1400);
    const { container } = await mountLoaded(5);
    const files = useFilesStore();
    const row = (path: string) => container.querySelector(`[data-path="${path}"]`)!;
    await fireEvent.click(row("/home/u/proj/a.txt"), { ctrlKey: true });
    expect(files.selectedFiles).toEqual(["/home/u/proj/a.txt"]);
    await fireEvent.click(row("/home/u/proj/lib"), { shiftKey: true });
    expect(files.selectedFiles).toEqual([
      "/home/u/proj/a.txt", "/home/u/proj/lib", "/home/u/proj/src",
    ]);
    expect(files.selectedFiles).not.toContain("/home/u");
  });

  // Mutation killed: dropping the `lastIndex === -1` guard in handleRowClick. With
  // an anchor that is not among the rows, findIndex gives -1 and slice(-1, 4) is
  // empty, so the selection stays ["/elsewhere/x"] and a.txt is not selected.
  it("shift-click with an anchor from another folder selects only the clicked row", async () => {
    setWidth(1400);
    const { container } = await mountLoaded(5);
    const files = useFilesStore();
    files.setSelectedFiles(["/elsewhere/x"]);
    await fireEvent.click(container.querySelector('[data-path="/home/u/proj/a.txt"]')!, { shiftKey: true });
    expect(files.selectedFiles).toEqual(["/home/u/proj/a.txt"]);
  });
});

describe("FilesView: toolbar download at `~` and `/`", () => {
  let anchors: { download: string }[];
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    setWidth(1400);
    stubMatchMedia();
    anchors = [];
    URL.createObjectURL = vi.fn(() => "blob:zip");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push({ download: this.download });
    });
  });
  afterEach(() => {
    routeState.dir = "/home/u/proj";
    vi.restoreAllMocks();
    vi.clearAllMocks();
    setWidth(1024);
  });

  // Mutation: restore `name: name === '~' ? '' : name` (empty name): the file is
  // "download.zip" and the message "Downloaded .zip".
  it.each([
    ["~", "home"],
    ["/", "root"],
  ])("at %s the zip is named %s.zip and the message says so", async (dir, name) => {
    routeState.dir = dir;
    vi.mocked(http.fetchDirectory).mockResolvedValue({ box: "local", directory: dir, entries: ENTRIES } as any);
    const { container } = await mountLoaded(4);
    await fireEvent.click(container.querySelector("button.download-current-dir")!);
    await waitFor(() => expect(anchors.length).toBe(1));
    expect(http.directorySize).toHaveBeenCalledWith("local", dir, "tok");
    expect(http.downloadDirectory).toHaveBeenCalledWith("local", dir, "tok");
    expect(anchors[0]?.download).toBe(`${name}.zip`);
    expect(messageSpy.success).toHaveBeenCalledWith(`Downloaded ${name}.zip (3 B)`);
  });

  // Mutation killed: naming the zip from the row (`${row.name}.zip`) instead of
  // the server's Content-Disposition name: at `~` the file becomes "home.zip"
  // rather than the real home folder's name ("gabu.zip" from the server).
  it("at ~ the zip takes the server's name for the home folder", async () => {
    routeState.dir = "~";
    vi.mocked(http.fetchDirectory).mockResolvedValue({ box: "local", directory: "~", entries: ENTRIES } as any);
    vi.mocked(http.downloadDirectory).mockResolvedValueOnce({ blob: new Blob(["zip"]), filename: "gabu.zip" });
    const { container } = await mountLoaded(4); // no `..` row at ~
    await fireEvent.click(container.querySelector("button.download-current-dir")!);
    await waitFor(() => expect(anchors.length).toBe(1));
    expect(http.downloadDirectory).toHaveBeenCalledWith("local", "~", "tok");
    expect(anchors[0]?.download).toBe("gabu.zip");
    expect(messageSpy.success).toHaveBeenCalledWith("Downloaded gabu.zip (3 B)");
  });
});
