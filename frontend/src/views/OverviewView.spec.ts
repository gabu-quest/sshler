import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { boxStats, fetchBoxes, searchDirectories, syncBoxSessions } from "@/api/http";
import OverviewView from "./OverviewView.vue";

const push = vi.hoisted(() => vi.fn());
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
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
  searchDirectories: vi.fn(),
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

describe("OverviewView: the search field of a box card", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    FakeStream.last = null;
    vi.stubGlobal("EventSource", FakeStream);
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([
      { id: "1", box: "build-box", session_name: "foo", working_directory: "/h/a/foo", created_at: 1, last_accessed_at: 1, active: true, window_count: 1, metadata: {} },
    ] as never);
    vi.mocked(searchDirectories).mockResolvedValue({
      box: "build-box", query: "foo", results: [{ path: "/h/x/foo-two", score: 1, source: "discovery" }],
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function typeFoo(container: Element) {
    await waitFor(() => expect(container.querySelector(".server-search input")).not.toBeNull());
    const input = container.querySelector(".server-search input") as HTMLInputElement;
    await fireEvent.update(input, "foo");
    await waitFor(() => expect(container.querySelectorAll(".server-search .search-result")).toHaveLength(2));
    return Array.from(container.querySelectorAll<HTMLElement>(".server-search .search-result"));
  }

  it("every card has exactly one field", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
      { name: "other-box", host: "10.0.0.6", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelectorAll(".server-card")).toHaveLength(2));
    const perCard = Array.from(container.querySelectorAll(".server-card")).map(
      (card) => card.querySelectorAll(".server-search").length,
    );
    expect(perCard).toEqual([1, 1]);
    expect(container.querySelectorAll(".server-search")).toHaveLength(2);
  });

  it("the box's session and folder show in one list", async () => {
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelectorAll(".server-search")).toHaveLength(1));
    const rows = await typeFoo(container);
    expect(rows.map((row) => row.getAttribute("data-kind"))).toEqual(["session", "folder"]);
    expect(searchDirectories).toHaveBeenCalledTimes(1);
  });

  it("a box named a&b and a session named x#y arrive whole in the route", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "a&b", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([
      { id: "1", box: "a&b", session_name: "x#y", working_directory: "/h/x#y", created_at: 1, last_accessed_at: 1, active: true, window_count: 1, metadata: {} },
    ] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelector(".server-search input")).not.toBeNull());
    await fireEvent.update(container.querySelector(".server-search input")!, "x#y");
    await waitFor(() => expect(container.querySelectorAll(".server-search .search-result").length).toBeGreaterThan(0));
    await fireEvent.mouseDown(container.querySelector(".server-search .search-result[data-kind='session']")!);
    expect(push.mock.calls).toEqual([["/terminal?box=a%26b&session=x%23y"]]);
  });

  it("a session row goes to the terminal of the session", async () => {
    const { container } = render(OverviewView);
    const rows = await typeFoo(container);
    await fireEvent.mouseDown(rows[0]!);
    expect(push.mock.calls).toEqual([["/terminal?box=build-box&session=foo"]]);
  });

  it("a folder row goes to the terminal in that folder, the folder encoded", async () => {
    const { container } = render(OverviewView);
    const rows = await typeFoo(container);
    await fireEvent.mouseDown(rows[1]!);
    expect(push.mock.calls).toEqual([["/terminal?box=build-box&dir=%2Fh%2Fx%2Ffoo-two"]]);
  });
});
