import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProgressBar, ProgressEvent } from "@/api/types";
import { useProgressStore } from "./progress";
import { useAppStore } from "@/stores/app";

vi.mock("@/api/http", () => ({
  fetchProgress: vi.fn(),
  deleteProgress: vi.fn(),
}));

import { deleteProgress, fetchProgress } from "@/api/http";

function makeBar(name: string, overrides: Partial<ProgressBar> = {}): ProgressBar {
  const now = Date.now() / 1000;
  return {
    name,
    current: 5,
    total: 10,
    color: "blue",
    label: "lbl",
    status: "running",
    created_at: now - 10,
    updated_at: now,
    metadata: {},
    metadata_error: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Minimal in-test WebSocket stub: lets us assert what URL the store dialled,
// and fire events synchronously.
// ---------------------------------------------------------------------------
class MockWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState: number = MockWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  fireOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  fireMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  fireClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }

  close() {
    this.fireClose();
  }
}

const realWebSocket = globalThis.WebSocket;

describe("progress store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket;
    // app store reads window.matchMedia for theme detection at init; jsdom lacks it.
    if (!window.matchMedia) {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: (query: string) => ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }),
      });
    }
    vi.mocked(fetchProgress).mockReset();
    vi.mocked(deleteProgress).mockReset();
    // Subscriptions are scoped to the active box; default scope for tests.
    useAppStore().activeBox = "test-box";
  });

  afterEach(() => {
    (globalThis as any).WebSocket = realWebSocket;
    vi.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // Event reducer
  // -------------------------------------------------------------------------

  it("snapshot event replaces bars wholesale", () => {
    const store = useProgressStore();
    const a = makeBar("a");
    const b = makeBar("b");
    store._injectEventForTest({ type: "snapshot", bars: [a, b] });
    expect(Object.keys(store.bars)).toHaveLength(2);
    expect(store.bars["a"]!.current).toBe(5);
    expect(store.bars["b"]!.name).toBe("b");

    const c = makeBar("c");
    store._injectEventForTest({ type: "snapshot", bars: [c] });
    expect(Object.keys(store.bars)).toEqual(["c"]);
    expect(store.bars["a"]).toBeUndefined();
  });

  it("upsert event adds a new bar and later updates it in place", () => {
    const store = useProgressStore();
    store._injectEventForTest({ type: "upsert", name: "build", bar: makeBar("build", { current: 1 }) });
    expect(store.bars["build"]!.current).toBe(1);

    store._injectEventForTest({ type: "upsert", name: "build", bar: makeBar("build", { current: 9 }) });
    expect(store.bars["build"]!.current).toBe(9);
    expect(Object.keys(store.bars)).toHaveLength(1);
  });

  it("delete event removes the bar but leaves the subscription intact", () => {
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("ghost")] });
    store.subscribe("ghost");
    expect(store.isSubscribed("ghost")).toBe(true);

    store._injectEventForTest({ type: "delete", name: "ghost", bar: null });
    expect(store.bars["ghost"]).toBeUndefined();
    expect(store.isSubscribed("ghost")).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Subscription model
  // -------------------------------------------------------------------------

  it("subscribe/unsubscribe persists to localStorage under the active box scope", () => {
    const store = useProgressStore();
    store.subscribe("ci");
    store.subscribe("deploy");
    expect(store.isSubscribed("ci")).toBe(true);
    expect(JSON.parse(localStorage.getItem("sshler:progress:subscribed") || "{}")).toEqual({
      "test-box": ["ci", "deploy"],
    });

    store.unsubscribe("ci");
    expect(store.isSubscribed("ci")).toBe(false);
    expect(JSON.parse(localStorage.getItem("sshler:progress:subscribed") || "{}")).toEqual({
      "test-box": ["deploy"],
    });
  });

  it("subscribedBars filters by the subscription set", () => {
    const store = useProgressStore();
    store._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("a"), makeBar("b"), makeBar("c")],
    });
    store.subscribe("a");
    store.subscribe("c");

    expect(store.subscribedBars).toHaveLength(2);
    const names = store.subscribedBars.map((bar) => bar.name).sort();
    expect(names).toEqual(["a", "c"]);
  });

  it("hydrates per-scope subscriptions from localStorage on first use", () => {
    localStorage.setItem(
      "sshler:progress:subscribed",
      JSON.stringify({ "test-box": ["preloaded"], "other-box": ["nope"] }),
    );
    const store = useProgressStore();
    expect(store.isSubscribed("preloaded")).toBe(true);
    // The other box's subscription must not leak into the active scope.
    expect(store.isSubscribed("nope")).toBe(false);
  });

  it("discards the old flat-array localStorage shape on hydration", () => {
    localStorage.setItem("sshler:progress:subscribed", JSON.stringify(["legacy-global"]));
    const store = useProgressStore();
    expect(store.isSubscribed("legacy-global")).toBe(false);
    expect(store.subscriptionsByScope).toEqual({});
  });

  it("subscriptions are isolated per box scope", () => {
    const app = useAppStore();
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("a"), makeBar("b")] });

    app.activeBox = "sshler";
    store.subscribe("a");
    expect(store.subscribedBars.map((x) => x.name)).toEqual(["a"]);

    app.activeBox = "maintenance";
    expect(store.subscribedBars).toHaveLength(0);
    store.subscribe("b");
    expect(store.subscribedBars.map((x) => x.name)).toEqual(["b"]);

    app.activeBox = "sshler";
    expect(store.subscribedBars.map((x) => x.name)).toEqual(["a"]);
  });

  it("subscribe is a no-op when no box is active", () => {
    const app = useAppStore();
    const store = useProgressStore();
    app.activeBox = null;
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("orphan")] });
    store.subscribe("orphan");
    expect(store.isSubscribed("orphan")).toBe(false);
    expect(store.subscribedBars).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // isStale
  // -------------------------------------------------------------------------

  it("isStale flags bars older than the threshold", () => {
    const store = useProgressStore();
    const now = Date.now() / 1000;
    const fresh = makeBar("fresh", { updated_at: now });
    const stale = makeBar("stale", { updated_at: now - 600 });
    expect(store.isStale(fresh)).toBe(false);
    expect(store.isStale(stale)).toBe(true);
    expect(store.isStale(stale, 700)).toBe(false);
  });

  // -------------------------------------------------------------------------
  // refresh / remove (REST)
  // -------------------------------------------------------------------------

  it("refresh hydrates bars from listProgress", async () => {
    vi.mocked(fetchProgress).mockResolvedValue({
      bars: [makeBar("from-rest", { current: 7 })],
    });
    const store = useProgressStore();
    await store.refresh("token");
    expect(fetchProgress).toHaveBeenCalledWith("token");
    expect(store.bars["from-rest"]!.current).toBe(7);
  });

  it("remove deletes the bar locally when the server confirms removal", async () => {
    vi.mocked(deleteProgress).mockResolvedValue({ ok: true, removed: true });
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("doomed")] });
    expect(store.bars["doomed"]).toBeDefined();
    const removed = await store.remove("doomed", "token");
    expect(removed).toBe(true);
    expect(deleteProgress).toHaveBeenCalledWith("doomed", "token");
    expect(store.bars["doomed"]).toBeUndefined();
  });

  it("remove leaves local state alone when the server says no-op", async () => {
    vi.mocked(deleteProgress).mockResolvedValue({ ok: true, removed: false });
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("stays")] });
    const removed = await store.remove("stays", "token");
    expect(removed).toBe(false);
    expect(store.bars["stays"]).toBeDefined();
  });

  // -------------------------------------------------------------------------
  // WebSocket lifecycle
  // -------------------------------------------------------------------------

  it("connect opens a WS with token in query string", () => {
    const store = useProgressStore();
    store.connect("abc123");
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0]!.url).toBe(
      `ws://${location.host}/ws/progress?token=abc123`,
    );
    expect(store.connecting).toBe(true);
    expect(store.connected).toBe(false);
  });

  it("onopen flips connected = true", () => {
    const store = useProgressStore();
    store.connect("tok");
    MockWebSocket.instances[0]!.fireOpen();
    expect(store.connected).toBe(true);
    expect(store.connecting).toBe(false);
  });

  it("onmessage routes events through the reducer", () => {
    const store = useProgressStore();
    store.connect("tok");
    MockWebSocket.instances[0]!.fireOpen();
    MockWebSocket.instances[0]!.fireMessage({
      type: "upsert",
      name: "live",
      bar: makeBar("live", { current: 3 }),
    } satisfies ProgressEvent);
    expect(store.bars["live"]!.current).toBe(3);
  });

  it("schedules a reconnect after an unintentional close", () => {
    vi.useFakeTimers();
    const store = useProgressStore();
    store.connect("tok");
    MockWebSocket.instances[0]!.fireOpen();
    MockWebSocket.instances[0]!.fireClose();
    expect(store.connected).toBe(false);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1_000);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1]!.url).toContain("token=tok");
  });

  it("disconnect cancels reconnect and closes the socket", () => {
    vi.useFakeTimers();
    const store = useProgressStore();
    store.connect("tok");
    MockWebSocket.instances[0]!.fireOpen();
    MockWebSocket.instances[0]!.fireClose();
    store.disconnect();
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(store.connected).toBe(false);
  });

  // -------------------------------------------------------------------------
  // Done bars persist (no auto-dismiss — they stay until unsubscribe/delete)
  // -------------------------------------------------------------------------

  it("keeps a done bar in subscribedBars indefinitely", () => {
    vi.useFakeTimers();
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("ci")] });
    store.subscribe("ci");
    expect(store.subscribedBars).toHaveLength(1);
    expect(store.subscribedBars[0]!.name).toBe("ci");

    store._injectEventForTest({
      type: "upsert",
      name: "ci",
      bar: makeBar("ci", { status: "done", current: 10 }),
    });
    expect(store.subscribedBars).toHaveLength(1);
    expect(store.subscribedBars[0]!.status).toBe("done");

    // No auto-dismiss: the bar is still there long after the old 10s timeout.
    vi.advanceTimersByTime(60_000);
    expect(store.subscribedBars).toHaveLength(1);
    expect(store.subscribedBars[0]!.name).toBe("ci");
    expect(store.subscribedBars[0]!.status).toBe("done");
  });

  it("a done bar that goes back to running updates in place", () => {
    const store = useProgressStore();
    store._injectEventForTest({ type: "snapshot", bars: [makeBar("ci", { status: "done", current: 10 })] });
    store.subscribe("ci");
    expect(store.subscribedBars).toHaveLength(1);
    expect(store.subscribedBars[0]!.status).toBe("done");

    store._injectEventForTest({
      type: "upsert",
      name: "ci",
      bar: makeBar("ci", { status: "running", current: 1 }),
    });
    expect(store.subscribedBars).toHaveLength(1);
    expect(store.subscribedBars[0]!.status).toBe("running");
    expect(store.subscribedBars[0]!.current).toBe(1);
  });

  it("keeps failed and cancelled bars", () => {
    const store = useProgressStore();
    store._injectEventForTest({
      type: "snapshot",
      bars: [
        makeBar("boom", { status: "failed" }),
        makeBar("nope", { status: "cancelled" }),
      ],
    });
    store.subscribe("boom");
    store.subscribe("nope");

    expect(store.subscribedBars).toHaveLength(2);
    const statuses = store.subscribedBars.map((b) => b.status).sort();
    expect(statuses).toEqual(["cancelled", "failed"]);
  });

  it("delete removes the bar from subscribedBars", async () => {
    vi.mocked(deleteProgress).mockResolvedValue({ ok: true, removed: true });
    const store = useProgressStore();
    store._injectEventForTest({
      type: "snapshot",
      bars: [makeBar("ci", { status: "done" })],
    });
    store.subscribe("ci");
    expect(store.subscribedBars).toHaveLength(1);

    const removed = await store.remove("ci", "tok");
    expect(removed).toBe(true);
    expect(store.bars["ci"]).toBeUndefined();
    expect(store.subscribedBars).toHaveLength(0);
  });

});
