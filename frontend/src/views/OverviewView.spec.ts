import { fireEvent, render, waitFor } from "@testing-library/vue";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { boxStats, fetchBoxes, recreateSession, searchDirectories, syncBoxSessions } from "@/api/http";
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
  recreateSession: vi.fn(),
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

describe("OverviewView: Restore layout on the box card's session list", () => {
  const row = {
    id: "1", box: "build-box", session_name: "foo", working_directory: "/h/a/foo",
    created_at: 1, last_accessed_at: 1, active: true, window_count: 1, metadata: {}, restorable: false,
  };
  const ended = { ...row, id: "2", session_name: "web", active: false, restorable: true };
  const bare = { ...row, id: "3", session_name: "old", active: false, restorable: false };

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    FakeStream.last = null;
    vi.stubGlobal("EventSource", FakeStream);
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "10.0.0.5", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function openList(container: Element): Promise<void> {
    await waitFor(() => expect(container.querySelector(".session-chip")).not.toBeNull());
  }

  function restoreRows(): string[] {
    return Array.from(document.querySelectorAll(".session-chip"))
      .filter((item) => item.querySelector(".restore-layout"))
      .map((item) => item.querySelector(".session-name")!.textContent ?? "");
  }

  it("only an ended row whose layout is kept offers Restore layout", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([row, ended, bare] as never);
    const { container } = render(OverviewView);
    await openList(container);

    expect(restoreRows()).toEqual(["web"]);
    expect(document.querySelector(".session-chip .restore-layout")!.textContent!.trim()).toBe("Restore layout");
  });

  it("one press recreates the session by the recovery route, then the row is running", async () => {
    vi.mocked(syncBoxSessions)
      .mockResolvedValueOnce([ended] as never)
      .mockResolvedValue([{ ...ended, active: true, restorable: false }] as never);
    vi.mocked(recreateSession).mockResolvedValue({ status: "ok", message: "Recreated web" } as never);
    const { container } = render(OverviewView);
    await openList(container);
    await fireEvent.click(document.querySelector(".session-chip .restore-layout")!);

    await waitFor(() => expect(restoreRows()).toEqual([]));
    expect(vi.mocked(recreateSession).mock.calls).toEqual([["2", "tok"]]);
    expect(push).not.toHaveBeenCalled();
    expect(document.querySelector(".session-chip")!.classList.contains("inactive")).toBe(false);
  });

  it("a failed restore shows its message on the row and keeps the entry", async () => {
    vi.mocked(syncBoxSessions).mockResolvedValue([ended] as never);
    vi.mocked(recreateSession).mockRejectedValue(new ApiError(404, "not_found", "Session has no saved layout"));
    const { container } = render(OverviewView);
    await openList(container);
    await fireEvent.click(document.querySelector(".session-chip .restore-layout")!);

    await waitFor(() =>
      expect(document.querySelector(".session-chip .restore-failure")?.textContent?.trim()).toBe(
        "Not found — Session has no saved layout",
      ),
    );
    expect(restoreRows()).toEqual(["web"]);
  });
});

describe("OverviewView: the box card's links and session chips", () => {
  const mk = (i: number) => ({
    id: String(i), box: "build-box", session_name: `s${i}`, working_directory: `/h/s${i}`,
    created_at: i, last_accessed_at: i, active: true, window_count: 1, metadata: {}, restorable: false,
  });

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    FakeStream.last = null;
    vi.stubGlobal("EventSource", FakeStream);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ["my box", "my%20box"],
    ["a%20b", "a%2520b"],
  ])("a box named %s: Files, Multi and Terminal carry the encoded name", async (name, encoded) => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name, host: "h", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelector("[data-testid='box-files']")).not.toBeNull());
    await fireEvent.click(container.querySelector("[data-testid='box-files']")!);
    await fireEvent.click(container.querySelector("[data-testid='box-multi']")!);
    await fireEvent.click(container.querySelector("[data-testid='box-terminal']")!);
    expect(push.mock.calls).toEqual([
      [`/files?box=${encoded}`],
      [`/multi-terminal?box=${encoded}`],
      [`/terminal?box=${encoded}`],
    ]);
  });

  it("twelve sessions show four chips, then all of them, then four again", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "h", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue(Array.from({ length: 12 }, (_, i) => mk(i + 1)) as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelectorAll(".session-chip")).toHaveLength(4));
    const more = container.querySelector("[data-testid='sessions-toggle']")!;
    expect(more.textContent!.trim()).toBe("+8 more");
    await fireEvent.click(more);
    await waitFor(() => expect(container.querySelectorAll(".session-chip")).toHaveLength(12));
    expect(container.querySelector("[data-testid='sessions-toggle']")!.textContent!.trim()).toBe("Show less");
    await fireEvent.click(container.querySelector("[data-testid='sessions-toggle']")!);
    await waitFor(() => expect(container.querySelectorAll(".session-chip")).toHaveLength(4));
  });

  it("a chip opens the terminal of its session on a tap", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "h", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([mk(1)] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelector(".session-chip .session-open")).not.toBeNull());
    await fireEvent.click(container.querySelector(".session-chip .session-open")!);
    expect(push.mock.calls).toEqual([["/terminal?box=build-box&session=s1"]]);
  });

  it("a favourite on a box with a space has encoded links and a settings button that is not hover-only", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "my box", host: "h", transport: "ssh", favorites: ["/h/proj one"], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelectorAll(".favorite-tile")).toHaveLength(1));
    const hrefs = Array.from(container.querySelectorAll(".favorite-tile a")).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/app/terminal?box=my%20box&dir=%2Fh%2Fproj%20one",
      "/app/files?box=my%20box&path=%2Fh%2Fproj%20one",
    ]);
    expect(container.querySelector(".favorite-tile .fav-settings-btn")).not.toBeNull();
  });

  it("with no favourites there is no favourites section", async () => {
    vi.mocked(fetchBoxes).mockResolvedValue([
      { name: "build-box", host: "h", transport: "ssh", favorites: [], pinned: false, default_dir: "/" },
    ] as never);
    vi.mocked(syncBoxSessions).mockResolvedValue([] as never);
    const { container } = render(OverviewView);
    await waitFor(() => expect(container.querySelectorAll(".server-card")).toHaveLength(1));
    expect(container.querySelectorAll(".favorite-tile")).toHaveLength(0);
  });
});
