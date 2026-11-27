import { render } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRouter, createWebHistory } from "vue-router";

import { ApiError } from "@/api/apiError";
import { useBootstrapStore } from "@/stores/bootstrap";
import AppHeader from "./AppHeader.vue";

vi.mock("@/api/http", () => ({
  boxStats: vi.fn(),
  fetchSnapshotStatus: vi.fn(async () => ({ last_snapshot_at: null })),
}));

import { boxStats, fetchSnapshotStatus } from "@/api/http";

vi.mock("naive-ui", () => {
  const stub = (template: string) => ({ template });
  return {
    NButton: stub("<button><slot /></button>"),
    NIcon: stub("<span><slot /></span>"),
    NDrawer: stub("<div><slot /></div>"),
    NDrawerContent: stub("<div><slot /></div>"),
    NTooltip: stub("<span><slot name='trigger' /><slot /></span>"),
    NPopover: stub("<span><slot name='trigger' /><slot /></span>"),
  };
});

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: "/", component: { template: "<div>home</div>" } }],
});

function signIn(authRequired: boolean, authenticated: boolean) {
  useBootstrapStore().payload = {
    version: "1", token_header: "X-SSHLER-TOKEN", token: null, auth_required: authRequired, authenticated,
    allow_origins: [], spa_base: "/app/", spa_enabled: true, platform: "posix",
    default_shell: null, windows_shells: [],
  } as unknown as ReturnType<typeof useBootstrapStore>["payload"];
}

function mountHeader() {
  return render(AppHeader, {
    global: {
      plugins: [router],
      stubs: { CommandPalette: { template: "<span />" }, ShortcutsOverlay: { template: "<span />" }, ProgressPicker: { template: "<span />" } },
    },
  });
}

describe("AppHeader: the local box's stats", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.mocked(boxStats).mockReset();
    vi.mocked(fetchSnapshotStatus).mockClear();
    signIn(false, true);
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
        stubs: { CommandPalette: { template: "<span />" }, ShortcutsOverlay: { template: "<span />" }, ProgressPicker: { template: "<span />" } },
      },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelectorAll('[data-testid="local-stats"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="local-stats"]')?.textContent).toMatch(/42/);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(vi.mocked(boxStats)).toHaveBeenCalledTimes(2);
    expect(container.querySelectorAll('[data-testid="local-stats"]')).toHaveLength(0);
  });

  it("signed out: neither poll is asked", async () => {
    signIn(true, false);
    mountHeader();
    await vi.advanceTimersByTimeAsync(10_000);
    expect([vi.mocked(boxStats).mock.calls.length, vi.mocked(fetchSnapshotStatus).mock.calls.length]).toEqual([0, 0]);
  });

  it("signed in with a password set: the stats are asked once at the start, the snapshot status never", async () => {
    signIn(true, true);
    vi.mocked(boxStats).mockResolvedValue({
      name: "local", cpu_percent: 1, memory_used_mb: 1, memory_total_mb: 2, memory_percent: 50, uptime_seconds: 1,
    });
    mountHeader();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(10_000);
    expect([vi.mocked(boxStats).mock.calls.length, vi.mocked(fetchSnapshotStatus).mock.calls.length]).toEqual([2, 0]);
  });
});
