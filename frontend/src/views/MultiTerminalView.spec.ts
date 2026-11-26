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
