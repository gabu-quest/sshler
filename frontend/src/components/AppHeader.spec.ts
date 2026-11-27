import { fireEvent, render } from "@testing-library/vue";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { createRouter, createWebHistory, type Router } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

import { useAppStore } from "@/stores/app";
import AppHeader from "./AppHeader.vue";

vi.mock("@/api/http", () => ({
  boxStats: vi.fn(async () => {
    throw new Error("no stats in this test");
  }),
}));

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = []) => ({ props, template });
  return {
    NButton: stub("<button><slot /></button>"),
    NIcon: stub("<span><slot /></span>"),
    NDrawer: stub('<div v-if="show" class="stub-drawer" :data-placement="placement"><slot /></div>', ["show", "placement"]),
    NDrawerContent: stub("<div><slot /></div>"),
    NTooltip: stub("<span><slot name='trigger' /></span>"),
    // The flyout's content is rendered so its links can be read.
    NPopover: stub("<span><slot name='trigger' /><slot /></span>"),
  };
});

const ROUTE_PATHS = [
  "/", "/boxes", "/files", "/commander", "/terminal", "/multi-terminal", "/progress",
  "/artifacts", "/claude", "/diff", "/settings",
];

function makeRouter(): Router {
  return createRouter({
    history: createWebHistory(),
    routes: ROUTE_PATHS.map((path) => ({ path, component: { template: `<div>${path}</div>` } })),
  });
}

function setWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
}

async function mountHeader(path = "/", width = 1366) {
  setWidth(width);
  const router = makeRouter();
  await router.push(path);
  await router.isReady();
  const view = render(AppHeader, {
    global: {
      plugins: [router],
      stubs: {
        CommandPalette: { template: "<button>cmd</button>" },
        ShortcutsOverlay: { template: "<button>shortcuts</button>" },
        ProgressPicker: { props: ["show"], template: '<div v-if="show" data-testid="picker" />' },
      },
    },
  });
  await nextTick();
  return { ...view, router };
}

function hrefs(root: Element | null, selector: string): Array<[string, string | null]> {
  expect(root).not.toBeNull();
  return Array.from(root!.querySelectorAll<HTMLAnchorElement>(selector)).map((a) => [
    a.querySelector(".rail-label, .mobile-nav-label")?.textContent?.trim() ?? "",
    a.getAttribute("href"),
  ]);
}

function key(init: KeyboardEventInit, target: EventTarget = document.body): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

describe("AppHeader", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
    });
  });
  afterEach(() => {
    setWidth(1024);
    document.body.innerHTML = "";
  });

  describe("on a desktop window: the side rail", () => {
    it("is the only navigation: no top bar, the four daily links carry the active box", async () => {
      useAppStore().activeBox = "local";
      const { container } = await mountHeader("/");
      expect(container.querySelectorAll("aside.app-rail")).toHaveLength(1);
      expect(container.querySelectorAll(".app-header")).toHaveLength(0);
      // The tools flyout's links are drawn outside the nav on the page; here the popover
      // stub keeps them inside, so only the nav's own links are read.
      expect(hrefs(container.querySelector(".desktop-nav"), ":scope > a.rail-item")).toEqual([
        ["Overview", "/"],
        ["Files", "/files?box=local"],
        ["Terminal", "/terminal?box=local"],
        ["Multi-Terminal", "/multi-terminal?box=local"],
      ]);
    });

    it("holds the six tools in a flyout, and settings at the bottom", async () => {
      useAppStore().activeBox = "local";
      const { container, getByTestId } = await mountHeader("/");
      expect(hrefs(getByTestId("rail-tools-menu"), "a.rail-item")).toEqual([
        ["Commander", "/commander?box=local"],
        ["Boxes", "/boxes"],
        ["Claude", "/claude"],
        ["Diff", "/diff"],
        ["Progress", "/progress"],
        ["Artifacts", "/artifacts"],
      ]);
      expect(hrefs(container.querySelector(".rail-dock"), "a.rail-item")).toEqual([["Settings", "/settings"]]);
    });

    it("marks Tools active on a tool's page", async () => {
      const { getByTestId } = await mountHeader("/diff");
      expect(getByTestId("rail-tools").classList.contains("active")).toBe(true);
    });

    it("opens collapsed; its toggle expands it and says so", async () => {
      const { getByTestId, container } = await mountHeader("/");
      const toggle = getByTestId("rail-toggle");
      expect([toggle.getAttribute("aria-expanded"), toggle.getAttribute("aria-label")]).toEqual(["false", "Expand sidebar"]);
      await fireEvent.click(toggle);
      expect(useAppStore().railCollapsed).toBe(false);
      expect([toggle.getAttribute("aria-expanded"), toggle.getAttribute("aria-label")]).toEqual(["true", "Collapse sidebar"]);
      expect(container.querySelector("aside.app-rail")!.classList.contains("expanded")).toBe(true);
    });

    it("Alt+\\ toggles it, but not while a text field has the keys", async () => {
      await mountHeader("/");
      const app = useAppStore();
      const input = document.createElement("input");
      document.body.appendChild(input);
      input.focus();
      key({ key: "\\", code: "Backslash", altKey: true }, input);
      expect(app.railCollapsed).toBe(true);
      key({ key: "\\", code: "Backslash", altKey: true });
      expect(app.railCollapsed).toBe(false);
      key({ key: "\\", code: "Backslash", altKey: true });
      expect(app.railCollapsed).toBe(true);
    });

    it("Escape closes it when expanded and is left alone when collapsed", async () => {
      await mountHeader("/");
      const app = useAppStore();
      const ignored = key({ key: "Escape" });
      expect([app.railCollapsed, ignored.defaultPrevented]).toEqual([true, false]);
      app.toggleRailCollapsed();
      key({ key: "Escape" });
      expect(app.railCollapsed).toBe(true);
    });

    it("a click outside closes it; a click inside does not", async () => {
      const { container } = await mountHeader("/");
      const app = useAppStore();
      app.toggleRailCollapsed();
      await nextTick();
      await fireEvent.pointerDown(container.querySelector(".desktop-nav")!);
      expect(app.railCollapsed).toBe(false);
      const outside = document.createElement("div");
      document.body.appendChild(outside);
      await fireEvent.pointerDown(outside);
      expect(app.railCollapsed).toBe(true);
    });

    it("a pick closes it", async () => {
      const { container, router } = await mountHeader("/");
      const app = useAppStore();
      app.toggleRailCollapsed();
      await nextTick();
      const files = Array.from(container.querySelectorAll<HTMLAnchorElement>(".desktop-nav a")).find(
        (a) => a.getAttribute("href") === "/files",
      )!;
      await fireEvent.click(files);
      await new Promise((resolve) => setTimeout(resolve, 0));
      await nextTick();
      expect([router.currentRoute.value.path, app.railCollapsed]).toEqual(["/files", true]);
    });

    it("the progress button counts the subscribed bars and opens the picker", async () => {
      useAppStore().activeBox = "local";
      const { getByTestId, queryByTestId } = await mountHeader("/");
      const button = getByTestId("rail-progress");
      expect(button.getAttribute("aria-label")).toBe("Progress bars");
      expect(queryByTestId("rail-progress-count")).toBeNull();
      expect(queryByTestId("picker")).toBeNull();
      await fireEvent.click(button);
      expect(queryByTestId("picker")).not.toBeNull();
    });

    it("the theme button names the mode in the page's language", async () => {
      useAppStore().setColorMode("system");
      const { getByTestId, container } = await mountHeader("/");
      expect(getByTestId("rail-theme").getAttribute("title")).toBe("System (Light)");
      expect(container.textContent).not.toMatch(/system \(/);
    });

    it("a window exactly 768 px wide is a desktop", async () => {
      const { container } = await mountHeader("/files", 768);
      expect([container.querySelectorAll(".app-rail").length, container.querySelectorAll(".app-header").length]).toEqual([1, 0]);
    });
  });

  describe("on a phone (below 768 px)", () => {
    it("every view but the terminal has the 36 px bar whose menu button opens the drawer from the left", async () => {
      const { container, getByRole } = await mountHeader("/files", 767);
      expect(container.querySelectorAll(".app-rail")).toHaveLength(0);
      expect(container.querySelectorAll(".app-header.app-header--nav")).toHaveLength(1);
      expect(document.querySelector(".stub-drawer")).toBeNull();
      await fireEvent.click(getByRole("button", { name: "Open menu" }));
      expect(useAppStore().mobileNavOpen).toBe(true);
      const drawer = document.querySelector(".stub-drawer");
      expect(drawer?.getAttribute("data-placement")).toBe("left");
      expect(hrefs(drawer, "a.mobile-nav-link").map(([, href]) => href)).toEqual([
        "/", "/files", "/terminal", "/multi-terminal",
        "/commander", "/boxes", "/claude", "/diff", "/progress", "/artifacts",
        "/settings",
      ]);
    });

    it.each(["/multi-terminal", "/commander", "/"])("%s has the 36 px bar too", async (path) => {
      const { container } = await mountHeader(path, 767);
      expect(container.querySelectorAll(".app-header--nav .mobile-menu-btn")).toHaveLength(1);
    });

    it("the terminal keeps the 14 px bar, with no menu button", async () => {
      const { container } = await mountHeader("/terminal", 767);
      expect(container.querySelectorAll(".app-header")).toHaveLength(1);
      expect(container.querySelectorAll(".app-header--nav, .mobile-menu-btn")).toHaveLength(0);
    });

    it("a pick in the drawer closes it", async () => {
      const { router } = await mountHeader("/", 767);
      const app = useAppStore();
      app.mobileNavOpen = true;
      await nextTick();
      const files = Array.from(document.querySelectorAll<HTMLAnchorElement>(".stub-drawer a")).find(
        (a) => a.getAttribute("href") === "/files",
      )!;
      await fireEvent.click(files);
      await new Promise((resolve) => setTimeout(resolve, 0));
      await nextTick();
      expect([router.currentRoute.value.path, app.mobileNavOpen]).toEqual(["/files", false]);
    });
  });

  it("ignores Alt+F while an input is focused, but navigates on Alt+F elsewhere", async () => {
    const { router } = await mountHeader("/");

    // Alt+F while focus is inside an <input> must NOT navigate.
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    key({ key: "f", altKey: true }, input);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(router.currentRoute.value.path).toBe("/");

    // Alt+F outside a typing target DOES navigate; so does a tool's shortcut.
    document.body.removeChild(input);
    key({ key: "f", altKey: true });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(router.currentRoute.value.path).toBe("/files");
    key({ key: "d", altKey: true });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(router.currentRoute.value.path).toBe("/diff");
  });

  describe("expanded rail utility rows", () => {
    it("pressing the label of Command Palette or Keyboard Shortcuts presses the row's own button, once", async () => {
      const { container } = await mountHeader("/");
      useAppStore().toggleRailCollapsed();
      await nextTick();
      const rows = Array.from(container.querySelectorAll<HTMLElement>(".rail-utility"));
      expect(rows).toHaveLength(2);
      for (const row of rows) {
        const button = row.querySelector<HTMLButtonElement>(".rail-icon button")!;
        const onClick = vi.fn();
        button.addEventListener("click", onClick);
        await fireEvent.click(row.querySelector(".rail-label")!);
        expect(onClick).toHaveBeenCalledTimes(1);
        // A click on the button itself is not doubled by the row.
        await fireEvent.click(button);
        expect(onClick).toHaveBeenCalledTimes(2);
      }
    });
  });
});
