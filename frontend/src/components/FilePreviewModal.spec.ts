import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import type { FilePreview } from "@/api/types";
import { createI18n } from "@/i18n";
import FilePreviewModal from "./FilePreviewModal.vue";

vi.mock("@/api/http", () => ({
  fetchFilePreview: vi.fn(async (_box: string, path: string): Promise<FilePreview> => ({
    box: "local",
    path,
    parent: "/dir",
    content: `content of ${path}`,
    syntax_class: null,
    image_data: null,
    image_mime: null,
    image_too_large: false,
    image_limit_kb: 200,
    is_markdown: false,
  })),
  downloadFile: vi.fn(async () => new Blob(["x"])),
  exportPdf: vi.fn(async () => new Blob(["pdf"])),
}));

vi.mock("@/components/CodeEditor.vue", () => ({
  default: { name: "CodeEditor", template: '<div class="stub-code-editor" />' },
}));

vi.mock("@/components/ExcelPreview.vue", () => ({
  default: { name: "ExcelPreview", template: '<div class="stub-excel-preview" />' },
}));

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NModal: stub('<div class="stub-modal" v-if="show"><slot name="header" /><slot /><slot name="footer" /></div>', ["show"]),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
      ["disabled", "loading", "size", "title", "text"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<div class="stub-spin"><slot /></div>'),
    NSwitch: stub('<button class="stub-switch" @click="$emit(\'update:value\', !value)"><slot name="checked" /><slot name="unchecked" /></button>', ["value"]),
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
    useLoadingBar: () => ({ start: vi.fn(), finish: vi.fn(), error: vi.fn() }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhArrowCounterClockwise: stub("PhArrowCounterClockwise"),
    PhArrowsOut: stub("PhArrowsOut"),
    PhCaretLeft: stub("PhCaretLeft"),
    PhCaretRight: stub("PhCaretRight"),
    PhCopy: stub("PhCopy"),
    PhDownloadSimple: stub("PhDownloadSimple"),
    PhEye: stub("PhEye"),
    PhFile: stub("PhFile"),
    PhFilePdf: stub("PhFilePdf"),
    PhMagnifyingGlassMinus: stub("PhMagnifyingGlassMinus"),
    PhMagnifyingGlassPlus: stub("PhMagnifyingGlassPlus"),
    PhPencil: stub("PhPencil"),
    PhPrinter: stub("PhPrinter"),
    PhX: stub("PhX"),
  };
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

function mountModal(props: Partial<InstanceType<typeof FilePreviewModal>["$props"]> = {}) {
  return render(FilePreviewModal, {
    props: {
      show: true,
      path: "/dir/b.txt",
      box: "local",
      token: null,
      theme: "light",
      siblings: ["/dir/a.txt", "/dir/b.txt", "/dir/c.txt"],
      ...props,
    },
    global: { plugins: [i18nPlugin] },
  });
}

describe("FilePreviewModal — sibling navigation", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the position indicator for the current file among its siblings", async () => {
    const { findByText } = mountModal();
    expect(await findByText("2 / 3")).toBeTruthy();
  });

  it("emits the next sibling path when ArrowRight is pressed", async () => {
    const { emitted, findByText } = mountModal();
    await findByText("2 / 3"); // wait for mount + initial fetch to settle
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    const navEvents = emitted()["navigate"];
    expect(navEvents).toBeTruthy();
    expect(navEvents![0]).toEqual(["/dir/c.txt"]);
  });

  it("emits the previous sibling path when ArrowLeft is pressed", async () => {
    const { emitted, findByText } = mountModal();
    await findByText("2 / 3");
    await fireEvent.keyDown(window, { key: "ArrowLeft" });
    const navEvents = emitted()["navigate"];
    expect(navEvents).toBeTruthy();
    expect(navEvents![0]).toEqual(["/dir/a.txt"]);
  });

  it("does not emit navigate on ArrowRight when already at the last sibling", async () => {
    const { emitted, findByText } = mountModal({ path: "/dir/c.txt" });
    await findByText("3 / 3");
    await fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(emitted()["navigate"]).toBeUndefined();
  });

  it("does not react to ArrowRight when the event target is a text input", async () => {
    const { emitted, findByText } = mountModal();
    await findByText("2 / 3");
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    await fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(emitted()["navigate"]).toBeUndefined();
    document.body.removeChild(input);
  });
});
