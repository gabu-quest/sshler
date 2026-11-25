import { render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { boxStats } from "@/api/http";
import OverviewView from "./OverviewView.vue";

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
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
  boxStats: vi.fn(),
  statsStreamUrl: vi.fn(() => "/api/v1/boxes/stats/stream"),
  fetchBoxSessions: vi.fn(async () => []),
  syncBoxSessions: vi.fn(async () => []),
}));

/** The stats stream: the test sends its events and its failure. */
class FakeStream {
  static last: FakeStream | null = null;
  listeners: Record<string, (event: MessageEvent) => void> = {};
  onerror: (() => void) | null = null;
  constructor() {
    FakeStream.last = this;
  }
  addEventListener(name: string, listener: (event: MessageEvent) => void) {
    this.listeners[name] = listener;
  }
  close() {}
  send(data: unknown) {
    this.listeners.stats?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

function cardText(container: Element): string[] {
  return Array.from(container.querySelectorAll(".server-stats-loading")).map((el) => el.textContent?.trim() ?? "");
}

describe("OverviewView: a box whose stats failed", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    FakeStream.last = null;
    vi.stubGlobal("EventSource", FakeStream);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a stats event with a failure shows its text on the card in place of the numbers", async () => {
    const { container } = render(OverviewView);
    await waitFor(() => expect(FakeStream.last).not.toBeNull());
    FakeStream.last!.send({ name: "build-box", error: "build-box did not answer", reason: "unreachable" });
    await waitFor(() => expect(cardText(container)).toEqual(["Cannot connect to the box — build-box did not answer"]));
    expect(container.querySelectorAll(".server-stats")).toHaveLength(0);
  });

  it("a reason the page does not know shows the no-reason line and the text, never a key", async () => {
    const { container } = render(OverviewView);
    await waitFor(() => expect(FakeStream.last).not.toBeNull());
    FakeStream.last!.send({ name: "build-box", error: "odd", reason: "exploded" });
    await waitFor(() => expect(cardText(container)).toEqual(["The request failed — odd"]));
  });

  describe("when the stats stream fails", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    async function mountAndFail(times: number) {
      const utils = render(OverviewView);
      await waitFor(() => expect(FakeStream.last).not.toBeNull());
      const stream = FakeStream.last!;
      for (let i = 0; i < times; i += 1) stream.onerror!();
      return utils;
    }

    it("one stats request per box is sent", async () => {
      vi.mocked(boxStats).mockResolvedValue({ name: "build-box" } as never);
      await mountAndFail(1);
      await waitFor(() => expect(boxStats).toHaveBeenCalledTimes(1));
      expect(vi.mocked(boxStats).mock.calls[0]![0]).toBe("build-box");
    });

    it("a poll that fails shows its message on the box's card in place of the numbers", async () => {
      vi.mocked(boxStats).mockRejectedValue(new ApiError(0, "network", ""));
      const { container } = await mountAndFail(1);
      await waitFor(() => expect(cardText(container)).toEqual(["No answer from sshler; check the connection"]));
      expect(container.querySelectorAll(".server-stats")).toHaveLength(0);
    });

    it("two stream errors leave one timer: 30 s later one more request per box", async () => {
      vi.mocked(boxStats).mockResolvedValue({ name: "build-box" } as never);
      await mountAndFail(2);
      await waitFor(() => expect(boxStats).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(30_000);
      expect(boxStats).toHaveBeenCalledTimes(2);
    });
  });
});
