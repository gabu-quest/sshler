import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NSelect } from "naive-ui";

import { ApiError } from "@/api/apiError";
import TerminalView from "./TerminalView.vue";

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: "build-box", dir: "/srv" }, path: "/terminal", params: {}, name: "terminal" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("naive-ui", async () => {
  const actual = await vi.importActual<typeof import("naive-ui")>("naive-ui");
  return { ...actual, useMessage: () => messageSpy };
});

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "build-box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/", terminal_theme: "nord" },
  ]),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  gitInfo: vi.fn(async () => ({ branch: null, commit: null, is_repo: false, dirty: false })),
  setBoxTerminalTheme: vi.fn(),
}));

import { setBoxTerminalTheme } from "@/api/http";

describe("TerminalView: the box's terminal theme", () => {
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

  it("a theme the server did not save shows the failure's message", async () => {
    vi.mocked(setBoxTerminalTheme).mockRejectedValue(new ApiError(0, "network", ""));
    const wrapper = shallowMount(TerminalView);
    await flushPromises();
    const themeSelect = wrapper.findAllComponents(NSelect).find((c) => c.props("value") === "nord")!;
    themeSelect.vm.$emit("update:value", "dracula");
    await flushPromises();

    expect(vi.mocked(setBoxTerminalTheme)).toHaveBeenCalledTimes(1);
    expect(messageSpy.error.mock.calls).toEqual([["No answer from sshler; check the connection"]]);
    wrapper.unmount();
  });
});
