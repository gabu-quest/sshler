import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NButton, NInput, NSelect } from "naive-ui";
import { defineComponent, h } from "vue";

import Terminal from "@/components/Terminal.vue";
import SessionSwitcher from "@/components/SessionSwitcher.vue";
import { useAppStore } from "@/stores/app";
import TerminalView from "./TerminalView.vue";

const state = vi.hoisted(() => ({
  platform: "posix",
  box: "build" as string,
  dir: "/srv" as string | undefined,
  theme: null as string | null,
}));

// Every text comes back as its key, so a literal left in the view shows as itself.
vi.mock("@/i18n", async () => {
  const { computed, ref } = await import("vue");
  const locale = ref("en");
  const t = (key: string, params?: Record<string, string | number>) =>
    params ? `${key}${JSON.stringify(params)}` : key;
  return {
    translate: t,
    useI18n: () => ({ locale: computed(() => locale.value), t, setLocale: () => {} }),
    createI18n: vi.fn(),
    availableLocales: [{ label: "English", value: "en" }],
  };
});

vi.mock("vue-router", () => ({
  useRoute: () => ({
    query: { box: state.box, ...(state.dir ? { dir: state.dir } : {}) },
    path: "/terminal",
    params: {},
    name: "terminal",
  }),
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
    windows_shells: [{ id: "pwsh", label: "PowerShell 7", available: true }],
    default_shell: "pwsh",
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "localhost", transport: "local", favorites: [], pinned: false, default_dir: "/", terminal_theme: state.theme },
    { name: "build", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/", terminal_theme: state.theme },
  ]),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  gitInfo: vi.fn(async () => ({ branch: null, commit: null, is_repo: false, dirty: false })),
  setBoxTerminalTheme: vi.fn(),
}));

const innerWidth = window.innerWidth;

async function mountView(width = 1366) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  const pinia = createPinia();
  setActivePinia(pinia);
  const wrapper = shallowMount(TerminalView, {
    global: { plugins: [pinia], renderStubDefaultSlot: true },
  });
  await flushPromises();
  return { wrapper, app: useAppStore(pinia) };
}

const themeSelect = (w: ReturnType<typeof shallowMount>) =>
  w.findAllComponents(NSelect).find((c) => (c.props("options") as { value: string }[] | undefined)?.some((o) => o.value === "nord"));

describe("TerminalView: one row, one switcher, the phone's menu", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.assign(state, { platform: "posix", box: "build", dir: "/srv", theme: null });
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
    Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth });
  });

  // Mutation: the view's own session popover kept -> two switchers on a single terminal.
  it("a single terminal leaves the session switcher to its own title bar", async () => {
    const { wrapper } = await mountView();
    expect(wrapper.findAllComponents(Terminal)).toHaveLength(1);
    expect(wrapper.findAllComponents(SessionSwitcher)).toHaveLength(0);
    wrapper.unmount();
  });

  // Mutation: the view keeps its own switcher -> two Sessions buttons stacked in Windows tabs.
  // The tab's own title-bar button forwards a pick (TerminalTabs.spec), and the view opens it as a tab.
  it("Windows local tabs have no second session switcher, and a forwarded pick opens as a tab", async () => {
    Object.assign(state, { platform: "windows", box: "local" });
    const openSession = vi.fn();
    const TabsStub = defineComponent({
      name: "TerminalTabs",
      emits: ["session-select"],
      setup(_p, { expose }) {
        expose({ openSession });
        return () => h("div");
      },
    });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1366 });
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = shallowMount(TerminalView, {
      global: { plugins: [pinia], renderStubDefaultSlot: true, stubs: { TerminalTabs: TabsStub } },
    });
    await flushPromises();
    expect(wrapper.findAllComponents(SessionSwitcher)).toHaveLength(0);
    const pick = { session_name: "work", working_directory: "C:\\x" };
    wrapper.findComponent(TabsStub).vm.$emit("session-select", pick);
    expect(openSession).toHaveBeenCalledWith(pick);
    wrapper.unmount();
  });

  const dirSelect = (w: ReturnType<typeof shallowMount>) =>
    w.findAllComponents(NSelect).find((c) => (c.props("options") as { value: string }[] | undefined)?.some((o) => o.value === "__custom__"))!;
  const enter = (composing: boolean, keyCode = 13) =>
    new KeyboardEvent("keydown", { key: "Enter", isComposing: composing, keyCode });

  // Mutation: no close on the phone's custom path -> the user is stuck in the field.
  it("on the phone, the custom path field has a close button that returns to the list without applying", async () => {
    const { wrapper } = await mountView(390);
    await wrapper.find(".mobile-title-btn").trigger("click");
    dirSelect(wrapper).vm.$emit("update:value", "__custom__");
    await flushPromises();
    const dropdown = () => wrapper.find(".mobile-controls-dropdown");
    expect(dropdown().findComponent(NInput).exists()).toBe(true);
    dropdown().findComponent(NInput).vm.$emit("update:value", "/half/typed");
    const close = dropdown().findAllComponents(NButton).find((b: { attributes: (n: string) => string | undefined }) => b.attributes("aria-label") === "common.close")!;
    expect(close).toBeTruthy();
    close.vm.$emit("click");
    await flushPromises();
    expect(dropdown().findComponent(NInput).exists()).toBe(false);
    expect(dirSelect(wrapper).exists()).toBe(true);
    expect(dirSelect(wrapper).props("value")).toBe("/srv");
    wrapper.unmount();
  });

  // Mutation: Enter ends an IME conversion and applies the half-typed folder.
  it.each([
    ["desktop", 1366, ".header-select--dir"],
    ["phone", 390, ".mobile-controls-dropdown"],
  ])("on the %s, the Enter that ends a Japanese conversion does not apply the folder", async (_n, width, scope) => {
    const { wrapper } = await mountView(width);
    if (width < 768) await wrapper.find(".mobile-title-btn").trigger("click");
    dirSelect(wrapper).vm.$emit("update:value", "__custom__");
    await flushPromises();
    const input = scope === ".mobile-controls-dropdown"
      ? wrapper.find(scope).findComponent(NInput)
      : wrapper.findAllComponents(NInput).find((c) => c.classes().includes("header-select--dir"))!;
    input.vm.$emit("update:value", "/にほん");
    input.vm.$emit("keydown", enter(true, 229));
    input.vm.$emit("keydown", enter(false, 229));
    await flushPromises();
    expect(wrapper.findComponent(Terminal).props("directory")).toBe("/srv");
    input.vm.$emit("keydown", enter(false));
    await flushPromises();
    expect(wrapper.findComponent(Terminal).props("directory")).toBe("/にほん");
    wrapper.unmount();
  });

  // Mutation: no menu button in the terminal's phone row -> the drawer cannot be opened
  // (the 14 px bar above has none).
  it("on the phone, the menu button in the terminal's row opens the drawer", async () => {
    const { wrapper, app } = await mountView(390);
    const button = wrapper.find('.mobile-header button[aria-label="header.menu"]');
    expect(button.exists()).toBe(true);
    expect(app.mobileNavOpen).toBe(false);
    await button.trigger("click");
    expect(app.mobileNavOpen).toBe(true);
    wrapper.unmount();
  });

  // Mutation: literal aria-labels kept -> "Copy path", "Snippets", "Port Forwarding".
  it("the phone row's buttons are named through their keys", async () => {
    const { wrapper } = await mountView(390);
    const labels = wrapper.findAll(".mobile-header .mobile-action-btn").map((b) => b.attributes("aria-label"));
    expect(labels).toEqual([
      "header.menu", "files.copy_path", "terminal.add_favorite", "snippets.title", "tunnels.title", "terminal.browse_files",
    ]);
    wrapper.unmount();
  });
});

describe("TerminalView: the terminal's theme", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.assign(state, { platform: "posix", box: "build", dir: "/srv", theme: null });
  });

  // Mutation: the fallback left at cyberpunk -> a box with no saved theme opens cyberpunk.
  it("a box with no saved theme opens the calm theme, and every theme is still offered", async () => {
    const { wrapper } = await mountView();
    expect(wrapper.findComponent(Terminal).props("theme")).toBe("calm");
    const select = themeSelect(wrapper)!;
    expect(select.props("value")).toBe("calm");
    expect((select.props("options") as { value: string }[]).map((o) => o.value)).toEqual([
      "calm", "cyberpunk", "default", "solarized", "dracula", "nord", "monokai", "light",
    ]);
    wrapper.unmount();
  });

  it.each(["cyberpunk", "nord", "light"])("a saved %s is kept", async (saved) => {
    state.theme = saved;
    const { wrapper } = await mountView();
    expect(wrapper.findComponent(Terminal).props("theme")).toBe(saved);
    wrapper.unmount();
  });
});

describe("TerminalView: the box the address names", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.assign(state, { platform: "posix", box: "build", dir: "/srv", theme: null });
  });

  // Mutation: the old fallback to the first box -> ?box=nope opens a terminal on local.
  it("an unknown ?box= opens no terminal and says the box is not known", async () => {
    state.box = "nope";
    const { wrapper } = await mountView();
    expect(wrapper.findAllComponents(Terminal)).toHaveLength(0);
    expect(wrapper.find(".no-box-selected").text()).toContain("terminal.unknown_box");
    expect(wrapper.find(".no-box-selected").text()).toContain('terminal.unknown_box_named{"box":"nope"}');
    wrapper.unmount();
  });

  it("no ?box= opens the first box, as before", async () => {
    state.box = "";
    const { wrapper } = await mountView();
    expect(wrapper.findComponent(Terminal).props("boxName")).toBe("local");
    wrapper.unmount();
  });

  // Mutation: the directory list's English labels kept -> "📁 Browse..." and the rest.
  it("the directory list's own entries are named through their keys", async () => {
    const { wrapper } = await mountView();
    const dir = wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "terminal.dir")!;
    expect((dir.props("options") as { label: string }[]).map((o) => o.label)).toEqual([
      "terminal.dir_home_option", "terminal.dir_browse_option", "terminal.dir_custom_option",
    ]);
    wrapper.unmount();
  });
});
