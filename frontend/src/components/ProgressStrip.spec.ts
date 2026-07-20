import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type App, nextTick } from "vue";

import type { ProgressBar } from "@/api/types";
import { createI18n } from "@/i18n";
import { useProgressStore } from "@/stores/progress";
import { useAppStore } from "@/stores/app";
import ProgressStrip from "./ProgressStrip.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NIcon: stub('<span class="stub-icon" />'),
    // ProgressPicker (nested) uses these; stub minimally.
    NModal: stub('<div v-if="show"><slot /></div>', ["show"]),
    NCard: stub("<div><slot /></div>", ["title"]),
    NButton: stub("<button><slot /></button>"),
    NEmpty: stub("<div />", ["description"]),
    NCheckbox: stub("<button />", ["checked"]),
    // Render both slots so the trigger (the strip-bar) AND the tooltip content
    // are present in the DOM for assertions.
    NTooltip: stub('<div class="stub-tip"><slot name="trigger" /><slot /></div>'),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhCheckCircle: stub("PhCheckCircle"),
    PhCircleDashed: stub("PhCircleDashed"),
    PhMinusCircle: stub("PhMinusCircle"),
    PhPlus: stub("PhPlus"),
    PhWarningCircle: stub("PhWarningCircle"),
    PhX: stub("PhX"),
    PhXCircle: stub("PhXCircle"),
  };
});

const i18nPlugin = {
  install(app: App) {
    createI18n(app);
  },
};

function makeBar(name: string, overrides: Partial<ProgressBar> = {}): ProgressBar {
  const now = Date.now() / 1000;
  return {
    name,
    current: 4,
    total: 10,
    color: null,
    label: null,
    status: "running",
    created_at: now - 5,
    updated_at: now,
    metadata: {},
    metadata_error: null,
    ...overrides,
  };
}

function mountStrip() {
  return render(ProgressStrip, { global: { plugins: [i18nPlugin] } });
}

describe("ProgressStrip", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    if (!window.matchMedia) {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: (query: string) => ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }),
      });
    }
  });

  it("renders nothing when no box is active", () => {
    const app = useAppStore();
    app.activeBox = null;
    const progress = useProgressStore();
    progress._injectEventForTest({ type: "snapshot", bars: [makeBar("x")] });
    const { container } = mountStrip();
    expect(container.querySelector(".progress-strip")).toBeNull();
  });

  it("renders the strip (with + button) when a box is active even with no subscriptions", () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const { container } = mountStrip();
    expect(container.querySelector(".progress-strip")).not.toBeNull();
    expect(container.querySelector(".strip-add")).not.toBeNull();
    expect(container.querySelectorAll(".strip-bar")).toHaveLength(0);
  });

  it("renders one strip-bar per subscribed bar in the active scope", async () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("a", { label: "Build A" }), makeBar("b"), makeBar("c")],
    });
    progress.subscribe("a");
    progress.subscribe("c");

    const { container } = mountStrip();
    await nextTick();
    const labels = Array.from(
      container.querySelectorAll(".strip-bar__label"),
    ).map((el) => el.textContent?.trim());
    expect(labels).toEqual(["Build A", "c"]);
    expect(container.querySelectorAll(".strip-bar")).toHaveLength(2);
  });

  it("shows different bars when the active box changes (per-box scope)", async () => {
    const app = useAppStore();
    const progress = useProgressStore();
    progress._injectEventForTest({ type: "snapshot", bars: [makeBar("a"), makeBar("b")] });

    app.activeBox = "sshler";
    progress.subscribe("a");
    const { container } = mountStrip();
    expect(container.querySelectorAll(".strip-bar")).toHaveLength(1);

    app.activeBox = "maintenance";
    await Promise.resolve();
    expect(container.querySelectorAll(".strip-bar")).toHaveLength(0);
  });

  it("opens the picker when the + button is clicked", async () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const progress = useProgressStore();
    progress._injectEventForTest({ type: "snapshot", bars: [makeBar("a")] });

    const { container } = mountStrip();
    // Picker modal (NModal stub) hidden initially because show=false.
    expect(container.querySelector(".picker-list")).toBeNull();
    const addBtn = container.querySelector(".strip-add") as HTMLElement;
    await fireEvent.click(addBtn);
    // After opening, the picker's list (or empty) renders inside the NModal stub.
    expect(container.querySelector(".picker-row")).not.toBeNull();
  });

  it("floors the displayed percent (3299/3300 shows 99%, never 100%)", async () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("build", { current: 3299, total: 3300 })],
    });
    progress.subscribe("build");

    const { container } = mountStrip();
    await nextTick();
    const numbers = container.querySelector(".strip-bar__numbers") as HTMLElement;
    expect(numbers.textContent?.trim()).toBe("99%");
  });

  it("flashes a bar once when it transitions running -> done", async () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("ci", { status: "running" })],
    });
    progress.subscribe("ci");

    const { container } = mountStrip();
    await nextTick();
    // Not flashing while still running.
    expect(container.querySelector(".strip-bar--flash")).toBeNull();

    progress._injectEventForTest({
      type: "upsert",
      name: "ci",
      bar: makeBar("ci", { status: "done", current: 10 }),
    });
    await nextTick();
    expect(container.querySelector(".strip-bar--flash")).not.toBeNull();
  });

  it("renders metadata fields and the error line in the tooltip content", async () => {
    const app = useAppStore();
    app.activeBox = "sshler";
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [
        makeBar("build", {
          metadata: { stage: "link", warnings: 3 },
          metadata_error: "metadata must be a JSON object",
        }),
      ],
    });
    progress.subscribe("build");

    const { container } = mountStrip();
    await nextTick();
    const tip = container.querySelector(".strip-tip") as HTMLElement;
    const meta = tip.querySelector(".strip-tip__meta") as HTMLElement;
    expect(meta.textContent).toContain("stage");
    expect(meta.textContent).toContain("link");
    expect(meta.textContent).toContain("warnings");
    expect(meta.textContent).toContain("3");
    const err = tip.querySelector(".strip-tip__error") as HTMLElement;
    expect(err.textContent?.trim()).toBe(
      "metadata error: metadata must be a JSON object",
    );
  });
});
