import { render } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRouter, createWebHistory } from "vue-router";

import { ApiError } from "@/api/apiError";
import AppHeader from "./AppHeader.vue";

vi.mock("@/api/http", () => ({
  boxStats: vi.fn(),
  fetchSnapshotStatus: vi.fn(async () => ({ last_snapshot_at: null })),
}));

import { boxStats } from "@/api/http";

vi.mock("naive-ui", () => {
  const stub = (template: string) => ({ template });
  return {
    NButton: stub("<button><slot /></button>"),
    NIcon: stub("<span><slot /></span>"),
    NSpace: stub("<div><slot /></div>"),
    NDrawer: stub("<div><slot /></div>"),
    NDrawerContent: stub("<div><slot /></div>"),
    NTooltip: stub("<span><slot name='trigger' /><slot /></span>"),
    NProgress: stub("<div />"),
    NSelect: stub("<select />"),
  };
});

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: "/", component: { template: "<div>home</div>" } }],
});

describe("AppHeader: the local box's stats", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("a failed poll hides the widget instead of keeping the old numbers", async () => {
    vi.mocked(boxStats)
      .mockResolvedValueOnce({
        name: "local", cpu_percent: 42, memory_used_mb: 1024, memory_total_mb: 4096, memory_percent: 25,
        uptime_seconds: 60,
      })
      .mockRejectedValueOnce(new ApiError(0, "network", ""));
    const { container } = render(AppHeader, {
      global: {
        plugins: [router],
        stubs: { CommandPalette: { template: "<span />" }, ShortcutsOverlay: { template: "<span />" } },
      },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelectorAll(".mobile-stats")).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(vi.mocked(boxStats)).toHaveBeenCalledTimes(2);
    expect(container.querySelectorAll(".mobile-stats")).toHaveLength(0);
  });
});
