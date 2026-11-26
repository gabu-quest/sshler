import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NSelect } from "naive-ui";

import Terminal from "@/components/Terminal.vue";
import TerminalTabs from "@/components/TerminalTabs.vue";
import MobileInputBar from "@/components/MobileInputBar.vue";
import TerminalView from "./TerminalView.vue";

const state = vi.hoisted(() => ({ platform: "posix", box: "local" }));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { box: state.box, dir: "/srv" }, path: "/terminal", params: {}, name: "terminal" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("naive-ui", async () => {
  const actual = await vi.importActual<typeof import("naive-ui")>("naive-ui");
  const message = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
  return { ...actual, useMessage: () => message };
});

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: state.platform,
    windows_shells: [
      { id: "pwsh", label: "PowerShell 7", available: true },
      { id: "cmd", label: "Command Prompt", available: true },
    ],
    default_shell: "pwsh",
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "localhost", transport: "local", favorites: [], pinned: false, default_dir: "/", terminal_theme: "nord" },
    { name: "build", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/", terminal_theme: "nord" },
  ]),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  gitInfo: vi.fn(async () => ({ branch: null, commit: null, is_repo: false, dirty: false })),
  setBoxTerminalTheme: vi.fn(),
}));

async function mountView() {
  const wrapper = shallowMount(TerminalView);
  await flushPromises();
  return wrapper;
}

const shellSelect = (w: Awaited<ReturnType<typeof mountView>>) =>
  w.findAllComponents(NSelect).find((c) => c.props("placeholder") === "Shell");

describe("TerminalView: the Shell choice", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    state.platform = "posix";
    state.box = "local";
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      }),
    });
  });

  it.each(["local", "build"])("on the POSIX box %s the dropdown offers exactly tmux and Plain shell, at tmux", async (box) => {
    state.box = box;
    const wrapper = await mountView();
    const select = shellSelect(wrapper)!;

    expect(select.props("options")).toEqual([
      { label: "tmux", value: "tmux" },
      { label: "Plain shell", value: "plain" },
    ]);
    expect(select.props("value")).toBe("tmux");
    expect(wrapper.findComponent(Terminal).props("shell")).toBe("");
    wrapper.unmount();
  });

  it("picking Plain shell reconnects that terminal as plain, and stores nothing", async () => {
    const wrapper = await mountView();
    const stored = JSON.stringify({ ...localStorage });
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();

    expect(wrapper.findComponent(Terminal).props("shell")).toBe("plain");
    expect(shellSelect(wrapper)!.props("value")).toBe("plain");
    expect(JSON.stringify({ ...localStorage })).toBe(stored);
    wrapper.unmount();
  });

  it("opening the page again opens tmux, after a plain pick", async () => {
    const first = await mountView();
    shellSelect(first)!.vm.$emit("update:value", "plain");
    await flushPromises();
    first.unmount();

    const second = await mountView();
    expect(second.findComponent(Terminal).props("shell")).toBe("");
    expect(shellSelect(second)!.props("value")).toBe("tmux");
    second.unmount();
  });

  it("picking Plain shell twice leaves one terminal, plain", async () => {
    const wrapper = await mountView();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();

    expect(wrapper.findAllComponents(Terminal)).toHaveLength(1);
    expect(wrapper.findComponent(Terminal).props("shell")).toBe("plain");
    wrapper.unmount();
  });

  it("a new terminal in another box starts at tmux again", async () => {
    const wrapper = await mountView();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    const boxSelect = wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "Box")!;
    boxSelect.vm.$emit("update:value", "build");
    await flushPromises();

    expect(wrapper.findComponent(Terminal).props("shell")).toBe("");
    expect(shellSelect(wrapper)!.props("value")).toBe("tmux");
    wrapper.unmount();
  });

  it("a fallback to a plain shell shows Plain shell in the dropdown, and picking tmux asks again", async () => {
    const wrapper = await mountView();
    const first = wrapper.findComponent(Terminal);
    first.vm.$emit("shell", { kind: "plain", fallback: true });
    await flushPromises();
    expect(shellSelect(wrapper)!.props("value")).toBe("plain");

    shellSelect(wrapper)!.vm.$emit("update:value", "tmux");
    await flushPromises();
    expect(wrapper.findComponent(Terminal).props("shell")).toBe("");
    expect(shellSelect(wrapper)!.props("value")).toBe("tmux");
    expect(wrapper.findComponent(Terminal).element).not.toBe(first.element);
    wrapper.unmount();
  });

  it("a tmux frame, or a plain one that is not a fallback, leaves the dropdown as picked", async () => {
    const wrapper = await mountView();
    wrapper.findComponent(Terminal).vm.$emit("shell", { kind: "tmux", fallback: false });
    await flushPromises();
    expect(shellSelect(wrapper)!.props("value")).toBe("tmux");
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    wrapper.findComponent(Terminal).vm.$emit("shell", { kind: "plain", fallback: false });
    await flushPromises();
    expect(shellSelect(wrapper)!.props("value")).toBe("plain");
    wrapper.unmount();
  });

  it("picking a session from a plain terminal opens that tmux session, not a plain shell of its name", async () => {
    const wrapper = await mountView();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    wrapper.findComponent(Terminal).vm.$emit("session-select", { session_name: "work", working_directory: "/srv/work" });
    await flushPromises();

    const terminal = wrapper.findComponent(Terminal);
    expect(terminal.props("shell")).toBe("");
    expect(terminal.props("sessionName")).toBe("work");
    expect(shellSelect(wrapper)!.props("value")).toBe("tmux");
    wrapper.unmount();
  });

  it("a Windows local box keeps its shell list and its stored choice", async () => {
    state.platform = "windows";
    localStorage.setItem("sshler.windows_shell", "cmd");
    const wrapper = await mountView();
    const select = shellSelect(wrapper)!;

    expect(select.props("options")).toEqual([
      { label: "PowerShell 7", value: "pwsh" },
      { label: "Command Prompt", value: "cmd" },
    ]);
    expect(select.props("value")).toBe("cmd");
    select.vm.$emit("update:value", "pwsh");
    await flushPromises();
    expect(localStorage.getItem("sshler.windows_shell")).toBe("pwsh");
    expect(wrapper.findComponent(TerminalTabs).exists()).toBe(true);
    wrapper.unmount();
  });
});

describe("TerminalView: the phone's input bar knows the kind of terminal", () => {
  const widthBefore = window.innerWidth;
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    state.platform = "posix";
    state.box = "local";
    window.innerWidth = 400;
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
    window.innerWidth = widthBefore;
  });

  // On a phone the Box and Shell dropdowns are in the controls panel under the title.
  const mountPhone = async () => {
    const wrapper = await mountView();
    await wrapper.find(".mobile-title-btn").trigger("click");
    return wrapper;
  };

  const barTmux = (w: Awaited<ReturnType<typeof mountView>>) => w.findComponent(MobileInputBar).props("tmux");

  it("a new terminal is tmux: the bar shows the tmux keys", async () => {
    const wrapper = await mountPhone();
    expect(barTmux(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("picking Plain shell takes the tmux keys away before any frame; picking tmux gives them back", async () => {
    const wrapper = await mountPhone();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    expect(barTmux(wrapper)).toBe(false);
    shellSelect(wrapper)!.vm.$emit("update:value", "tmux");
    await flushPromises();
    expect(barTmux(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("a fallback frame takes them away, and another box brings them back", async () => {
    const wrapper = await mountPhone();
    wrapper.findComponent(Terminal).vm.$emit("shell", { kind: "plain", fallback: true });
    await flushPromises();
    expect(barTmux(wrapper)).toBe(false);
    wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "Box")!.vm.$emit("update:value", "build");
    await flushPromises();
    expect(barTmux(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("a tmux frame on a terminal that was asked plain wins: the keys are shown", async () => {
    const wrapper = await mountPhone();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    wrapper.findComponent(Terminal).vm.$emit("shell", { kind: "tmux", fallback: false });
    await flushPromises();
    expect(barTmux(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("a Windows local box never shows them", async () => {
    state.platform = "windows";
    const wrapper = await mountPhone();
    expect(barTmux(wrapper)).toBe(false);
    wrapper.unmount();
  });
});
