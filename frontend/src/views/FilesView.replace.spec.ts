/**
 * A rename or a batch move/copy onto an existing name asks "Replace?" as an upload does.
 * Each test names the mutation it kills.
 */
import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { defineComponent, h, ref } from "vue";

import { createI18n } from "@/i18n";
import { en } from "@/locales/en";
import * as http from "@/api/http";
import { ApiError } from "@/api/apiError";
import { useFilesStore } from "@/stores/files";
import FilesView from "./FilesView.vue";
import { REPLACE_ARM_MS } from "@/composables/useReplacePrompt";

const { messageSpy, dialogSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  dialogSpy: { warning: vi.fn() },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "local", dir: "/p" }, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  uploadFile: vi.fn(),
  renameFile: vi.fn(),
  batchMove: vi.fn(),
  batchCopy: vi.fn(),
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [{ name: "local", host: "local", favorites: [], pinned: false, default_dir: "/" }]),
  fetchBox: vi.fn(async () => ({ name: "local", host: "local", favorites: [], pinned: false })),
  fetchDirectory: vi.fn(async () => ({
    box: "local", directory: "/p",
    entries: [
      { name: "a.txt", path: "/p/a.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
    ],
  })),
  toggleFavorite: vi.fn(async () => ({ favorites: [] })),
  togglePin: vi.fn(async () => ({ pinned: false })),
  boxStatus: vi.fn(async () => ({ status: "online" })),
  touchFile: vi.fn(), createFolder: vi.fn(), downloadFile: vi.fn(), downloadDirectory: vi.fn(),
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
vi.mock("@/components/ContentSearchInput.vue", () => passthrough("ContentSearchInput"));
vi.mock("@/components/DirectorySearchInput.vue", () => passthrough("DirectorySearchInput"));
// A context menu that lists its items as buttons, and a batch dialog with one confirm button.
vi.mock("@/components/ContextMenu.vue", () => ({
  default: {
    props: ["items", "visible"],
    emits: ["select", "close"],
    template: '<div class="stub-menu"><button v-for="i in items.filter((x) => !x.separator)" :key="i.id" :class="`menu-${i.id}`" @click="$emit(\'select\', i.id)">{{ i.label }}</button></div>',
  },
}));
vi.mock("@/components/BatchMoveModal.vue", () => ({
  default: {
    props: ["visible", "mode"],
    emits: ["confirm", "close"],
    template: '<div v-if="visible" class="stub-batch"><button class="batch-confirm" @click="$emit(\'confirm\', \'/dest\')">go</button></div>',
  },
}));

vi.mock("naive-ui", async () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  const NDataTable = defineComponent({
    inheritAttrs: false,
    props: ["columns", "data"],
    setup(props: any) {
      return () => h("div", { class: "stub-datatable" }, props.data.map((row: any) =>
        h("div", { class: "row", "data-path": row.path },
          props.columns.map((col: any) => (col.type === "selection" ? null : h("div", { class: `cell-${col.key}` }, col.render ? col.render(row) : null))))));
    },
  });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["type", "disabled", "quaternary", "tertiary", "size", "loading", "title"],
      ["click"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NCheckbox: stub('<input type="checkbox" />', ["checked"]),
    NDataTable,
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NInput: stub('<input class="stub-input" :value="value" @input="$emit(\'update:value\', $event.target.value)" />', ["value", "size", "placeholder"], ["update:value"]),
    NModal: stub(
      '<div class="stub-modal" v-if="show"><slot /><button class="modal-positive" @click="$emit(\'positive-click\')">ok</button></div>',
      ["show"], ["positive-click", "negative-click", "update:show"],
    ),
    NPopover: stub('<div class="stub-popover"><slot name="trigger" /><div class="popover-body"><slot /></div></div>', ["show", "trigger", "placement"]),
    NProgress: stub('<div class="stub-progress" />'),
    NSelect: stub('<select class="stub-select" />', ["value", "options"]),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<div class="stub-spin"><slot /></div>'),
    NTag: stub('<span class="stub-tag"><slot /></span>'),
    NTooltip: stub('<span class="stub-tooltip"><slot name="trigger" /></span>'),
    useDialog: () => dialogSpy,
    useMessage: () => ({ ...messageSpy, loading: vi.fn(() => ({ destroy: vi.fn() })) }),
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
const renameFile = vi.mocked(http.renameFile);
const batchMove = vi.mocked(http.batchMove);
const batchCopy = vi.mocked(http.batchCopy);
const createArchive = vi.mocked(http.createArchive);
const taken = () => new ApiError(409, "exists", "File already exists");
/** What a person reads for a failed item: the line of its reason, " — ", the server's text. */
const line = (reason: string, text: string) => `${en[`error.reason.${reason}`]} — ${text}`;
const exists = (path: string) => ({ path, error: "File already exists", reason: "exists" as const });
const blocked = (path: string) => ({ path, error: "Only a regular file can be replaced", reason: "cannot_replace" as const });
const done = (succeeded: string[], failed: any[] = []) => ({ status: failed.length ? "partial" as const : "ok" as const, succeeded, failed });

async function mountLoaded() {
  const utils = render(FilesView, { global: { plugins: [i18nPlugin] } });
  await waitFor(() => expect(utils.container.querySelector('[data-path="/p/a.txt"]')).toBeTruthy());
  return utils;
}

async function prompt(n: number) {
  await waitFor(() => expect(dialogSpy.warning).toHaveBeenCalledTimes(n));
  // Replace is dead for a moment after a prompt appears; wait until it answers.
  await new Promise((resolve) => setTimeout(resolve, REPLACE_ARM_MS + 50));
  return dialogSpy.warning.mock.calls[n - 1]![0];
}

/** Press a button of the multi-name prompt by its label. */
/** The buttons of a prompt (the tick box and the row), and the one called `label`: "No" is the first, "Replace" the last. */
function buttonsOf(options: any) {
  const [row, box] = options.action().children as any[];
  return { box, buttons: row.children as any[] };
}
function press(options: any, label: string, ticked = false) {
  const { box, buttons } = buttonsOf(options);
  if (ticked) box.props["onUpdate:checked"](true);
  const found = label === "No" ? buttons[0] : buttons.find((v) => v.children.default() === label);
  found.props.onClick();
}

/** Open the rename dialog for a.txt and type the new name. */
async function startRename(container: Element, newName: string) {
  await fireEvent.contextMenu(container.querySelector('[data-path="/p/a.txt"] .cell-name div')!);
  await fireEvent.click(container.querySelector(".menu-rename")!);
  const input = container.querySelector(".stub-modal .stub-input") as HTMLInputElement;
  await fireEvent.update(input, newName);
  await fireEvent.click(container.querySelector(".modal-positive")!);
}

async function startBatch(container: Element, label: "Move to..." | "Copy to...", paths: string[]) {
  useFilesStore().setSelectedFiles(paths);
  const button = await waitFor(() => {
    const found = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(label));
    expect(found).toBeTruthy();
    return found!;
  });
  await fireEvent.click(button);
  await fireEvent.click(container.querySelector(".batch-confirm")!);
}

describe("FilesView: rename onto an existing name", () => {
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
    renameFile.mockReset();
    batchMove.mockReset();
    batchCopy.mockReset();
  });

  // Mutation: no prompt on 409 (the error toast of today), or Yes without the flag.
  it("a 409 asks naming the file; Replace sends the rename again with overwrite and reloads", async () => {
    renameFile.mockRejectedValueOnce(taken()).mockResolvedValueOnce({ status: "ok", message: "renamed" });
    const { container } = await mountLoaded();
    const loads = vi.mocked(http.fetchDirectory).mock.calls.length;
    await startRename(container, "b.txt");
    const opts = await prompt(1);
    expect(opts.content).toBe("\"b.txt\" already exists here. Replace it?");
    expect(messageSpy.error).not.toHaveBeenCalled();
    press(opts, "Replace");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("renamed"));
    expect(renameFile.mock.calls.map((c) => c.slice(1))).toEqual([
      ["/p/a.txt", "b.txt", "tok"],
      ["/p/a.txt", "b.txt", "tok", { overwrite: true }],
    ]);
    expect(vi.mocked(http.fetchDirectory).mock.calls.length).toBeGreaterThan(loads);
    expect(messageSpy.error).not.toHaveBeenCalled();
  });

  // Mutation: treat a dismissed prompt as Yes, or show the 409 text as an error.
  it.each([
    ["No", (o: any) => press(o, "No")],
    ["Escape", (o: any) => o.onEsc()],
    ["a click outside", (o: any) => o.onMaskClick()],
  ])("%s changes nothing and shows no error", async (_label, dismiss) => {
    renameFile.mockRejectedValueOnce(taken());
    const { container } = await mountLoaded();
    await startRename(container, "b.txt");
    dismiss(await prompt(1));
    await waitFor(() => expect(container.querySelector(".modal-positive")).toBeTruthy());
    expect(renameFile).toHaveBeenCalledTimes(1);
    expect(messageSpy.error).not.toHaveBeenCalled();
    expect(messageSpy.success).not.toHaveBeenCalled();
    expect(messageSpy.warning).not.toHaveBeenCalled();
  });

  // Mutation: every 409 asks (a Yes would then fail: the server never replaces these).
  it.each([
    ["a folder or link in the way", "cannot_replace", "That name is taken and cannot be replaced — File already exists"],
    ["a 409 that does not say why", null, "The request failed — File already exists"],
  ] as const)("%s shows the failure's line and text and asks nothing", async (_label, reason, shown) => {
    renameFile.mockRejectedValueOnce(new ApiError(409, reason, "File already exists"));
    const { container } = await mountLoaded();
    await startRename(container, "b.txt");
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledWith(shown));
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(renameFile).toHaveBeenCalledTimes(1);
  });

  // Mutation: the unchanged name is sent (the server answers 409 for the file itself).
  it("the unchanged name sends nothing, closes the dialog and shows no message", async () => {
    const { container } = await mountLoaded();
    await startRename(container, "a.txt");
    await waitFor(() => expect(container.querySelector(".stub-modal")).toBeNull());
    expect(renameFile).not.toHaveBeenCalled();
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(messageSpy.error).not.toHaveBeenCalled();
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  // Mutation: the prompt stays up after the view is left, and a Yes sends the overwrite.
  it("leaving the view takes the prompt down and a late Yes sends nothing", async () => {
    const destroy = vi.fn();
    dialogSpy.warning.mockImplementationOnce(() => ({ destroy }));
    renameFile.mockRejectedValueOnce(taken());
    const { container, unmount } = await mountLoaded();
    await startRename(container, "b.txt");
    const opts = await prompt(1);
    unmount();
    expect(destroy).toHaveBeenCalledTimes(1);
    press(opts, "Replace");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(renameFile).toHaveBeenCalledTimes(1);
  });

  // Mutation: prompt on every failure.
  it("any other error shows the server's text and asks nothing", async () => {
    renameFile.mockRejectedValueOnce(Object.assign(new Error("Only a regular file can be replaced"), { status: 400 }));
    const { container } = await mountLoaded();
    await startRename(container, "b.txt");
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledWith("Only a regular file can be replaced"));
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(renameFile).toHaveBeenCalledTimes(1);
  });
});

describe.each([
  ["move", "Move to...", batchMove, "Moved"],
  ["copy", "Copy to...", batchCopy, "Copied"],
] as const)("FilesView: batch %s onto existing names", (_mode, label, call, verb) => {
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
    batchMove.mockReset();
    batchCopy.mockReset();
  });

  const sent = () => call.mock.calls.map((c) => [c[1], c[4]]);

  // Mutation: send the retry without the flag, or all the paths again (the first is moved twice).
  it("asks for each taken name in order; the accepted ones are sent again once, with overwrite; the message counts all three", async () => {
    call
      .mockResolvedValueOnce(done(["/p/a"], [exists("/p/b"), exists("/p/c"), exists("/p/d"), blocked("/p/e")]))
      .mockResolvedValueOnce(done(["/p/b", "/p/d"]));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/p/a", "/p/b", "/p/c", "/p/d", "/p/e"]);
    const first = await prompt(1);
    expect(first.content).toBe("\"b\" already exists here. Replace it?");
    press(first, "Replace");
    const second = await prompt(2);
    expect(second.content).toBe("\"c\" already exists here. Replace it?");
    press(second, "Skip");
    const third = await prompt(3);
    expect(third.content).toBe("\"d\" already exists here. Replace it?");
    press(third, "Replace");
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalledTimes(1));
    expect(messageSpy.warning).toHaveBeenCalledWith(`${verb} 3, skipped 1, failed 1`);
    expect(sent()).toEqual([
      [["/p/a", "/p/b", "/p/c", "/p/d", "/p/e"], undefined],
      [["/p/b", "/p/d"], { overwrite: true }],
    ]);
    expect(messageSpy.error).toHaveBeenCalledWith(
      `e: ${line("cannot_replace", "Only a regular file can be replaced")}`, { closable: true, duration: 0 },
    );
    expect(dialogSpy.warning).toHaveBeenCalledTimes(3);
    expect(useFilesStore().selectedFiles).toEqual([]);
  });

  // Mutation: the server's text is dropped again ("failed 1" alone).
  it("a failed item shows its file name and the server's text", async () => {
    call.mockResolvedValueOnce(done([], [
      { path: "/p/f", error: "Could not copy /p/f to /dest/f: disk full", reason: "failed" },
      blocked("/p/g"),
    ]));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/p/f", "/p/g"]);
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalledWith(`${verb} 0, skipped 0, failed 2`));
    expect(messageSpy.error.mock.calls).toEqual([
      [`f: ${line("failed", "Could not copy /p/f to /dest/f: disk full")}`, { closable: true, duration: 0 }],
      [`g: ${line("cannot_replace", "Only a regular file can be replaced")}`, { closable: true, duration: 0 }],
    ]);
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(call).toHaveBeenCalledTimes(1);
  });

  it("a denied item shows its file name, the line of its reason and the server's text", async () => {
    call.mockResolvedValueOnce(done([], [
      { path: "/a/x.txt", error: "Permission denied: /a/x.txt", reason: "denied" },
    ]));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/a/x.txt"]);
    await waitFor(() => expect(messageSpy.error.mock.calls).toEqual([
      [`x.txt: ${en["error.reason.denied"]} — Permission denied: /a/x.txt`, { closable: true, duration: 0 }],
    ]));
  });

  // Mutation: No sends the paths anyway; or an error toast / cleared selection for a user's own choice.
  it("No to every prompt sends nothing more, shows no error and keeps the selection", async () => {
    call.mockResolvedValueOnce(done([], [exists("/p/b"), exists("/p/c")]));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/p/b", "/p/c"]);
    press(await prompt(1), "Skip", true);
    await waitFor(() => expect(messageSpy.info).toHaveBeenCalledWith(`${verb} 0, skipped 2, failed 0`));
    expect(call).toHaveBeenCalledTimes(1);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
    expect(messageSpy.error).not.toHaveBeenCalled();
    expect(messageSpy.warning).not.toHaveBeenCalled();
    expect(useFilesStore().selectedFiles).toEqual(["/p/b", "/p/c"]);
  });

  // Mutation: asking again about a retry that fails.
  it("a retry that fails for another reason shows its text, with no second prompt", async () => {
    call
      .mockResolvedValueOnce(done([], [exists("/p/b")]))
      .mockRejectedValueOnce(new Error("Could not replace /dest/b: disk full"));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/p/b"]);
    press(await prompt(1), "Replace");
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledWith(
      `b: ${line("failed", "Could not replace /dest/b: disk full")}`, { closable: true, duration: 0 },
    ));
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
    expect(call).toHaveBeenCalledTimes(2);
  });

  // Mutation: a message for plain success changes.
  it("with nothing taken the message is the plain count and no prompt is shown", async () => {
    call.mockResolvedValueOnce(done(["/p/a", "/p/b"]));
    const { container } = await mountLoaded();
    await startBatch(container, label, ["/p/a", "/p/b"]);
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith(`${verb} 2 item(s)`));
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(sent()).toEqual([[["/p/a", "/p/b"], undefined]]);
  });
});

describe("FilesView: compress onto an existing archive", () => {
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
    createArchive.mockReset();
  });

  async function compress(container: Element) {
    await fireEvent.contextMenu(container.querySelector('[data-path="/p/a.txt"] .cell-name div')!);
    await fireEvent.click(container.querySelector(".menu-compress-zip")!);
  }

  // Mutation: no prompt on 409 (the error toast), or Yes without the flag.
  it("a 409 asks naming the archive; Replace sends the compress again with overwrite", async () => {
    createArchive.mockRejectedValueOnce(taken()).mockResolvedValueOnce({ status: "ok", message: "Created a.txt.zip" });
    const { container } = await mountLoaded();
    await compress(container);
    const opts = await prompt(1);
    expect(opts.content).toBe("\"a.txt.zip\" already exists here. Replace it?");
    expect(messageSpy.error).not.toHaveBeenCalled();
    press(opts, "Replace");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Created a.txt.zip"));
    expect(createArchive.mock.calls.map((c) => c.slice(1))).toEqual([
      [["/p/a.txt"], "/p", "a.txt.zip", "zip", "tok"],
      [["/p/a.txt"], "/p", "a.txt.zip", "zip", "tok", { overwrite: true }],
    ]);
    expect(messageSpy.error).not.toHaveBeenCalled();
  });

  // Mutation: a dismissed prompt is read as Yes, or the 409 text is shown as an error.
  it.each([
    ["No", (o: any) => press(o, "No")],
    ["Escape", (o: any) => o.onEsc()],
    ["a click outside", (o: any) => o.onMaskClick()],
  ])("%s sends nothing more and shows no message", async (_label, dismiss) => {
    createArchive.mockRejectedValueOnce(taken());
    const { container } = await mountLoaded();
    await compress(container);
    dismiss(await prompt(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(createArchive).toHaveBeenCalledTimes(1);
    expect(messageSpy.error).not.toHaveBeenCalled();
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  // Mutation: every refusal asks (a Yes would fail: a folder or link is never replaced).
  it("a name that cannot be replaced shows the failure's line and text and asks nothing", async () => {
    createArchive.mockRejectedValueOnce(new ApiError(409, "cannot_replace", "File already exists"));
    const { container } = await mountLoaded();
    await compress(container);
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledWith(
      "That name is taken and cannot be replaced — File already exists"));
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(createArchive).toHaveBeenCalledTimes(1);
  });
});

describe("FilesView: extract onto existing files", () => {
  const extractArchive = vi.mocked(http.extractArchive);
  const names = ["a.txt", "b.txt", "sub/c.txt", "sub/d.txt", "sub/e.txt"];
  const takenMany = () => new ApiError(
    409, "exists", "Files of the archive that already exist here (12): a.txt, b.txt, sub/c.txt, sub/d.txt, sub/e.txt and 7 more",
    null, { count: 12, names },
  );

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
    vi.mocked(http.fetchDirectory).mockResolvedValue({
      box: "local", directory: "/p",
      entries: [
        { name: "a.txt", path: "/p/a.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
        { name: "in.zip", path: "/p/in.zip", is_directory: false, size: 9, modified: 1, mode: 420 },
      ],
    } as any);
  });
  afterEach(() => {
    vi.clearAllMocks();
    extractArchive.mockReset();
  });

  async function extract(container: Element) {
    await fireEvent.contextMenu(container.querySelector('[data-path="/p/in.zip"] .cell-name div')!);
    await fireEvent.click(container.querySelector(".menu-extract")!);
  }

  // Mutation: no prompt on 409 (the error toast), the server's count and names left out, or Yes without the flag.
  it("a 409 asks once with the count and the first names; Replace sends the extract again with overwrite", async () => {
    extractArchive.mockRejectedValueOnce(takenMany()).mockResolvedValueOnce({ status: "ok", message: "Extracted" });
    const { container } = await mountLoaded();
    await extract(container);
    const opts = await prompt(1);
    expect([opts.title, buttonsOf(opts).buttons.map((v: any) => v.children.default())]).toEqual(
      ["File already exists", ["No, keep it", "Keep both", "Replace"]],
    );
    expect(opts.content).toBe(
      "Files here that would be replaced: 12 (a.txt, b.txt, sub/c.txt, sub/d.txt, sub/e.txt, …). Replace them?",
    );
    expect(messageSpy.error).not.toHaveBeenCalled();
    press(opts, "Replace");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Extracted archive"));
    expect(extractArchive.mock.calls.map((c) => c.slice(1))).toEqual([
      ["/p/in.zip", "/p", "tok"],
      ["/p/in.zip", "/p", "tok", { overwrite: true }],
    ]);
    expect(dialogSpy.warning).toHaveBeenCalledTimes(1);
    expect(messageSpy.error).not.toHaveBeenCalled();
  });

  // Mutation: "…" shown when every taken name is listed.
  it("lists every name when the server sent them all", async () => {
    extractArchive.mockRejectedValueOnce(
      new ApiError(409, "exists", "Files of the archive that already exist here (1): a.txt", null, { count: 1, names: ["a.txt"] }),
    );
    const { container } = await mountLoaded();
    await extract(container);
    expect((await prompt(1)).content).toBe("Files here that would be replaced: 1 (a.txt). Replace them?");
  });

  // Mutation: a dismissed prompt is read as Yes, or the 409 text is shown as an error.
  it.each([
    ["No", (o: any) => press(o, "No")],
    ["Escape", (o: any) => o.onEsc()],
    ["a click outside", (o: any) => o.onMaskClick()],
  ])("%s sends nothing more and shows no message", async (_label, dismiss) => {
    extractArchive.mockRejectedValueOnce(takenMany());
    const { container } = await mountLoaded();
    await extract(container);
    dismiss(await prompt(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(extractArchive).toHaveBeenCalledTimes(1);
    expect(messageSpy.error).not.toHaveBeenCalled();
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  // Mutation: every refusal asks (a Yes would fail: a folder or link is never replaced).
  it("names that cannot be replaced show the failure's line and text and ask nothing", async () => {
    extractArchive.mockRejectedValueOnce(new ApiError(
      409, "cannot_replace", "Names of the archive taken here by something that cannot be replaced (1): x",
      null, { count: 1, names: ["x"] },
    ));
    const { container } = await mountLoaded();
    await extract(container);
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledWith(
      "That name is taken and cannot be replaced — Names of the archive taken here by something that cannot be replaced (1): x"));
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(extractArchive).toHaveBeenCalledTimes(1);
  });
});

describe("FilesView: Keep both", () => {
  const renameFileMock = vi.mocked(http.renameFile);
  const extractArchive = vi.mocked(http.extractArchive);
  const entry = (name: string) => ({ name, path: `/p/${name}`, is_directory: false, size: 3, modified: 1, mode: 420 });

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
    vi.mocked(http.fetchDirectory).mockResolvedValue({
      box: "local", directory: "/p",
      entries: [entry("a.txt"), entry("in.zip"), entry("b (2).txt"), entry("c (2)"), entry("d (2)")],
    } as any);
  });
  afterEach(() => {
    vi.clearAllMocks();
    renameFileMock.mockReset();
    batchMove.mockReset();
    createArchive.mockReset();
    extractArchive.mockReset();
  });

  it("rename: sends keepBoth and never overwrite, names the landed name and selects that entry", async () => {
    renameFileMock.mockRejectedValueOnce(taken()).mockResolvedValueOnce({ status: "ok", message: "Renamed", path: "/p/b (2).txt" });
    const { container } = await mountLoaded();
    await startRename(container, "b.txt");
    press(await prompt(1), "Keep both");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Kept both: saved as b (2).txt"));
    expect(renameFileMock.mock.calls.map((c) => c.slice(1))).toEqual([
      ["/p/a.txt", "b.txt", "tok"],
      ["/p/a.txt", "b.txt", "tok", { keepBoth: true }],
    ]);
    expect(useFilesStore().selectedFiles).toEqual(["/p/b (2).txt"]);
    expect(messageSpy.success).toHaveBeenCalledTimes(1);
  });

  it("a name with markup in it is shown as text", async () => {
    renameFileMock.mockRejectedValueOnce(taken()).mockResolvedValueOnce({ status: "ok", message: "Renamed", path: "/p/<b>x (2)" });
    const { container } = await mountLoaded();
    await startRename(container, "<b>x");
    press(await prompt(1), "Keep both");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Kept both: saved as <b>x (2)"));
  });

  it("batch move: Replace and Keep both go out as two calls; the message names the landed name of each, all selected", async () => {
    batchMove
      .mockResolvedValueOnce(done([], [exists("/p/b"), exists("/p/c"), exists("/p/d")]))
      .mockResolvedValueOnce(done(["/p/b"]))
      .mockResolvedValueOnce({ ...done(["/p/c", "/p/d"]), placed: [{ path: "/p/c", placed: "/p/c (2)" }, { path: "/p/d", placed: "/p/d (2)" }] });
    const { container } = await mountLoaded();
    await startBatch(container, "Move to...", ["/p/b", "/p/c", "/p/d"]);
    press(await prompt(1), "Replace");
    press(await prompt(2), "Keep both", true);
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Kept both: saved as c (2), d (2)"));
    expect(batchMove.mock.calls.map((c) => [c[1], c[4]])).toEqual([
      [["/p/b", "/p/c", "/p/d"], undefined],
      [["/p/b"], { overwrite: true }],
      [["/p/c", "/p/d"], { keepBoth: true }],
    ]);
    expect(useFilesStore().selectedFiles).toEqual(["/p/c (2)", "/p/d (2)"]);
  });

  it("compress: Keep both sends keepBoth and says the landed archive name", async () => {
    createArchive.mockRejectedValueOnce(taken()).mockResolvedValueOnce({ status: "ok", message: "Created b (2).txt", path: "/p/b (2).txt" });
    const { container } = await mountLoaded();
    await fireEvent.contextMenu(container.querySelector('[data-path="/p/a.txt"] .cell-name div')!);
    await fireEvent.click(container.querySelector(".menu-compress-zip")!);
    press(await prompt(1), "Keep both");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Kept both: saved as b (2).txt"));
    expect(createArchive.mock.calls.map((c) => c.slice(5))).toEqual([["tok"], ["tok", { keepBoth: true }]]);
  });

  it("extract: Keep both sends the extract again with keepBoth and says the new folder", async () => {
    extractArchive
      .mockRejectedValueOnce(new ApiError(409, "exists", "taken", null, { count: 1, names: ["a.txt"] }))
      .mockResolvedValueOnce({ status: "ok", message: "Extracted", path: "/p/c (2)/" });
    const { container } = await mountLoaded();
    await fireEvent.contextMenu(container.querySelector('[data-path="/p/in.zip"] .cell-name div')!);
    await fireEvent.click(container.querySelector(".menu-extract")!);
    press(await prompt(1), "Keep both");
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith("Kept both: saved as c (2)"));
    expect(extractArchive.mock.calls.map((c) => c.slice(3))).toEqual([["tok"], ["tok", { keepBoth: true }]]);
    expect(useFilesStore().selectedFiles).toEqual(["/p/c (2)"]);
  });
});
