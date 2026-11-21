import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import { boxStatus, fetchDirectory } from "@/api/http";
import { ApiError } from "@/api/apiError";
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
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "local", favorites: [], pinned: false, default_dir: "/" },
    { name: "other", host: "other", favorites: [], pinned: false, default_dir: "/" },
  ]),
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
    NSelect: stub(
      `<select class="stub-select" @change="$emit('update:value', $event.target.value)">
        <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>`,
      ["value", "options"],
      ["update:value"],
    ),
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

function alertText(container: Element, index = 0): string {
  return container.querySelectorAll(".stub-alert")[index]?.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

describe("FilesView: the status of the box", () => {
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
  });

  it("an offline box shows why after the status words", async () => {
    vi.mocked(boxStatus).mockResolvedValueOnce({
      name: "local", status: "offline", latency_ms: null, error: "local did not answer", reason: "unreachable",
    });
    const { container } = render(FilesView, { global: { plugins: [i18nPlugin] } });
    await waitFor(() =>
      expect(alertText(container)).toBe("local is offline: Cannot connect to the box — local did not answer"),
    );
  });

  it("a failed status call shows the box offline with the failure's message", async () => {
    vi.mocked(boxStatus).mockRejectedValueOnce(new ApiError(0, "network", ""));
    const { container } = render(FilesView, { global: { plugins: [i18nPlugin] } });
    await waitFor(() =>
      expect(alertText(container)).toBe("local is offline: No answer from sshler; check the connection"),
    );
    expect(messageSpy.error).toHaveBeenCalledTimes(0);
  });

  describe("a box switched while its status call is still out", () => {
    async function switchWhileOut() {
      let settleLocal!: { resolve: (value: Awaited<ReturnType<typeof boxStatus>>) => void; reject: (reason: unknown) => void };
      vi.mocked(boxStatus)
        .mockImplementationOnce(() => new Promise((resolve, reject) => { settleLocal = { resolve, reject }; }))
        .mockResolvedValueOnce({ name: "other", status: "online", latency_ms: 3, error: null, reason: null });
      const { container } = render(FilesView, { global: { plugins: [i18nPlugin] } });
      await waitFor(() => expect(boxStatus).toHaveBeenCalledTimes(1));
      const select = container.querySelector(".stub-select") as HTMLSelectElement;
      await fireEvent.change(select, { target: { value: "other" } });
      await waitFor(() => expect(alertText(container)).toBe("other is online"));
      return { container, settleLocal };
    }

    it("an answer of the first box that arrives last is not shown for the second", async () => {
      const { container, settleLocal } = await switchWhileOut();
      settleLocal.resolve({ name: "local", status: "offline", latency_ms: null, error: "local did not answer", reason: "unreachable" });
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(alertText(container)).toBe("other is online");
    });

    it("a failure of the first box's call that arrives last is not shown for the second", async () => {
      const { container, settleLocal } = await switchWhileOut();
      settleLocal.reject(new ApiError(0, "network", ""));
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(alertText(container)).toBe("other is online");
    });
  });

  it("a folder that cannot be read stays closed, shows why, and a second click asks again", async () => {
    const listing = await vi.mocked(fetchDirectory)("local", "/home/u/proj", null);
    vi.mocked(fetchDirectory).mockImplementation(async (_box, path) => {
      if (path === "/home/u/proj/src") throw new ApiError(403, "denied", "/home/u/proj/src: permission denied");
      return listing;
    });
    vi.mocked(fetchDirectory).mockClear();
    const { container } = render(FilesView, { global: { plugins: [i18nPlugin] } });
    await waitFor(() => expect(container.querySelector('[data-path="/home/u/proj/src"] button')).not.toBe(null));
    const caret = () => container.querySelector('[data-path="/home/u/proj/src"] button') as HTMLButtonElement;
    const asked = () => vi.mocked(fetchDirectory).mock.calls.filter((call) => call[1] === "/home/u/proj/src").length;

    await fireEvent.click(caret());
    await waitFor(() => expect(alertText(container, 1)).toBe("Permission denied — /home/u/proj/src: permission denied"));
    expect(caret().title).toBe("Expand");
    expect(asked()).toBe(1);

    await fireEvent.click(caret());
    await waitFor(() => expect(asked()).toBe(2));
    expect(caret().title).toBe("Expand");
  });
});
