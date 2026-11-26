import { render, fireEvent, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import type { FilePreview } from "@/api/types";
import { createI18n } from "@/i18n";
import * as http from "@/api/http";
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

describe("FilePreviewModal: links and images in a markdown file on a Windows drive", () => {
  const markdown = (path: string, parent: string | undefined, content: string): FilePreview => ({
    box: "local", path, parent: parent as string, content, syntax_class: null,
    image_data: null, image_mime: null, image_too_large: false, image_limit_kb: 200, is_markdown: true,
  });
  const png = { image_data: "AAAA", image_mime: "image/png", image_too_large: false };

  beforeEach(() => {
    setActivePinia(createPinia());
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  function serve(file: FilePreview) {
    vi.mocked(http.fetchFilePreview).mockImplementation(async (_box: string, path: string) =>
      path === file.path ? file : ({ ...file, content: "", is_markdown: false, ...png } as FilePreview));
  }

  // The file is fetched when `show` turns true, so open it after mounting closed.
  async function openModal(path: string) {
    const utils = mountModal({ show: false, path, siblings: [] });
    await utils.rerender({ show: true });
    return utils;
  }

  const fetched = () => vi.mocked(http.fetchFilePreview).mock.calls.map((c) => c[1]);

  // Mutation: put back the old resolveImagePath body (`"/" + parts.join("/")`): the image is
  // asked for as `/C:/Users/me/pic1.png`.
  it("an image next to the file is asked for under the drive, with no leading slash", async () => {
    serve(markdown("C:/Users/me/a.md", "C:/Users/me", "![p](pic1.png)"));
    await openModal("C:/Users/me/a.md");
    await waitFor(() => expect(fetched()).toContain("C:/Users/me/pic1.png"));
    expect(fetched()).not.toContain("/C:/Users/me/pic1.png");
  });

  // Mutation: put back `props.path.split('/').slice(0, -1).join('/') || '/'` in resolveImagePath:
  // with no parent in the answer, `C:/a.md` has the parent `C:` and the image `/C:/pic2.png`.
  it("with no parent in the answer the image is cut from the file's own path", async () => {
    serve(markdown("C:/a.md", "", "![p](pic2.png)"));
    await openModal("C:/a.md");
    await waitFor(() => expect(fetched()).toContain("C:/pic2.png"));
    expect(fetched()).not.toContain("/C:/pic2.png");
  });

  // Mutation: put back the old handleMarkdownClick resolution and parent cut: `../../z.md`
  // from C:/Users/me gives `/C:/z.md` and the folder `/C:`.
  it.each([
    ["docs/x.md", "C:/Users/me/docs", "x.md"],
    ["../../z.md", "C:/", "z.md"],
    ["../w.md", "C:/Users", "w.md"],
  ])("a link to %s opens folder %s with %s previewed", async (href, folder, name) => {
    serve(markdown("C:/Users/me/a.md", "C:/Users/me", `[l](${href})`));
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const { container } = await openModal("C:/Users/me/a.md");
    const link = await waitFor(() => {
      const a = container.querySelector(".markdown-rendered a");
      expect(a).not.toBeNull();
      return a as HTMLAnchorElement;
    });
    await fireEvent.click(link);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]?.[0]).toBe(
      `/app/files?${new URLSearchParams({ box: "local", path: folder, preview: name }).toString()}`,
    );
  });

  it("with no parent in the answer a link is resolved against the file's own folder", async () => {
    serve(markdown("C:/a.md", "", "[l](b.md)"));
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const { container } = await openModal("C:/a.md");
    const link = await waitFor(() => {
      const a = container.querySelector(".markdown-rendered a");
      expect(a).not.toBeNull();
      return a as HTMLAnchorElement;
    });
    await fireEvent.click(link);
    expect(open.mock.calls[0]?.[0]).toBe(
      `/app/files?${new URLSearchParams({ box: "local", path: "C:/", preview: "b.md" }).toString()}`,
    );
  });

  // Mutation: put back `props.path.split('/').slice(0, -1).join('/') || '/'` in either
  // parent cut: a path with backslashes has no `/` to cut at, so the parent becomes `/`.
  it("with no parent in the answer a backslash path is cut at its backslashes, for images and links", async () => {
    serve(markdown("C:\\Users\\me\\a.md", "", "![p](pic3.png) [l](b.md)"));
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const { container } = await openModal("C:\\Users\\me\\a.md");
    await waitFor(() => expect(fetched()).toContain("C:/Users/me/pic3.png"));
    await fireEvent.click(container.querySelector(".markdown-rendered a")!);
    expect(open.mock.calls[0]?.[0]).toBe(
      `/app/files?${new URLSearchParams({ box: "local", path: "C:/Users/me", preview: "b.md" }).toString()}`,
    );
  });
});
