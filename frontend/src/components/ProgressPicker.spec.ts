import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import type { ProgressBar } from "@/api/types";
import { createI18n } from "@/i18n";
import { useProgressStore } from "@/stores/progress";
import { useAppStore } from "@/stores/app";
import ProgressPicker from "./ProgressPicker.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NModal: stub('<div class="stub-modal" v-if="show"><slot /></div>', ["show"]),
    NCard: stub(
      '<div class="stub-card"><div class="stub-card__title">{{ title }}</div><slot name="header-extra" /><slot /></div>',
      ["title"],
    ),
    NButton: stub('<button class="stub-button" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'),
    NIcon: stub('<span class="stub-icon" />'),
    NEmpty: stub('<div class="stub-empty">{{ description }}</div>', ["description"]),
    NCheckbox: stub(
      '<button class="stub-checkbox" :data-checked="checked" @click="$emit(\'update:checked\', !checked)" />',
      ["checked"],
    ),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({ name, template: `<span data-icon="${name}" />` });
  return {
    PhCheckCircle: stub("PhCheckCircle"),
    PhCircleDashed: stub("PhCircleDashed"),
    PhMinusCircle: stub("PhMinusCircle"),
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
    current: 3,
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

function mountPicker(show = true) {
  return render(ProgressPicker, {
    props: { show },
    global: { plugins: [i18nPlugin] },
  });
}

describe("ProgressPicker", () => {
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
    useAppStore().activeBox = "sshler";
  });

  it("shows the empty message when there are no bars", () => {
    const { container } = mountPicker();
    expect(container.querySelector(".stub-empty")).not.toBeNull();
  });

  it("renders one row per bar with the active scope in the title", async () => {
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("alpha", { label: "Alpha" }), makeBar("beta")],
    });
    const { container, findByText } = mountPicker();
    expect(await findByText("Alpha")).toBeTruthy();
    expect(await findByText("beta")).toBeTruthy();
    expect(container.querySelectorAll(".picker-row")).toHaveLength(2);
    // Title interpolates the active box.
    expect(container.querySelector(".stub-card__title")?.textContent).toContain("sshler");
  });

  it("toggling a checkbox subscribes/unsubscribes in the active scope", async () => {
    const progress = useProgressStore();
    progress._injectEventForTest({ type: "snapshot", bars: [makeBar("flip")] });
    expect(progress.isSubscribed("flip")).toBe(false);

    const { container } = mountPicker();
    const cb = container.querySelector(".stub-checkbox") as HTMLElement;
    expect(cb).not.toBeNull();
    await fireEvent.click(cb);
    expect(progress.isSubscribed("flip")).toBe(true);
    expect(JSON.parse(localStorage.getItem("sshler:progress:subscribed") || "{}")).toEqual({
      sshler: ["flip"],
    });

    await fireEvent.click(cb);
    expect(progress.isSubscribed("flip")).toBe(false);
  });

  it("emits update:show=false when the close button is clicked", async () => {
    const { container, emitted } = mountPicker();
    const closeBtn = container.querySelector(".stub-button") as HTMLElement;
    expect(closeBtn).not.toBeNull();
    await fireEvent.click(closeBtn);
    expect(emitted()["update:show"]).toBeTruthy();
    expect(emitted()["update:show"]![0]).toEqual([false]);
  });
});
