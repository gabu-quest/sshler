import { render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import FilesView from "./FilesView.vue";

const { routeState, messageSpy, tableMode } = vi.hoisted(() => ({
  routeState: { dir: "/home/u/proj" },
  messageSpy: { success: vi.fn(), error: vi.fn() },
  // "stub" renders the hand-built table below; "real" renders Naive UI's own NDataTable.
  tableMode: { value: "stub" as "stub" | "real" },
}));

// the real i18n, so the Japanese words can be read
vi.mock("@/i18n", async () => await vi.importActual("@/i18n"));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "local", dir: routeState.dir }, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
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
      { name: "b.zip", path: "/home/u/proj/b.zip", is_directory: false, size: 9, modified: 1, mode: 420 },
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
    NPopover: stub('<div class="stub-popover"><slot name="trigger" /><div class="popover-body"><slot /></div></div>', ["show", "trigger", "placement"]),
    NProgress: stub('<div class="stub-progress" />'),
    NSelect: stub('<select class="stub-select" />', ["value", "options"]),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<div class="stub-spin"><slot /></div>'),
    NTag: stub('<span class="stub-tag"><slot /></span>'),
    NTooltip: stub('<span class="stub-tooltip"><slot name="trigger" /><span class="tip"><slot /></span></span>'),
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
    "PhArchive", "PhPlus",
  ];
  return Object.fromEntries(names.map((n) => [n, stub(n)]));
});

const i18nPlugin = { install(app: App) { createI18n(app); } };

async function mountLoaded() {
  const utils = render(FilesView, { global: { plugins: [i18nPlugin] } });
  await waitFor(() => expect(utils.container.querySelectorAll(".row").length).toBe(4));
  return utils;
}

function tips(container: Element, path: string): string[] {
  return Array.from(container.querySelectorAll(`.row[data-path="${path}"] .tip`)).map((el) => el.textContent ?? "");
}

const JA_FOLDER = ["パスをコピー", "お気に入りに追加", "ここでターミナルを開く", "zipでダウンロード", "名前変更", "削除"];
const JA_FILE = ["パスをコピー", "プレビュー", "編集", "ダウンロード", "名前変更", "削除"];

describe("FilesView: the row action tooltips", () => {
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
    localStorage.clear();
    createI18n({ provide: () => {} } as unknown as App);
  });

  it("English: the tooltips read as they always did", async () => {
    const { container } = await mountLoaded();
    expect(tips(container, "/home/u/proj/src")).toEqual(["Copy Path", "Add to Favorites", "Open Terminal Here", "Download as .zip", "Rename", "Delete"]);
    expect(tips(container, "/home/u/proj/a.txt")).toEqual(["Copy Path", "Preview", "Edit", "Download", "Rename", "Delete"]);
  });

  // Mutation: a tooltip left as a literal English string fails here.
  it("Japanese: every row tooltip is Japanese", async () => {
    localStorage.setItem("sshler:locale", "ja");
    const { container } = await mountLoaded();
    expect(tips(container, "/home/u/proj/src")).toEqual(JA_FOLDER);
    expect(tips(container, "/home/u/proj/a.txt")).toEqual(JA_FILE);
  });
});
