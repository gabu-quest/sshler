import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import { createI18n } from "@/i18n";
import FileEditorModal from "./FileEditorModal.vue";

const writeFileMock = vi.fn(async (..._args: unknown[]) => ({ ok: true }));

vi.mock("@/api/http", () => ({
  fetchFilePreview: vi.fn(async (_box: string, path: string) => ({
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
  writeFile: (...args: unknown[]) => writeFileMock(...args),
}));

vi.mock("@/components/CodeEditor.vue", () => ({
  // Minimal stand-in: a plain textarea wired to v-model so tests can type
  // into it and trigger @save, without pulling in real CodeMirror.
  default: {
    name: "CodeEditor",
    props: ["modelValue", "language", "theme", "lineNumbers", "wordWrap", "placeholder"],
    emits: ["update:modelValue", "save"],
    template:
      '<textarea class="stub-code-editor" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}));

const dialogWarningMock = vi.fn();

vi.mock("naive-ui", () => {
  // NOTE: components that internally $emit an event name (e.g. "click") while
  // ALSO receiving that same listener via attrs fallthrough will fire it
  // TWICE unless `emits` is declared — Vue then knows to exclude it from
  // fallthrough and only invoke it once via the emit mechanism.
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NModal: stub(
      '<div class="stub-modal" v-if="show"><slot name="header" /><slot /><slot name="footer" /></div>',
      ["show"],
    ),
    NButton: stub(
      '<button class="stub-button" :disabled="disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
      ["disabled", "loading"],
      ["click"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSpin: stub('<div class="stub-spin"><slot /></div>'),
    NSwitch: stub(
      '<button class="stub-switch" @click="$emit(\'update:value\', !value)"><slot name="checked" /><slot name="unchecked" /></button>',
      ["value"],
      ["update:value"],
    ),
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
    useDialog: () => ({ warning: dialogWarningMock }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhPencil: stub("PhPencil"),
    PhUploadSimple: stub("PhUploadSimple"),
  };
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

function mountModal(props: Partial<InstanceType<typeof FileEditorModal>["$props"]> = {}) {
  return render(FileEditorModal, {
    props: {
      show: true,
      path: "/dir/b.ts",
      box: "local",
      token: null,
      theme: "light",
      ...props,
    },
    global: { plugins: [i18nPlugin] },
  });
}

describe("FileEditorModal — unsaved-changes guard", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("closes immediately on Cancel when the content was never edited", async () => {
    const { emitted, findByRole, container } = mountModal();
    // Wait for the initial fetch to populate the editor.
    await findByRole("textbox");
    const cancelButton = container.querySelectorAll(".stub-button")[0] as HTMLElement;
    await fireEvent.click(cancelButton);

    expect(dialogWarningMock).not.toHaveBeenCalled();
    expect(emitted()["update:show"]).toBeTruthy();
    expect(emitted()["update:show"]?.[0]).toEqual([false]);
  });

  it("prompts a discard-confirm dialog on Cancel when content was edited, and does NOT close without confirmation", async () => {
    const { emitted, findByRole, container } = mountModal();
    const textarea = (await findByRole("textbox")) as HTMLTextAreaElement;

    await fireEvent.update(textarea, "content of /dir/b.ts EDITED");

    const cancelButton = container.querySelectorAll(".stub-button")[0] as HTMLElement;
    await fireEvent.click(cancelButton);

    expect(dialogWarningMock).toHaveBeenCalledTimes(1);
    const call = dialogWarningMock.mock.calls[0]![0];
    expect(call.title).toBe("Discard unsaved changes?");
    // The modal must NOT close on its own — only the dialog's positive click may close it.
    expect(emitted()["update:show"]).toBeUndefined();
  });

  it("closes only after the discard dialog is confirmed (onPositiveClick)", async () => {
    const { emitted, findByRole, container } = mountModal();
    const textarea = (await findByRole("textbox")) as HTMLTextAreaElement;
    await fireEvent.update(textarea, "content of /dir/b.ts EDITED");

    const cancelButton = container.querySelectorAll(".stub-button")[0] as HTMLElement;
    await fireEvent.click(cancelButton);

    const call = dialogWarningMock.mock.calls[0]![0];
    call.onPositiveClick();

    expect(emitted()["update:show"]).toBeTruthy();
    expect(emitted()["update:show"]?.[0]).toEqual([false]);
  });

  it("saving then closing does NOT prompt the discard dialog", async () => {
    const { emitted, findByRole, container } = mountModal();
    const textarea = (await findByRole("textbox")) as HTMLTextAreaElement;
    await fireEvent.update(textarea, "content of /dir/b.ts EDITED");

    const saveButton = container.querySelectorAll(".stub-button")[1] as HTMLElement;
    await fireEvent.click(saveButton);

    expect(writeFileMock).toHaveBeenCalledTimes(1);
    // saveEdit() closes the modal itself after a successful save.
    expect(emitted()["update:show"]).toBeTruthy();
    expect(emitted()["update:show"]?.[0]).toEqual([false]);
    expect(dialogWarningMock).not.toHaveBeenCalled();
  });
});

describe("FileEditorModal — persisted view toggles", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("persists showLineNumbers/wordWrap toggles to localStorage when changed", async () => {
    const { findByRole, container } = mountModal();
    await findByRole("textbox");

    const switches = container.querySelectorAll(".stub-switch");
    expect(switches).toHaveLength(2);

    await fireEvent.click(switches[0]!); // line numbers switch
    expect(localStorage.getItem("sshler:editor:lineNumbers")).toBe("false");

    await fireEvent.click(switches[1]!); // word wrap switch
    expect(localStorage.getItem("sshler:editor:wordWrap")).toBe("false");
  });

  it("hydrates toggle state from localStorage on mount", async () => {
    localStorage.setItem("sshler:editor:lineNumbers", "false");
    localStorage.setItem("sshler:editor:wordWrap", "false");

    // We can't read internal component state directly, but we CAN prove the
    // persisted value round-trips: toggling once from the persisted "false"
    // baseline should flip localStorage back to "true".
    const { findByRole, container } = mountModal();
    await findByRole("textbox");
    const switches = container.querySelectorAll(".stub-switch");

    await fireEvent.click(switches[0]!);
    expect(localStorage.getItem("sshler:editor:lineNumbers")).toBe("true");
  });
});
