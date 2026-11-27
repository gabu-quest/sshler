import { flushPromises, mount, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { NSelect } from "naive-ui";
import Terminal from "@/components/Terminal.vue";
import MultiTerminalView from "./MultiTerminalView.vue";

const { messageSpy, state } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  state: { platform: "posix" },
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, path: "/multi-terminal", params: {}, name: "multi-terminal" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// The dialogs, menus and selects render their content in place, so the spec can reach it.
vi.mock("naive-ui", async () => {
  const actual = await vi.importActual<typeof import("naive-ui")>("naive-ui");
  const { defineComponent, h } = await import("vue");
  const NModal = defineComponent({
    props: { show: Boolean, title: String },
    setup: (props, { slots }) => () =>
      props.show ? h("div", { class: "modal" }, [slots.default?.(), slots.footer?.()]) : null,
  });
  const NSelect = defineComponent({
    props: { value: null, options: Array, placeholder: String },
    emits: ["update:value"],
    setup: () => () => h("div"),
  });
  const NButton = defineComponent({
    emits: ["click"],
    setup: (_p, { slots, emit }) => () => h("button", { onClick: () => emit("click") }, slots.default?.()),
  });
  const NSpace = defineComponent({ setup: (_p, { slots }) => () => h("div", slots.default?.()) });
  const NPopover = defineComponent({
    setup: (_p, { slots }) => () => h("div", [slots.trigger?.(), slots.default?.()]),
  });
  return { ...actual, useMessage: () => messageSpy, NModal, NSelect, NButton, NSpace, NPopover };
});

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: state.platform,
    windows_shells: [{ id: "pwsh", label: "PowerShell 7", available: true }], default_shell: "pwsh",
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "local", host: "localhost", transport: "local", favorites: [], pinned: false, default_dir: "/", terminal_theme: "nord" },
    { name: "build", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/", terminal_theme: "nord" },
  ]),
  fetchFavorites: vi.fn(async () => []),
  fetchBoxFavorites: vi.fn(async () => []),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  fetchLayouts: vi.fn(),
  createLayout: vi.fn(),
  deleteLayout: vi.fn(),
}));

import { createLayout, fetchLayouts } from "@/api/http";

function reset() {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  localStorage.clear();
  state.platform = "posix";
  vi.mocked(fetchLayouts).mockResolvedValue([]);
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  });
}

beforeEach(reset);

describe("MultiTerminalView: the saved layouts", () => {
  it("saved layouts that could not be listed show the failure's message", async () => {
    vi.mocked(fetchLayouts).mockRejectedValue(new ApiError(502, "unreachable", "sshler did not answer"));
    const wrapper = shallowMount(MultiTerminalView);
    await flushPromises();

    expect(messageSpy.error.mock.calls).toEqual([["Cannot connect to the box — sshler did not answer"]]);
    wrapper.unmount();
  });
});

const LAYOUT_KEY = "sshler:multi-terminal:layout";

async function mountView() {
  const wrapper = mount(MultiTerminalView, {
    global: { stubs: { Terminal: true, DirectoryPickerModal: true } },
  });
  await flushPromises();
  return wrapper;
}

type Wrapper = Awaited<ReturnType<typeof mountView>>;
const shellSelect = (w: Wrapper) => w.findAllComponents(NSelect).find((c) => c.props("placeholder") === "Shell");
const boxSelect = (w: Wrapper) => w.findAllComponents(NSelect).find((c) => c.props("placeholder") === "Choose box")!;
const buttonLabelled = (w: Wrapper, text: string) => w.findAll("button").find((b) => b.text() === text)!;

async function addTerminal(w: Wrapper, box: string, shell?: string) {
  await buttonLabelled(w, "Add Terminal").trigger("click");
  boxSelect(w).vm.$emit("update:value", box);
  await flushPromises();
  if (shell) shellSelect(w)!.vm.$emit("update:value", shell);
  await flushPromises();
  await w.find(".modal").findAll("button").find((b) => b.text() === "Add Terminal")!.trigger("click");
  await flushPromises();
}

describe("MultiTerminalView: the Shell choice in Add Terminal", () => {
  it.each(["local", "build"])("the dialog offers exactly tmux and Plain shell for the box %s, at tmux", async (box) => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", box);
    await flushPromises();
    const select = shellSelect(wrapper)!;

    expect(select.props("options")).toEqual([
      { label: "tmux", value: "tmux" },
      { label: "Plain shell", value: "plain" },
    ]);
    expect(select.props("value")).toBe("tmux");
    wrapper.unmount();
  });

  it("a plain pick that was cancelled is gone when the dialog opens again", async () => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();
    shellSelect(wrapper)!.vm.$emit("update:value", "plain");
    await flushPromises();
    await buttonLabelled(wrapper, "Cancel").trigger("click");
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");

    expect(shellSelect(wrapper)!.props("value")).toBe("tmux");
    wrapper.unmount();
  });

  it("a Windows local box shows no Shell select in the dialog", async () => {
    state.platform = "windows";
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();

    expect(shellSelect(wrapper)).toBeUndefined();
    wrapper.unmount();
  });

  it("a plain pane gets shell=plain, a default pane gets none, and the dialog starts at tmux again", async () => {
    const wrapper = await mountView();
    await addTerminal(wrapper, "local", "plain");
    await addTerminal(wrapper, "local");

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("shell"))).toEqual(["plain", ""]);
    wrapper.unmount();
  });

  it("the current layout keeps each pane's kind, and a reload brings the plain pane back plain", async () => {
    const first = await mountView();
    await addTerminal(first, "local", "plain");
    await addTerminal(first, "build");
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)!).map((p: { shell: string }) => p.shell)).toEqual(["plain", "tmux"]);
    first.unmount();

    const second = await mountView();
    expect(second.findAllComponents(Terminal).map((c) => c.props("shell"))).toEqual(["plain", ""]);
    second.unmount();
  });

  it("a layout saved before the kind existed, and panes with a shell the page does not know, open as tmux", async () => {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify([
      { boxName: "local", sessionName: "a", directory: "/" },
      { boxName: "local", sessionName: "b", directory: "/", shell: "zsh" },
      { boxName: "local", sessionName: "c", directory: "/", shell: 7 },
      { boxName: "local", sessionName: "d", directory: "/", shell: "plain" },
    ]));
    const wrapper = await mountView();

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("shell"))).toEqual(["", "", "", "plain"]);
    wrapper.unmount();
  });

  it("a named layout is saved with each pane's kind and comes back with it", async () => {
    const wrapper = await mountView();
    await addTerminal(wrapper, "local", "plain");
    await buttonLabelled(wrapper, "Save Layout").trigger("click");
    await wrapper.find("input").setValue("two");
    await wrapper.findAll(".modal").at(-1)!.findAll("button").find((b) => b.text() === "Save")!.trigger("click");
    await flushPromises();

    const saved = vi.mocked(createLayout).mock.calls[0]![1] as unknown as { shell: string }[];
    expect(saved.map((p) => p.shell)).toEqual(["plain"]);
    wrapper.unmount();
  });

  it("a named layout from before the kind existed loads as tmux", async () => {
    vi.mocked(fetchLayouts).mockResolvedValue([
      { id: "1", name: "old", created_at: 1, terminals: [{ boxName: "local", sessionName: "a", directory: "/" }] },
      { id: "2", name: "new", created_at: 2, terminals: [{ boxName: "local", sessionName: "b", directory: "/", shell: "plain" }] },
    ] as never);
    const wrapper = await mountView();
    const rows = wrapper.findAll(".layout-row");
    await rows.find((r) => r.text().includes("old"))!.trigger("click");
    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("shell"))).toEqual([""]);
    await rows.find((r) => r.text().includes("new"))!.trigger("click");
    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("shell"))).toEqual(["plain"]);
    wrapper.unmount();
  });
});

// A Terminal stand-in that draws its title-bar slot and a close button, as the real one does.
async function mountWithPanes(layout: object[]) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  const { defineComponent, h } = await import("vue");
  const TerminalStub = defineComponent({
    name: "Terminal",
    props: { boxName: String, sessionName: String, directory: String, shell: String, nativeShell: { type: Boolean, default: undefined }, fontSize: Number, theme: String, closable: Boolean, showTitleBar: { type: Boolean, default: true } },
    emits: ["close", "session-select"],
    setup: (_p, { slots, emit }) => () =>
      h("div", { class: "pane-stub" }, [
        h("span", { class: "meta" }, slots["titlebar-meta"]?.()),
        h("button", { class: "pane-session", onClick: () => emit("session-select", { session_name: "picked", working_directory: "/picked/dir" }) }, "s"),
        h("button", { class: "pane-close", onClick: () => emit("close") }, "x"),
      ]),
  });
  const wrapper = mount(MultiTerminalView, {
    global: { stubs: { Terminal: TerminalStub, DirectoryPickerModal: true } },
  });
  await flushPromises();
  return wrapper;
}

const pane = (box: string, dir: string, session = dir) => ({ boxName: box, sessionName: session, directory: dir, shell: "tmux" });

describe("MultiTerminalView: one title bar per pane", () => {
  it("has no outer pane header: the pane's own title bar closes it, and shows the box and folder", async () => {
    const wrapper = await mountWithPanes([pane("local", "/srv/app")]);

    expect(wrapper.find(".terminal-header").exists()).toBe(false);
    const terminal = wrapper.findComponent(Terminal);
    expect(terminal.props("closable")).toBe(true);
    expect(terminal.props("showTitleBar")).not.toBe(false);
    expect(wrapper.find(".meta").text()).toContain("app");
    wrapper.unmount();
  });

  it("closing the middle of three panes leaves the other two, in order", async () => {
    const wrapper = await mountWithPanes([pane("local", "/a"), pane("build", "/b"), pane("local", "/c")]);
    await wrapper.findAll(".pane-close")[1]!.trigger("click");

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("directory"))).toEqual(["/a", "/c"]);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)!).map((p: { directory: string }) => p.directory)).toEqual(["/a", "/c"]);
    wrapper.unmount();
  });

  // Mutation: no theme passed -> every pane turns calm and the box's saved choice is lost.
  it("each pane takes its box's saved theme, an unknown box the calm one", async () => {
    const wrapper = await mountWithPanes([pane("local", "/a"), pane("nope", "/b")]);

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("theme"))).toEqual(["nord", "calm"]);
    wrapper.unmount();
  });

  it("the toolbar is one row: no subtitle line, the count beside the title", async () => {
    const wrapper = await mountWithPanes([pane("local", "/a"), pane("local", "/b")]);

    expect(wrapper.find(".header p").exists()).toBe(false);
    expect(wrapper.find(".header").text()).toContain("2");
    expect(wrapper.find(".header").text()).toContain("Add Terminal");
    wrapper.unmount();
  });
});

describe("MultiTerminalView: the directory list", () => {
  it("offers the shared list with a custom path, named through the keys", async () => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();
    const dir = wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "~ or select from favorites")!;

    expect((dir.props("options") as { label: string }[]).map((o) => o.label)).toEqual([
      "~ (Home)", "Browse...", "+ Custom path...",
    ]);
    wrapper.unmount();
  });

  it("a custom path typed in the dialog is the pane's directory", async () => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();
    const dir = wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "~ or select from favorites")!;
    dir.vm.$emit("update:value", "__custom__");
    await flushPromises();
    await wrapper.find("input.custom-dir").setValue("/opt/thing");
    await wrapper.find(".modal").findAll("button").find((b) => b.text() === "Add Terminal")!.trigger("click");
    await flushPromises();

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("directory"))).toEqual(["/opt/thing"]);
    wrapper.unmount();
  });
  const openCustom = async () => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();
    const find = () => wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "~ or select from favorites");
    const home = (find()!.props("options") as { value: string }[])[0].value;
    find()!.vm.$emit("update:value", home);
    await flushPromises();
    find()!.vm.$emit("update:value", "__custom__");
    await flushPromises();
    return { wrapper, find, home };
  };

  it("Back beside the custom path returns to the list with its previous choice", async () => {
    const { wrapper, find, home } = await openCustom();
    expect(find()).toBeUndefined();
    await wrapper.find(".modal").findAll("button").find((b) => b.text() === "Back")!.trigger("click");
    await flushPromises();

    expect(wrapper.find("input.custom-dir").exists()).toBe(false);
    expect(find()!.props("value")).toBe(home);
    wrapper.unmount();
  });

  it("Escape in the custom path field does the same as Back", async () => {
    const { wrapper, find, home } = await openCustom();
    await wrapper.find("input.custom-dir").trigger("keydown", { key: "Escape" });
    await flushPromises();

    expect(wrapper.find("input.custom-dir").exists()).toBe(false);
    expect(find()!.props("value")).toBe(home);
    wrapper.unmount();
  });
});

describe("MultiTerminalView: a pane's session pick", () => {
  it("the title bar's pick switches that pane to the picked session in the same box", async () => {
    const wrapper = await mountWithPanes([pane("local", "/a", "one"), pane("build", "/b", "two")]);
    await wrapper.findAll(".pane-session")[1]!.trigger("click");
    await flushPromises();

    const panes = wrapper.findAllComponents(Terminal);
    expect(panes.map((c) => c.props("sessionName"))).toEqual(["one", "picked"]);
    expect(panes.map((c) => c.props("boxName"))).toEqual(["local", "build"]);
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY)!).map((p: { sessionName: string }) => p.sessionName)).toEqual(["one", "picked"]);
    wrapper.unmount();
  });

  it("a plain pane that picks a session becomes tmux", async () => {
    const wrapper = await mountWithPanes([{ ...pane("local", "/a", "one"), shell: "plain" }]);
    await wrapper.find(".pane-session").trigger("click");
    await flushPromises();

    expect(wrapper.findComponent(Terminal).props("shell")).toBe("");
    wrapper.unmount();
  });
});

describe("MultiTerminalView: the custom path field and the box", () => {
  const openCustomPath = async () => {
    const wrapper = await mountView();
    await buttonLabelled(wrapper, "Add Terminal").trigger("click");
    boxSelect(wrapper).vm.$emit("update:value", "local");
    await flushPromises();
    const dir = () => wrapper.findAllComponents(NSelect).find((c) => c.props("placeholder") === "~ or select from favorites");
    dir()!.vm.$emit("update:value", "__custom__");
    await flushPromises();
    return { wrapper, dir };
  };
  const addButton = (w: Wrapper) => w.find(".modal").findAll("button").find((b) => b.text() === "Add Terminal")!;

  it("an Enter that confirms an IME conversion does not add the pane", async () => {
    const { wrapper } = await openCustomPath();
    const input = wrapper.find("input.custom-dir");
    await input.setValue("/opt/日本");
    await input.trigger("keydown", { key: "Enter", isComposing: true });
    await input.trigger("keydown", { key: "Enter", keyCode: 229 });
    await flushPromises();

    expect(wrapper.findAllComponents(Terminal)).toHaveLength(0);
    wrapper.unmount();
  });

  it("a plain Enter adds the pane", async () => {
    const { wrapper } = await openCustomPath();
    const input = wrapper.find("input.custom-dir");
    await input.setValue("/opt/thing");
    await input.trigger("keydown", { key: "Enter" });
    await flushPromises();

    expect(wrapper.findAllComponents(Terminal).map((c) => c.props("directory"))).toEqual(["/opt/thing"]);
    wrapper.unmount();
  });

  it("changing the box resets the folder to home and leaves custom path", async () => {
    const { wrapper, dir } = await openCustomPath();
    await wrapper.find("input.custom-dir").setValue("/opt/thing");
    boxSelect(wrapper).vm.$emit("update:value", "build");
    await flushPromises();

    expect(wrapper.find("input.custom-dir").exists()).toBe(false);
    expect(dir()!.props("value")).toBe("~");
    await addButton(wrapper).trigger("click");
    await flushPromises();
    expect(wrapper.findAllComponents(Terminal).map((c) => [c.props("boxName"), c.props("directory")])).toEqual([["build", "~"]]);
    wrapper.unmount();
  });
});

describe("MultiTerminalView: a Windows local box's pane asks what the single view asks", () => {
  // The single view opens a Windows local box in TerminalTabs, whose Terminal gets the stored
  // shell id (empty for the default) and native-shell = true; a pane asks the same.
  it("is a native shell with the stored shell id, never tmux", async () => {
    state.platform = "windows";
    localStorage.setItem("sshler.windows_shell", "cmd");
    const wrapper = await mountView();
    await addTerminal(wrapper, "local");
    await addTerminal(wrapper, "build");

    const panes = wrapper.findAllComponents(Terminal);
    expect(panes.map((c) => [c.props("shell"), c.props("nativeShell")])).toEqual([["cmd", true], ["", undefined]]);
    wrapper.unmount();
  });

  it("with no stored choice asks the default shell, still native", async () => {
    state.platform = "windows";
    const wrapper = await mountView();
    await addTerminal(wrapper, "local");

    const pane = wrapper.findComponent(Terminal);
    expect([pane.props("shell"), pane.props("nativeShell")]).toEqual(["", true]);
    wrapper.unmount();
  });
});
