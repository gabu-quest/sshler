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

    expect(getAllByText("Overview")[0]).toBeTruthy();
    expect(getAllByText("Boxes")[0]).toBeTruthy();
    expect(getAllByText("Files")[0]).toBeTruthy();
    expect(getAllByText("Terminal")[0]).toBeTruthy();
  });
});
