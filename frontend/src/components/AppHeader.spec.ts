import { render } from "@testing-library/vue";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { createRouter, createWebHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";

import AppHeader from "./AppHeader.vue";

vi.mock("naive-ui", () => {
  const stub = (template: string) => ({ template });
  return {
    NButton: stub("<button><slot /></button>"),
    NIcon: stub("<span><slot /></span>"),
    NSpace: stub("<div><slot /></div>"),
    NDrawer: stub("<div><slot /></div>"),
    NDrawerContent: stub("<div><slot /></div>"),
    NTooltip: stub("<span><slot /></span>"),
    NProgress: stub("<div />"),
    NSelect: stub("<select />"),
  };
});

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: { template: "<div>home</div>" } },
    { path: "/boxes", component: { template: "<div>boxes</div>" } },
    { path: "/files", component: { template: "<div>files</div>" } },
    { path: "/terminal", component: { template: "<div>terminal</div>" } },
    { path: "/artifacts", component: { template: "<div>artifacts</div>" } },
    { path: "/settings", component: { template: "<div>settings</div>" } },
  ],
});

describe("AppHeader", () => {
  beforeEach(() => {
    // Stub matchMedia for theme detection
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: () => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    });
  });

  it("renders navigation links", async () => {
    setActivePinia(createPinia());
    const { getAllByText } = render(AppHeader, {
      global: {
        plugins: [router],
        stubs: {
          NButton: { template: "<button><slot /></button>" },
          NIcon: { template: "<span><slot /></span>" },
          NSpace: { template: "<div><slot /></div>" },
          NDrawer: { template: "<div><slot /></div>" },
          NDrawerContent: { template: "<div><slot /></div>" },
          CommandPalette: { template: "<button>cmd</button>" },
          ShortcutsOverlay: { template: "<button>shortcuts</button>" },
        },
      },
    });

    // Each label renders in the desktop nav and again in the mobile drawer (the
    // stubbed NDrawer always renders its slot), so exactly two links per label,
    // both pointing at the route the nav item names.
    // Mutation killed: a nav item removed, relabelled, or pointed at another route.
    const expected: Array<[string, string]> = [
      ["Overview", "/"],
      ["Boxes", "/boxes"],
      ["Files", "/files"],
      ["Terminal", "/terminal"],
      ["Artifacts", "/artifacts"],
    ];
    for (const [label, href] of expected) {
      const links = getAllByText(label).map((el) => el.closest("a")?.getAttribute("href"));
      expect(links, label).toEqual([href, href]);
    }
  });

  it("ignores Alt+F while an input is focused, but navigates on Alt+F elsewhere", async () => {
    setActivePinia(createPinia());
    const typingRouter = createRouter({
      history: createWebHistory(),
      routes: [
        { path: "/", component: { template: "<div>home</div>" } },
        { path: "/boxes", component: { template: "<div>boxes</div>" } },
        { path: "/files", component: { template: "<div>files</div>" } },
        { path: "/terminal", component: { template: "<div>terminal</div>" } },
        { path: "/artifacts", component: { template: "<div>artifacts</div>" } },
        { path: "/settings", component: { template: "<div>settings</div>" } },
      ],
    });
    await typingRouter.push("/");
    await typingRouter.isReady();

    render(AppHeader, {
      global: {
        plugins: [typingRouter],
        stubs: {
          NButton: { template: "<button><slot /></button>" },
          NIcon: { template: "<span><slot /></span>" },
          NSpace: { template: "<div><slot /></div>" },
          NDrawer: { template: "<div><slot /></div>" },
          NDrawerContent: { template: "<div><slot /></div>" },
          CommandPalette: { template: "<button>cmd</button>" },
          ShortcutsOverlay: { template: "<button>shortcuts</button>" },
        },
      },
    });

    // Alt+F while focus is inside an <input> must NOT navigate.
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "f", altKey: true, bubbles: true, cancelable: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(typingRouter.currentRoute.value.path).toBe("/");

    // Alt+F outside a typing target DOES navigate.
    document.body.removeChild(input);
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "f", altKey: true, bubbles: true, cancelable: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(typingRouter.currentRoute.value.path).toBe("/files");
  });
});
