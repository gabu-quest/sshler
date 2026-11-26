import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import { fetchDirectory, gitInfo } from "@/api/http";
import FilesView from "./FilesView.vue";

const { routeState, lang } = vi.hoisted(() => ({
  routeState: { query: {} as Record<string, string> },
  lang: { value: "en" as "en" | "ja" },
}));

vi.mock("@/i18n", async () => await vi.importActual("@/i18n"));
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: routeState.query, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "local", favorites: [], pinned: false, default_dir: "/" },
    { name: "my box", host: "h", favorites: [], pinned: false, default_dir: "/" },
  ]),
  fetchBox: vi.fn(async () => ({ name: "local", host: "local", favorites: [], pinned: false })),
  fetchDirectory: vi.fn(async (_box: string, dir: string) => ({
    box: "local", directory: dir,
    entries: [
      { name: "a b%20", path: `${dir.replace(/\/$/, "")}/a b%20`, is_directory: true, size: null, modified: null, mode: 493 },
      { name: "a.txt", path: `${dir.replace(/\/$/, "")}/a.txt`, is_directory: false, size: 3, modified: 1, mode: 420 },
    ],
  })),
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

const { passthrough } = vi.hoisted(() => ({
  passthrough: (name: string) => ({ default: { name, template: `<div class="stub-${name}"><slot /></div>` } }),
}));
vi.mock("@/components/FavoritesPanel.vue", () => passthrough("FavoritesPanel"));
vi.mock("@/components/FileBreadcrumb.vue", () => ({
  default: {
    name: "FileBreadcrumb",
    props: ["currentDir", "selectedBox", "status", "statusText", "isFavorite", "gitInfo", "boxOptions"],
    emits: ["navigate", "update:box"],
    template: `<div class="stub-FileBreadcrumb" :data-box="selectedBox ?? ''" :data-dir="currentDir" :data-git="gitInfo ? 'yes' : 'no'">
      <span class="options">{{ (boxOptions || []).map((o) => o.value).join('|') }}</span>
      <button class="go" @click="$emit('navigate', '/tmp/a b%20')" />
    </div>`,
  },
}));
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
  const NDataTable = defineComponent({
    inheritAttrs: false,
    props: ["columns", "data"],
    setup(props: any, { attrs }: any) {
      return () => h("div", { class: "stub-datatable" }, [
        h("div", { class: "head" }, props.columns.map((c: any) => h("span", { class: "col-title" }, c.title))),
        ...props.data.map((row: any) =>
          h("div", { class: "row", "data-path": row.path, onClick: (e: MouseEvent) => attrs["row-props"]?.(row).onClick(e) },
            props.columns.map((col: any) => (col.type === "selection" ? null : h("div", { class: `cell-${col.key}` }, col.render ? col.render(row) : null))))),
      ]);
    },
  });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" :title="title" @click="$emit(\'click\')"><slot /></button>',
      ["type", "disabled", "quaternary", "tertiary", "size", "loading", "title", "ghost"], ["click"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NDataTable,
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NInput: {
      props: ["value", "size", "placeholder"],
      template: '<input class="stub-input" :placeholder="placeholder" />',
      methods: { focus() { (this as any).$el.focus(); } },
    },
    NModal: stub('<div class="stub-modal" v-if="show"><slot /></div>', ["show"]),
    NPopover: stub('<div class="stub-popover"><span class="pop-trigger" @click="$emit(\'update:show\', true)"><slot name="trigger" /></span><div class="popover-body"><slot /></div></div>', ["show", "trigger", "placement"], ["update:show"]),
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
vi.mock("@phosphor-icons/vue", () => new Proxy({}, {
  get: (_t, name) => (typeof name === "string" && name.startsWith("Ph")
    ? { name, template: `<span data-icon="${name}" />` }
    : undefined),
  has: () => true,
}));

const i18nPlugin = { install(app: App) { createI18n(app).setLocale(lang.value); } };
const mount = () => render(FilesView, { global: { plugins: [i18nPlugin] } });
const text = (c: Element) => (c.textContent ?? "").replace(/\s+/g, " ");

describe("FilesView: workspace first", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    routeState.query = {};
    lang.value = "en";
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => vi.clearAllMocks());

  it("has no page header, no Connection card and no Actions card, only the workspace", async () => {
    const { container } = mount();
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="files-workspace"]')).not.toBeNull();
    expect(container.querySelector("h1")).toBeNull();
    expect(text(container)).not.toContain("File Browser & Editor");
    expect(text(container)).not.toContain("Connection");
    expect(container.querySelectorAll(".stub-card")).toHaveLength(0);
  });

  it("an online box shows no alert; the slim alert is only for a box that is not online", async () => {
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-FileBreadcrumb")!.getAttribute("data-box")).toBe("local"));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector(".status-alert")).toBeNull();
  });

  it("the toolbar is one row: filter, kinds, New (file, folder), Upload, search toggle, zip, expand, collapse", async () => {
    const { container } = mount();
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    const bar = container.querySelector('[data-testid="files-toolbar"]')!;
    expect(bar).not.toBeNull();
    const t = text(bar);
    for (const word of ["All", "Files", "Folders", "New", "Upload", "Search contents"]) expect(t).toContain(word);
    expect(bar.querySelector('button[title="Expand all"]')).not.toBeNull();
    expect(bar.querySelector('button[title="Collapse all"]')).not.toBeNull();
    expect(text(container.querySelector(".popover-body")!)).toContain("Create File");
    expect(text(container.querySelector(".popover-body")!)).toContain("Create Folder");
  });

  it("opening New puts the cursor in its name field", async () => {
    const { container } = mount();
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    const field = container.querySelector(".popover-body input") as HTMLInputElement;
    expect(document.activeElement).not.toBe(field);
    await fireEvent.click(container.querySelector(".pop-trigger button")!);
    await waitFor(() => expect(document.activeElement).toBe(field));
  });

  it("the type column comes from the language", async () => {
    lang.value = "ja";
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".cell-is_directory")).not.toBeNull());
    const types = Array.from(container.querySelectorAll(".cell-is_directory")).map((c) => c.textContent);
    expect(types).toContain("ディレクトリ");
    expect(types).toContain("テキスト");
    expect(types).not.toContain("directory");
  });

  it("the table sits in the container that carries the file-browser class", async () => {
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-datatable")).not.toBeNull());
    expect(container.querySelector(".table-region")!.contains(container.querySelector(".stub-datatable"))).toBe(true);
    expect(container.querySelector(".table-region")!.classList.contains("file-browser")).toBe(true);
  });

  it("the upload button feeds the same hidden file input", async () => {
    const { container } = mount();
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.hidden).toBe(true);
    const click = vi.spyOn(input, "click");
    const upload = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Upload"))!;
    await fireEvent.click(upload);
    expect(click).toHaveBeenCalledTimes(1);
  });

  it("?box=nope shows the helper's text, selects no box and loads nothing", async () => {
    routeState.query = { box: "nope" };
    const { container } = mount();
    await waitFor(() => expect(text(container)).toContain("This box is not known to the server."));
    expect(container.querySelector(".stub-FileBreadcrumb")!.getAttribute("data-box")).toBe("");
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchDirectory).not.toHaveBeenCalled();
    expect(gitInfo).not.toHaveBeenCalled();
  });

  it("a box named `my box` is known", async () => {
    routeState.query = { box: "my box", dir: "/srv/a b%20" };
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-FileBreadcrumb")!.getAttribute("data-box")).toBe("my box"));
    expect(text(container)).not.toContain("This box is not known");
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    expect(vi.mocked(fetchDirectory).mock.calls[0]!.slice(0, 2)).toEqual(["my box", "/srv/a b%20"]);
  });

  it("the git badge comes from the load, and a failed load is silent", async () => {
    vi.mocked(gitInfo).mockResolvedValue({ is_repo: true, branch: "main", commit: "abc", dirty: false } as never);
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-FileBreadcrumb")!.getAttribute("data-git")).toBe("yes"));
    vi.mocked(gitInfo).mockRejectedValue(new Error("boom"));
    await fireEvent.click(container.querySelector(".go")!);
    await waitFor(() => expect(container.querySelector(".stub-FileBreadcrumb")!.getAttribute("data-git")).toBe("no"));
    expect(container.querySelector(".stub-alert")).toBeNull();
    vi.mocked(gitInfo).mockReset();
    vi.mocked(gitInfo).mockResolvedValue(null as never);
  });

  it("a path with a space and a percent sign goes to the server as it is", async () => {
    const { container } = mount();
    await waitFor(() => expect(fetchDirectory).toHaveBeenCalled());
    await fireEvent.click(container.querySelector(".go")!);
    await waitFor(() => expect(vi.mocked(fetchDirectory).mock.calls.map((c) => c[1])).toContain("/tmp/a b%20"));
  });

  it("at a drive root the `..` row is absent", async () => {
    routeState.query = { box: "local", dir: "C:/" };
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-datatable .row")).not.toBeNull());
    expect(container.querySelector('.row[data-path="C:/"]')).toBeNull();
    expect(text(container.querySelector(".stub-datatable")!)).not.toMatch(/\.\./);
  });

  it("Japanese: the column titles and the toolbar tips come from the keys", async () => {
    lang.value = "ja";
    const { container } = mount();
    await waitFor(() => expect(container.querySelector(".stub-datatable .row")).not.toBeNull());
    const titles = Array.from(container.querySelectorAll(".col-title")).map((e) => e.textContent);
    expect(titles).toContain("権限");
    expect(titles).not.toContain("perms");
    expect(container.querySelector('button[title="すべて展開"]')).not.toBeNull();
    expect(container.querySelector('button[title="すべて折りたたむ"]')).not.toBeNull();
    expect(container.querySelector('button[title="展開"]')).not.toBeNull();
    expect(container.querySelector('.cell-mode span[title="493 — クリックで権限を変更"], .cell-mode span[title="755 — クリックで権限を変更"]')).not.toBeNull();
  });
});
