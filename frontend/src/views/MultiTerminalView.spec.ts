import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import MultiTerminalView from "./MultiTerminalView.vue";

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, path: "/multi-terminal", params: {}, name: "multi-terminal" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("naive-ui", async () => {
  const actual = await vi.importActual<typeof import("naive-ui")>("naive-ui");
  return { ...actual, useMessage: () => messageSpy };
});

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, basic_auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => []),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  fetchLayouts: vi.fn(),
  createLayout: vi.fn(),
  deleteLayout: vi.fn(),
}));

import { fetchLayouts } from "@/api/http";

describe("MultiTerminalView: the saved layouts", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });

  it("saved layouts that could not be listed show the failure's message", async () => {
    vi.mocked(fetchLayouts).mockRejectedValue(new ApiError(502, "unreachable", "sshler did not answer"));
    const wrapper = shallowMount(MultiTerminalView);
    await flushPromises();

    expect(messageSpy.error.mock.calls).toEqual([["Cannot connect to the box — sshler did not answer"]]);
    wrapper.unmount();
  });
});
