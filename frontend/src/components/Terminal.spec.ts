import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { en } from "@/locales/en";

// Imported statically (vi.mock calls are hoisted above it) so the slow first
// transform of Terminal.vue happens at collection, not inside a timed test.
// A dynamic import in the first test's body took ~2s, ran past the 5s test
// timeout under load, and the orphaned body then mounted a second Terminal
// on the next test's fake clock: two sockets where one is expected.
import Terminal from "./Terminal.vue";

const stubs = vi.hoisted(() => {
  const make = (): any =>
    new Proxy(function () {}, {
      get: (_t, prop) => (prop === "then" ? undefined : prop === "cols" ? 120 : prop === "rows" ? 32 : make()),
      apply: () => make(),
      set: () => true,
    });
  class Stub {
    constructor() {
      return make();
    }
  }
  return { Stub };
});

// xterm and its addons need a real canvas; replace them with a permissive
// stand-in where every property is a callable no-op that returns itself.
vi.mock("@xterm/xterm", () => ({ Terminal: stubs.Stub }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: stubs.Stub }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: stubs.Stub }));
vi.mock("@xterm/addon-search", () => ({ SearchAddon: stubs.Stub }));
vi.mock("@xterm/addon-clipboard", () => ({ ClipboardAddon: stubs.Stub }));
vi.mock("@xterm/addon-webgl", () => ({ WebglAddon: stubs.Stub }));
vi.mock("@xterm/addon-canvas", () => ({ CanvasAddon: stubs.Stub }));

const messageMock = { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock("naive-ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("naive-ui")>()),
  useMessage: () => messageMock,
}));

vi.mock("@/api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/http")>()),
  fetchSnapshotStatus: vi.fn(() => ({ last_snapshot_at: null })),
}));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
}

/** The i-th socket the component dialled; throws (failing the test) if it never did. */
function socket(i: number): FakeWebSocket {
  const ws = FakeWebSocket.instances[i];
  if (!ws) throw new Error(`no WebSocket #${i} was opened`);
  return ws;
}

const fetchMock = vi.fn(async (url: string) => {
  if (url === "/api/v1/terminal/handshake") {
    return new Response(JSON.stringify({ ws_url: "ws://127.0.0.1:8822/ws/term" }), { status: 200 });
  }
  return new Response("{}", { status: 200 });
});

beforeEach(async () => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  FakeWebSocket.instances = [];
  Object.values(messageMock).forEach((m) => m.mockClear());
  fetchMock.mockClear();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn() }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("Notification", { permission: "denied", requestPermission: vi.fn() });
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function mountConnected() {
  const wrapper = mount(Terminal, {
    props: { boxName: "box1", sessionName: "main", directory: "/tmp" },
    global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
  });
  await vi.advanceTimersByTimeAsync(100); // the onMounted auto-connect delay
  await flushPromises();
  expect(FakeWebSocket.instances).toHaveLength(1);
  return wrapper;
}

/** Advance fake time and let the handshake fetch in connect() settle. */
async function advance(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
  await flushPromises();
}

describe("Terminal.vue close codes", () => {
  // Mutation killed for each row: deleting that code's branch in onclose (the
  // close then falls through to scheduleReconnect), or changing its toast.
  it.each([
    [4403, "terminal.auth_failed", 5000],
    [4401, "terminal.auth_denied", 5000],
    [4502, "terminal.wsl_not_installed", 6000],
    [4503, "terminal.pywinpty_missing", 8000],
  ])("code %i shows its toast and never reconnects", async (code, key, duration) => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code });
    await advance(60_000);

    expect(messageMock.error).toHaveBeenCalledTimes(1);
    expect(messageMock.error).toHaveBeenCalledWith(en[key], { duration });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(wrapper.text()).not.toContain(en["terminal.reconnecting_to"]!.replace("{box}", "box1"));
    wrapper.unmount();
  });

  // Mutation killed: removing the 1000 branch (normal close would reconnect).
  it("code 1000 closes silently with no reconnect", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 1000 });
    await advance(60_000);

    expect(messageMock.error).toHaveBeenCalledTimes(0);
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });

  // Mutation killed: changing the backoff base (1000ms) or not scheduling a
  // reconnect for an unexpected close.
  it("an unexpected close (1006) reconnects after exactly 1000ms, then 2000ms", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 1006 });
    await flushPromises();
    expect(wrapper.text()).toContain(en["terminal.reconnecting_to"]!.replace("{box}", "box1"));

    await advance(999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await advance(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(socket(1).url).toBe(
      "ws://127.0.0.1:8822/ws/term?host=box1&dir=%2Ftmp&session=main&cols=120&rows=32",
    );

    // Second failure before open: attempt 1 -> 2000ms delay.
    socket(1).onclose!({ code: 1006 });
    await advance(1999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    await advance(1);
    expect(FakeWebSocket.instances).toHaveLength(3);
    expect(messageMock.error).toHaveBeenCalledTimes(0);
    wrapper.unmount();
  });

  // Mutation killed: dropping the reconnectAttempts reset in onopen (the next
  // drop after a successful reconnect would wait 2000ms instead of 1000ms).
  it("a successful reopen resets the backoff to 1000ms and toasts 'reconnected'", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    socket(1).onopen!();
    await flushPromises();
    expect(messageMock.success).toHaveBeenCalledWith(
      en["terminal.reconnected_to"]!.replace("{box}", "box1"),
      { duration: 3000, closable: false },
    );

    socket(1).onclose!({ code: 1006 });
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(3);
    wrapper.unmount();
  });

  // Mutation killed: removing the maxReconnectAttempts check.
  it("stops after 10 attempts with the max-reconnect toast", async () => {
    const wrapper = await mountConnected();
    for (let i = 0; i < 10; i++) {
      socket(i).onclose!({ code: 1006 });
      await advance(30_000);
    }
    expect(FakeWebSocket.instances).toHaveLength(11);
    socket(10).onclose!({ code: 1006 });
    await advance(60_000);

    expect(FakeWebSocket.instances).toHaveLength(11);
    expect(messageMock.error).toHaveBeenCalledTimes(1);
    expect(messageMock.error).toHaveBeenCalledWith(en["terminal.max_reconnect"], { duration: 5000 });
    wrapper.unmount();
  });
});
