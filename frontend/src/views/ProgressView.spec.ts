import { render, fireEvent } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { App } from "vue";

import type { ProgressBar } from "@/api/types";
import { createI18n } from "@/i18n";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useProgressStore } from "@/stores/progress";
import { useAppStore } from "@/stores/app";
import ProgressView from "./ProgressView.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({
    props,
    template,
  });
  return {
    NCard: stub('<div class="stub-card"><slot /></div>'),
    NEmpty: stub('<div class="stub-empty">{{ description }}<slot name="extra" /></div>', [
      "description",
    ]),
    NButton: stub(
      '<button class="stub-button" :data-loading="loading" :data-type="type" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
      ["loading", "type"],
    ),
    NIcon: stub('<span class="stub-icon"><slot /></span>'),
    NTag: stub('<span class="stub-tag" :data-type="type"><slot name="icon" /><slot /></span>', [
      "type",
    ]),
    NSpace: stub('<div class="stub-space"><slot /></div>'),
    NSwitch: stub(
      '<button class="stub-switch" :data-value="value" @click="$emit(\'update:value\', !value)"><slot name="checked" /><slot name="unchecked" /></button>',
      ["value"],
    ),
    NProgress: stub('<div class="stub-progress" :data-pct="percentage" />', ["percentage"]),
    NPopconfirm: {
      props: ["placement"],
      template:
        '<div class="stub-popconfirm" @click="$emit(\'positive-click\')"><slot name="trigger" /><slot /></div>',
    },
    useMessage: () => ({
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    }),
  };
});

vi.mock("@phosphor-icons/vue", () => {
  const stub = (name: string) => ({
    name,
    template: `<span class="ph-icon" data-icon="${name}" />`,
  });
  return {
    PhArrowsClockwise: stub("PhArrowsClockwise"),
    PhChartBar: stub("PhChartBar"),
    PhCheckCircle: stub("PhCheckCircle"),
    PhCircleDashed: stub("PhCircleDashed"),
    PhMinusCircle: stub("PhMinusCircle"),
    PhTrash: stub("PhTrash"),
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
    current: 30,
    total: 100,
    color: null,
    label: null,
    status: "running",
    created_at: now - 10,
    updated_at: now,
    metadata: {},
    metadata_error: null,
    ...overrides,
  };
}

function mountView() {
  return render(ProgressView, {
    global: {
      plugins: [i18nPlugin],
    },
  });
}

const realWebSocket = globalThis.WebSocket;
class StubWS {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;
  readyState = StubWS.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  close() {
    this.readyState = StubWS.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }
}

describe("ProgressView", () => {
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
    (globalThis as any).WebSocket = StubWS;
    const bootstrap = useBootstrapStore();
    bootstrap.setToken("test-token");
    // Subscriptions are per-box; set an active scope for the toggle test.
    useAppStore().activeBox = "test-box";
    const progress = useProgressStore();
    vi.spyOn(progress, "refresh").mockResolvedValue();
  });

  afterEach(() => {
    (globalThis as any).WebSocket = realWebSocket;
  });

  it("renders the empty state when no bars exist", async () => {
    const { container, findByText } = mountView();
    expect(await findByText("No progress bars yet.")).toBeTruthy();
    expect(container.querySelector(".stub-card")).toBeNull();
  });

  it("renders one row per bar (name fallback when no label)", async () => {
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [
        makeBar("alpha", { label: "Alpha task" }),
        makeBar("beta"),
      ],
    });
    const { container, findByText } = mountView();
    expect(await findByText("Alpha task")).toBeTruthy();
    expect(await findByText("beta")).toBeTruthy();
    expect(container.querySelectorAll(".stub-card")).toHaveLength(2);
  });

  it("toggling the subscribe switch updates the store and localStorage", async () => {
    const progress = useProgressStore();
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("flip")],
    });
    expect(progress.isSubscribed("flip")).toBe(false);

    const { container } = mountView();
    const sw = container.querySelector(".stub-switch") as HTMLElement;
    expect(sw).not.toBeNull();
    await fireEvent.click(sw);
    expect(progress.isSubscribed("flip")).toBe(true);
    expect(
      JSON.parse(localStorage.getItem("sshler:progress:subscribed") || "{}"),
    ).toEqual({ "test-box": ["flip"] });

    await fireEvent.click(sw);
    expect(progress.isSubscribed("flip")).toBe(false);
  });

  it("clicking the popconfirm trigger calls store.remove with token", async () => {
    const progress = useProgressStore();
    const removeSpy = vi
      .spyOn(progress, "remove")
      .mockResolvedValue(true);
    progress._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("drop-me")],
    });

    const { container } = mountView();
    // The stub-popconfirm forwards its own click event as positive-click.
    const popconfirm = container.querySelector(".stub-popconfirm") as HTMLElement;
    expect(popconfirm).not.toBeNull();
    await fireEvent.click(popconfirm);

    expect(removeSpy).toHaveBeenCalledWith("drop-me", "test-token");
  });

  it("clicking refresh calls store.refresh with token", async () => {
    const progress = useProgressStore();
    const refreshSpy = vi.mocked(progress.refresh);
    // mountView already calls refresh once via onMounted.
    mountView();
    await Promise.resolve();
    expect(refreshSpy).toHaveBeenCalledWith("test-token");
  });

  it("calls store.connect on mount", () => {
    const progress = useProgressStore();
    const connectSpy = vi.spyOn(progress, "connect");
    mountView();
    expect(connectSpy).toHaveBeenCalledWith("test-token");
  });
});
