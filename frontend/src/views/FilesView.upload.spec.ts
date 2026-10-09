/**
 * Upload refused vs replaced (wave 5d, WS-AG).
 *
 * Review: "`doUpload` catches every error, puts it in `uploadError` and returns normally,
 * so `uploadOne` never sees a failure ... The UI shows the 'uploaded' success toast and
 * the red 'File already exists' alert together." User: "it shoudl offer to replace it,
 * default choice is no." Each test names the mutation it kills.
 */
import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";
import { ref } from "vue";

import { createI18n } from "@/i18n";
import * as http from "@/api/http";
import FilesView from "./FilesView.vue";

const { messageSpy, dialogSpy, UploadErrorMock } = vi.hoisted(() => {
  class UploadErrorMock extends Error {
    readonly status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "UploadError";
      this.status = status;
    }
  }
  return {
    messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
    dialogSpy: { warning: vi.fn() },
    UploadErrorMock,
  };
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "local", dir: "/home/u/proj" }, path: "/files", params: {}, name: "files" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/api/http", () => ({
  UploadError: UploadErrorMock,
  uploadFile: vi.fn(),
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
      { name: "a.txt", path: "/home/u/proj/a.txt", is_directory: false, size: 3, modified: 1, mode: 420 },
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
vi.mock("@/components/BatchMoveModal.vue", () => passthrough("BatchMoveModal"));
vi.mock("@/components/ContentSearchInput.vue", () => passthrough("ContentSearchInput"));
vi.mock("@/components/ContextMenu.vue", () => passthrough("ContextMenu"));
vi.mock("@/components/DirectorySearchInput.vue", () => passthrough("DirectorySearchInput"));

vi.mock("naive-ui", async () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["type", "disabled", "quaternary", "tertiary", "size", "loading", "title"],
      ["click"],
    ),
    NCard: stub('<div class="stub-card"><slot /></div>', ["size"]),
    NDataTable: stub('<div class="stub-datatable" />', ["columns", "data"]),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NInput: stub('<input class="stub-input" />', ["value", "size", "placeholder"]),
    NModal: stub('<div class="stub-modal" v-if="show"><slot /></div>', ["show"]),
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
    "PhArchive",
  ];
  return Object.fromEntries(names.map((n) => [n, stub(n)]));
});

const i18nPlugin = { install(app: App) { createI18n(app); } };
const uploadFile = vi.mocked(http.uploadFile);
const exists = () => new UploadErrorMock("File already exists", 409);
const OK = { status: "ok", message: "uploaded" };

async function mountLoaded() {
  const utils = render(FilesView, { global: { plugins: [i18nPlugin] } });
  await waitFor(() => expect(vi.mocked(http.fetchDirectory)).toHaveBeenCalled());
  return utils;
}

/** Choose files in the toolbar's file input. */
async function choose(container: Element, names: string[]) {
  const files = names.map((n) => new File([n], n));
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, "files", {
    configurable: true,
    value: { length: files.length, item: (i: number) => files[i] ?? null },
  });
  await fireEvent.change(input);
  return files;
}

/** The options of the n-th replace prompt, once it has been shown. */
async function prompt(n: number) {
  await waitFor(() => expect(dialogSpy.warning).toHaveBeenCalledTimes(n));
  return dialogSpy.warning.mock.calls[n - 1]![0];
}

const uploadedNames = () => uploadFile.mock.calls.map((c) => [(c[2] as File).name, c[5]]);

describe("FilesView upload: refused is never shown as uploaded", () => {
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
    vi.useRealTimers();
    vi.clearAllMocks();
    uploadFile.mockReset();
  });

  // Mutation: the pre-5d store (doUpload swallows the error) or answering the prompt
  // with Yes by default; the skipped file is toasted as "uploaded" or re-sent.
  it("No on the prompt: the file is not re-sent, is reported skipped, and nothing is toasted as uploaded", async () => {
    uploadFile.mockRejectedValueOnce(exists());
    const { container } = await mountLoaded();
    await choose(container, ["a.txt"]);
    const opts = await prompt(1);
    expect([opts.title, opts.content, opts.positiveText, opts.negativeText]).toEqual([
      "File already exists", "\"a.txt\" already exists here. Replace it?", "Replace", "No, keep it",
    ]);
    opts.onNegativeClick();
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalledTimes(1));
    expect(messageSpy.warning).toHaveBeenCalledWith(
      "a.txt: skipped, the existing file was kept", { closable: true, duration: 0 },
    );
    expect(uploadedNames()).toEqual([["a.txt", {}]]);
    expect(messageSpy.success).not.toHaveBeenCalled();
    expect(messageSpy.error).not.toHaveBeenCalled();
  });

  // Mutation: resolve the prompt only from onPositiveClick/onNegativeClick; Escape or
  // a mask click then leaves the upload hanging (no skip report, busy forever).
  it.each([
    ["Escape", (o: any) => { o.onEsc(); o.onAfterLeave(); }],
    ["a mask click", (o: any) => { o.onMaskClick(); o.onAfterLeave(); }],
  ])("%s on the prompt counts as No", async (_label, dismiss) => {
    uploadFile.mockRejectedValueOnce(exists());
    const { container } = await mountLoaded();
    await choose(container, ["a.txt"]);
    dismiss(await prompt(1));
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalledTimes(1));
    expect(messageSpy.warning).toHaveBeenCalledWith(
      "a.txt: skipped, the existing file was kept", { closable: true, duration: 0 },
    );
    expect(uploadedNames()).toEqual([["a.txt", {}]]);
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  // Mutation: re-send without `{ overwrite: true }` (or not at all) after Yes.
  it("Replace on the prompt re-sends that file with the overwrite flag and toasts it uploaded", async () => {
    uploadFile.mockRejectedValueOnce(exists()).mockResolvedValueOnce(OK);
    const { container } = await mountLoaded();
    await choose(container, ["a.txt"]);
    (await prompt(1)).onPositiveClick();
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledTimes(1));
    expect(messageSpy.success).toHaveBeenCalledWith("uploaded");
    expect(uploadedNames()).toEqual([["a.txt", {}], ["a.txt", { overwrite: true }]]);
    expect(messageSpy.warning).not.toHaveBeenCalled();
  });

  // Mutation: count `files.length - failed` as uploaded (the pre-5d count) or report
  // only the last refused file; the multi-file toast would say 3 and one skip is lost.
  it("multi-file: each refused file is asked about and reported; only real uploads are counted", async () => {
    uploadFile
      .mockRejectedValueOnce(exists())
      .mockResolvedValueOnce(OK)
      .mockResolvedValueOnce(OK)
      .mockRejectedValueOnce(exists());
    const { container } = await mountLoaded();
    await choose(container, ["a.txt", "b.txt", "c.txt", "d.txt"]);
    (await prompt(1)).onNegativeClick();
    (await prompt(2)).onNegativeClick();
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledTimes(1));
    expect(messageSpy.success).toHaveBeenCalledWith("2 files uploaded");
    expect(messageSpy.warning.mock.calls.map((c) => c[0])).toEqual([
      "a.txt: skipped, the existing file was kept",
      "d.txt: skipped, the existing file was kept",
    ]);
    expect(dialogSpy.warning.mock.calls.map((c) => c[0].content)).toEqual([
      "\"a.txt\" already exists here. Replace it?",
      "\"d.txt\" already exists here. Replace it?",
    ]);
    expect(uploadedNames()).toEqual([["a.txt", {}], ["b.txt", {}], ["c.txt", {}], ["d.txt", {}]]);
  });

  // Mutation: retry every failure (the pre-5d loop); a 400 is sent three times with
  // "retrying" warnings.
  it("a 4xx other than 409 is reported once, not retried, and no prompt is shown", async () => {
    uploadFile.mockRejectedValueOnce(new UploadErrorMock("Upload exceeds 1024 KB limit", 400));
    const { container } = await mountLoaded();
    await choose(container, ["big.bin"]);
    await waitFor(() => expect(messageSpy.error).toHaveBeenCalledTimes(1));
    expect(messageSpy.error).toHaveBeenCalledWith(
      "big.bin: Upload exceeds 1024 KB limit", { closable: true, duration: 0 },
    );
    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(messageSpy.warning).not.toHaveBeenCalled();
    expect(dialogSpy.warning).not.toHaveBeenCalled();
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  // Negative control for the 4xx rule. Mutation: treat every failure as final (no
  // retry); a 502 would be reported as failed instead of retried after 1 s.
  it("a 5xx is still retried after the 1 s backoff", async () => {
    uploadFile.mockRejectedValueOnce(new UploadErrorMock("upload failed with 502", 502)).mockResolvedValueOnce(OK);
    const { container } = await mountLoaded();
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    await choose(container, ["a.txt"]);
    await vi.advanceTimersByTimeAsync(999);
    expect(uploadFile).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(messageSpy.success).toHaveBeenCalledWith("uploaded");
    expect(messageSpy.error).not.toHaveBeenCalled();
  });
});

describe("FilesView upload: the replace prompt's default answer is No", () => {
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
    uploadFile.mockReset();
  });

  // Naive UI's dialog focus trap focuses the first focusable element: the close button
  // when `closable`, otherwise the action row, which renders negativeText before
  // positiveText. jsdom cannot render the real dialog (cssstyle throws on its
  // `var(--n-border)` shorthand), so this pins the options that make "No" the first
  // stop. Mutation: drop `closable: false`, or give the dialog its own initial focus
  // (`autoFocus`, `positiveButtonProps`); the option set differs.
  it("the prompt has no close button and no focus override, so 'No, keep it' takes the first focus", async () => {
    uploadFile.mockRejectedValueOnce(exists());
    const { container } = await mountLoaded();
    await choose(container, ["a.txt"]);
    const opts = await prompt(1);
    expect(opts.closable).toBe(false);
    expect(Object.keys(opts).sort()).toEqual([
      "closable", "content", "negativeText", "onAfterLeave", "onEsc", "onMaskClick",
      "onNegativeClick", "onPositiveClick", "positiveText", "title",
    ]);
    // A dialog that closes any other way (onAfterLeave alone) resolves as No.
    opts.onAfterLeave();
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalledTimes(1));
    expect(messageSpy.warning).toHaveBeenCalledWith(
      "a.txt: skipped, the existing file was kept", { closable: true, duration: 0 },
    );
    expect(uploadedNames()).toEqual([["a.txt", {}]]);
  });
});
