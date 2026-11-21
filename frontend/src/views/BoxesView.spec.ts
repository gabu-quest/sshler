import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import BoxesView from "./BoxesView.vue";

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/api/http", () => ({
  fetchBootstrap: vi.fn(async () => ({
    token: "tok", token_header: "X-SSHLER-TOKEN", version: "test",
    spa_base: "/app/", spa_enabled: true, auth_required: false,
    wsl_distro: null, pdf_available: false, platform: "posix",
    windows_shells: [], default_shell: null,
  })),
  fetchBoxes: vi.fn(async () => [
    { name: "build-box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
  ]),
  fetchBox: vi.fn(),
  toggleFavorite: vi.fn(),
  togglePin: vi.fn(),
  boxStatus: vi.fn(),
}));

import { boxStatus } from "@/api/http";

vi.mock("naive-ui", () => {
  const stub = (template: string, props: string[] = [], emits: string[] = []) => ({ props, emits, template });
  return {
    NAlert: stub('<div class="stub-alert"><slot /></div>'),
    NButton: stub('<button class="stub-button" @click="$emit(\'click\')"><slot /></button>', [], ["click"]),
    NCard: stub('<div class="stub-card"><slot /></div>'),
    NGrid: stub("<div><slot /></div>"),
    NGridItem: stub("<div><slot /></div>"),
    NIcon: stub("<span />"),
    NSpace: stub("<div><slot /></div>"),
    useMessage: () => messageSpy,
  };
});

async function clickStatus() {
  const utils = render(BoxesView, { global: { mocks: { $router: { push: vi.fn() } } } });
  const button = await waitFor(() => {
    const found = Array.from(utils.container.querySelectorAll(".stub-button")).find((b) => b.textContent?.trim() === "status");
    expect(found).toBeTruthy();
    return found!;
  });
  await fireEvent.click(button);
  await waitFor(() => expect(vi.mocked(boxStatus)).toHaveBeenCalledTimes(1));
  await new Promise((r) => setTimeout(r, 0));
}

describe("BoxesView: the status button", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("an offline box shows why as an error", async () => {
    vi.mocked(boxStatus).mockResolvedValue({
      name: "build-box", status: "offline", latency_ms: null, error: "build-box did not answer", reason: "unreachable",
    });
    await clickStatus();
    expect(messageSpy.error.mock.calls).toEqual([["Cannot connect to the box — build-box did not answer"]]);
    expect(messageSpy.info).toHaveBeenCalledTimes(0);
  });

  it("a failed status call shows the failure's message as an error", async () => {
    vi.mocked(boxStatus).mockRejectedValue(new ApiError(502, "unreachable", "build-box: connection refused"));
    await clickStatus();
    expect(messageSpy.error.mock.calls).toEqual([["Cannot connect to the box — build-box: connection refused"]]);
    expect(messageSpy.info).toHaveBeenCalledTimes(0);
  });
});
