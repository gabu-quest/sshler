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
  // What the page wrote into xterm, as text, in order.
  const written: string[] = [];
  const decoder = new TextDecoder();
  class RecordingTerminal {
    constructor() {
      const base = make();
      return new Proxy(base, {
        get: (target, prop) =>
          prop === "write"
            ? (data: Uint8Array | string) => {
                written.push(typeof data === "string" ? data : decoder.decode(data));
              }
            : target[prop],
      });
    }
  }
  return { Stub, RecordingTerminal, written };
});

// xterm and its addons need a real canvas; replace them with a permissive
// stand-in where every property is a callable no-op that returns itself.
vi.mock("@xterm/xterm", () => ({ Terminal: stubs.RecordingTerminal }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: stubs.Stub }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: stubs.Stub }));
vi.mock("@xterm/addon-search", () => ({ SearchAddon: stubs.Stub }));
vi.mock("@xterm/addon-clipboard", () => ({ ClipboardAddon: stubs.Stub }));
vi.mock("@xterm/addon-webgl", () => ({ WebglAddon: stubs.Stub }));

const messageMock = { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock("naive-ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("naive-ui")>()),
  useMessage: () => messageMock,
}));

// The owner of "the session has ended" answers a 4401 close with its sign-in navigation
// (a promise) and any other close with null; the spec reads what the terminal asked it.
const snapshotMock = vi.hoisted(() => vi.fn(() => ({ last_snapshot_at: null })));
const signInMock = vi.hoisted(() =>
  vi.fn((code: number) => (code === 4401 ? Promise.resolve() : null)),
);
vi.mock("@/api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/http")>()),
  fetchSnapshotStatus: snapshotMock,
  signInAfterSocketClose: signInMock,
}));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number; reason?: string }) => void) | null = null;
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
  signInMock.mockClear();
  snapshotMock.mockClear();
  stubs.written.length = 0;
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

async function mountConnected(extra: Record<string, unknown> = {}) {
  const wrapper = mount(Terminal, {
    props: { boxName: "box1", sessionName: "main", directory: "/tmp", ...extra },
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
    [4400, "terminal.session_name_refused", 6000],
    [4403, "terminal.auth_failed", 5000],
    [4502, "terminal.wsl_not_installed", 6000],
    [4503, "terminal.pywinpty_missing", 8000],
    [4404, "terminal.unknown_box", 6000],
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

  // Mutation killed: a 4401 that toasts, reconnects, or never reaches the sign-in owner.
  it("code 4401 goes to the sign-in page: no toast, no reconnect", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 4401 });
    await advance(60_000);

    expect(signInMock.mock.calls).toEqual([[4401]]);
    expect(messageMock.error).toHaveBeenCalledTimes(0);
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

describe("Terminal.vue socket error", () => {
  it("a socket error on the first connection shows 'Connection failed' alone", async () => {
    const wrapper = await mountConnected();
    socket(0).onerror!(new Event("error"));
    await flushPromises();

    expect(messageMock.error.mock.calls).toEqual([["Connection failed"]]);
    wrapper.unmount();
  });
});

describe("Terminal.vue handshake", () => {
  // A refused handshake shows the failure's line and the server's detail.
  it("a refused handshake shows its reason's line and detail, and opens no socket", async () => {
    fetchMock.mockImplementationOnce(async () =>
      new Response(JSON.stringify({ detail: "build-box: connection timed out after 10 s", reason: "unreachable" }), { status: 502 }),
    );
    const wrapper = mount(Terminal, {
      props: { boxName: "box1", sessionName: "main", directory: "/tmp" },
      global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
    });
    await advance(100);

    expect(messageMock.error).toHaveBeenCalledTimes(1);
    expect(messageMock.error).toHaveBeenCalledWith(
      "Connection failed: Cannot connect to the box — build-box: connection timed out after 10 s",
    );
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/terminal/handshake");
    wrapper.unmount();
  });
});

const NOTICE = en["terminal.plain_fallback_notice"]!;
const NEW_SHELL = en["terminal.plain_reconnected_notice"]!;

function frame(ws: FakeWebSocket, body: unknown) {
  ws.onmessage!({ data: JSON.stringify(body) });
}

/** Let the write queue's animation frame run, then return everything written. */
async function written(): Promise<string> {
  await advance(50);
  return stubs.written.join("");
}

const toolbarGroups = (w: ReturnType<typeof mount>) => w.findAll(".toolbar-group").length;

describe("Terminal.vue shell frame", () => {
  it("a fallback frame writes the notice once, on its own line, before the shell's first output", async () => {
    const wrapper = await mountConnected();
    socket(0).onopen!();
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    socket(0).onmessage!({ data: Uint8Array.from([36, 32]).buffer });

    expect(await written()).toBe(NOTICE + "\r\n$ ");
    wrapper.unmount();
  });

  it("the frame is told to the parent once, as what the server opened", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    frame(socket(0), { op: "shell", kind: "zsh", fallback: false });

    expect(wrapper.emitted("shell")).toEqual([[{ kind: "plain", fallback: true }]]);
    wrapper.unmount();
  });

  it("a shell frame that comes twice on one socket prints the notice once", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });

    expect(await written()).toBe(NOTICE + "\r\n");
    wrapper.unmount();
  });

  it("a plain shell that was asked for, with no fallback, prints no notice", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it.each([
    ["an unknown kind", { op: "shell", kind: "zsh", fallback: true }],
    ["no fallback field", { op: "shell", kind: "plain" }],
    ["a non-boolean fallback", { op: "shell", kind: "plain", fallback: "yes" }],
    ["an unknown op", { op: "title", kind: "plain", fallback: true }],
  ])("%s: nothing is printed and the terminal goes on by what was asked", async (_name, body) => {
    const wrapper = await mountConnected();
    frame(socket(0), body);

    expect(await written()).toBe("");
    expect(toolbarGroups(wrapper)).toBe(3);
    wrapper.unmount();
  });

  it("a plain frame hides the three tmux toolbar groups", async () => {
    const wrapper = await mountConnected();
    expect(toolbarGroups(wrapper)).toBe(3);
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    await flushPromises();
    expect(toolbarGroups(wrapper)).toBe(0);
    wrapper.unmount();
  });

  it("a terminal asked for tmux by name keeps the tmux toolbar before any frame", async () => {
    const wrapper = await mountConnected({ shell: "tmux" });
    expect(toolbarGroups(wrapper)).toBe(3);
    wrapper.unmount();
  });

  it("a terminal asked to be plain asks for no snapshot status, ever", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    await advance(60_000);

    expect(snapshotMock).toHaveBeenCalledTimes(0);
    expect(toolbarGroups(wrapper)).toBe(0);
    wrapper.unmount();
  });

  it("a fallback stops the snapshot poll: no request after the frame", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    await advance(0);
    const before = snapshotMock.mock.calls.length;
    await advance(60_000);

    expect(snapshotMock.mock.calls.length).toBe(before);
    wrapper.unmount();
  });

  it("a reconnected plain shell says it is a new one", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    socket(0).onmessage!({ data: Uint8Array.from([36, 32]).buffer });
    await written();
    stubs.written.length = 0;
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("\r\n" + NEW_SHELL + "\r\n");
    wrapper.unmount();
  });

  it("an ordinary drop of an asked-for plain shell prints the line exactly once, on its own row", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    socket(0).onmessage!({ data: Uint8Array.from([36, 32]).buffer });
    await written();
    stubs.written.length = 0;
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("\r\n" + NEW_SHELL + "\r\n");
    wrapper.unmount();
  });

  it("a Windows native shell that reconnects is the same shell: no line", async () => {
    const wrapper = await mountConnected({ shell: "pwsh", nativeShell: true });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it("typing exit (close 1000) and clicking Reconnect prints no line", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    socket(0).onclose!({ code: 1000 });
    await advance(5000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await (wrapper.vm as unknown as { connect: () => Promise<void> }).connect();
    await flushPromises();
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it("an intentional disconnect (switching session) prints no line, even if its close arrives as a drop", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    (wrapper.vm as unknown as { switchSession: (n: string, d: string) => void }).switchSession("other", "/tmp");
    socket(0).onclose!({ code: 1006 });
    await advance(0);
    expect(FakeWebSocket.instances).toHaveLength(2);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it("a terminal that was asked for tmux and got a plain shell without fallback prints no line", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "plain", fallback: false });
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it("a first frame that cannot be read, a drop, then a valid plain frame: the line prints once", async () => {
    const wrapper = await mountConnected({ shell: "plain" });
    frame(socket(0), { op: "shell", kind: "zsh", fallback: false });
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: false });

    expect(await written()).toBe(NEW_SHELL + "\r\n");
    wrapper.unmount();
  });

  it("a reconnect that falls back again prints the fallback line alone", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "plain", fallback: true });
    await written();
    stubs.written.length = 0;
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "plain", fallback: true });

    expect(await written()).toBe("\r\n" + NOTICE + "\r\n");
    wrapper.unmount();
  });

  it("a reconnected tmux terminal prints nothing", async () => {
    const wrapper = await mountConnected();
    frame(socket(0), { op: "shell", kind: "tmux", fallback: false });
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    frame(socket(1), { op: "shell", kind: "tmux", fallback: false });

    expect(await written()).toBe("");
    wrapper.unmount();
  });

  it("a refused shell (4400, Invalid shell) toasts its own words and never reconnects", async () => {
    const wrapper = await mountConnected({ shell: "zsh" });
    socket(0).onclose!({ code: 4400, reason: "Invalid shell" });
    await advance(60_000);

    expect(messageMock.error.mock.calls).toEqual([[en["terminal.shell_refused"], { duration: 6000 }]]);
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });
});

describe("Terminal.vue tmux keys", () => {
  type Senders = Record<string, () => void>;
  const KEYS: [string, string][] = [
    ["tmuxKillWindow", "\x02&"],
    ["tmuxNewWindow", "\x02c"],
    ["tmuxPrevWindow", "\x02p"],
    ["tmuxNextWindow", "\x02n"],
    ["tmuxSplitVertical", "\x02%"],
    ["tmuxSplitHorizontal", "\x02\""],
    ["tmuxPaneLeft", "\x02\x1b[D"],
    ["tmuxPaneRight", "\x02\x1b[C"],
  ];
  const sentOn = (ws: FakeWebSocket) =>
    ws.send.mock.calls.map((c) => new TextDecoder().decode(c[0] as Uint8Array));

  it("a tmux terminal sends exactly each key's bytes", async () => {
    const wrapper = await mountConnected();
    socket(0).readyState = FakeWebSocket.OPEN;
    frame(socket(0), { op: "shell", kind: "tmux", fallback: false });
    for (const [name] of KEYS) (wrapper.vm as unknown as Senders)[name]!();

    expect(sentOn(socket(0))).toEqual(KEYS.map(([, bytes]) => bytes));
    wrapper.unmount();
  });

  it.each([
    ["asked plain", { shell: "plain" }, null],
    ["a Windows native shell", { shell: "pwsh", nativeShell: true }, null],
    ["a fallback", {}, { op: "shell", kind: "plain", fallback: true }],
  ])("%s: no key sends anything", async (_name, props, shellFrame) => {
    const wrapper = await mountConnected(props);
    socket(0).readyState = FakeWebSocket.OPEN;
    if (shellFrame) frame(socket(0), shellFrame);
    for (const [name] of KEYS) (wrapper.vm as unknown as Senders)[name]!();

    expect(socket(0).send).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
