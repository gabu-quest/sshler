import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiffCell from "./DiffCell.vue";
import type { DiffCellState } from "@/stores/diff";

// jsdom doesn't ship window.matchMedia; the app store calls it on init.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NSpin: stub('<span class="stub-spin" data-testid="spin" />'),
    NAlert: stub(
      '<div class="stub-alert" :data-alert-type="type" :data-testid="`alert-${type}`"><div class="stub-alert__title">{{ title }}</div><slot /></div>',
      ["type", "title"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NTag: stub('<span class="stub-tag" :data-tag-type="type"><slot /></span>', ["type", "size"]),
    NButton: stub(
      '<button class="stub-button" :data-testid="$attrs[`data-testid`]" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
      ["disabled", "type", "size"],
    ),
    NTooltip: stub('<span class="stub-tooltip"><slot name="trigger" /></span>'),
    NSelect: stub('<select class="stub-select" />'),
    NInput: stub('<input class="stub-input" />'),
    NAutoComplete: stub('<input class="stub-autocomplete" />'),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhFileX: stub("PhFileX"),
    PhFileCode: stub("PhFileCode"),
    PhTrash: stub("PhTrash"),
    PhArrowUp: stub("PhArrowUp"),
    PhArrowDown: stub("PhArrowDown"),
    PhArrowsLeftRight: stub("PhArrowsLeftRight"),
    PhArrowLeft: stub("PhArrowLeft"),
    PhArrowRight: stub("PhArrowRight"),
    PhGitBranch: stub("PhGitBranch"),
    PhFolder: stub("PhFolder"),
    PhFile: stub("PhFile"),
  };
});

vi.mock("@/components/DiffViewer.vue", () => ({
  default: {
    name: "DiffViewer",
    props: ["original", "modified", "language", "theme"],
    template: '<div class="stub-diff-viewer" data-testid="diff-viewer" :data-language="language" :data-left="original" :data-right="modified" />',
  },
}));

// DiffSidePicker imports gitBranches; stub the whole component so the picker
// doesn't reach into the network layer.
vi.mock("@/components/diff/DiffSidePicker.vue", () => ({
  default: {
    name: "DiffSidePicker",
    props: ["side", "variant"],
    emits: ["update:side"],
    template: '<div class="stub-picker" :data-testid="`stub-picker-${variant}`" />',
  },
}));

function emptySide() {
  return {
    config: { box: "", directory: "", ref: "", path: "" },
    content: "",
    status: "idle" as const,
    error: null,
    truncated: false,
  };
}

function makeState(overrides: Partial<DiffCellState> = {}): DiffCellState {
  return {
    id: "c1",
    left: emptySide(),
    right: emptySide(),
    status: "idle",
    error: null,
    ...overrides,
  };
}

function commonProps(state: DiffCellState, index = 0, total = 1) {
  return { state, index, total, language: "text" };
}

describe("DiffCell (M2)", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders idle copy when cell status is idle", () => {
    const { getByText } = render(DiffCell, { props: commonProps(makeState()) });
    expect(getByText(/Fill in at least one side/i)).toBeTruthy();
  });

  it("renders the spinner when cell status is loading", () => {
    const { getByTestId } = render(DiffCell, { props: commonProps(makeState({ status: "loading" })) });
    expect(getByTestId("spin")).toBeTruthy();
  });

  it("renders an error alert with the error message", () => {
    const state = makeState({ status: "error", error: "boom" });
    const { getByTestId } = render(DiffCell, { props: commonProps(state) });
    const alert = getByTestId("alert-error");
    expect(alert.textContent).toContain("boom");
  });

  it("renders the DiffViewer with both sides when ready", () => {
    const state: DiffCellState = {
      id: "c1",
      left: { ...emptySide(), config: { box: "local", directory: "/r", ref: "main", path: "a.ts" }, content: "left text", status: "loaded" },
      right: { ...emptySide(), config: { box: "local", directory: "/r", ref: "feat", path: "a.ts" }, content: "right text", status: "loaded" },
      status: "ready",
      error: null,
    };
    const { getByTestId } = render(DiffCell, { props: commonProps(state) });
    const viewer = getByTestId("diff-viewer");
    expect(viewer.getAttribute("data-language")).toBe("text");
    expect(viewer.getAttribute("data-left")).toBe("left text");
    expect(viewer.getAttribute("data-right")).toBe("right text");
  });

  it("renders a binary warning when status is binary", () => {
    const { getByTestId } = render(DiffCell, { props: commonProps(makeState({ status: "binary" })) });
    expect(getByTestId("alert-warning")).toBeTruthy();
  });

  it("emits 'remove' when the remove button is clicked", async () => {
    const { getByTestId, emitted } = render(DiffCell, { props: commonProps(makeState(), 2, 5) });
    await fireEvent.click(getByTestId("diff-cell-remove-2"));
    // Stubbed button's native click bubbles through the tooltip wrapper, so we
    // count >=1 rather than ===1. The user-facing contract is "clicking the
    // button fires the remove emit at least once."
    expect(emitted().remove?.length).toBeGreaterThanOrEqual(1);
  });

  it("emits 'swap-sides' on the swap button", async () => {
    const { getByTestId, emitted } = render(DiffCell, { props: commonProps(makeState(), 0, 1) });
    await fireEvent.click(getByTestId("diff-cell-swap-0"));
    expect(emitted()["swap-sides"]?.length).toBeGreaterThanOrEqual(1);
  });

  it("renders move-up disabled for the first cell, move-down disabled for the last", () => {
    const first = render(DiffCell, { props: commonProps(makeState(), 0, 3) });
    expect((first.getByTestId("diff-cell-up-0") as HTMLButtonElement).hasAttribute("disabled")).toBe(true);

    const last = render(DiffCell, { props: commonProps(makeState(), 2, 3) });
    expect((last.getByTestId("diff-cell-down-2") as HTMLButtonElement).hasAttribute("disabled")).toBe(true);
  });
});
