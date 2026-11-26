import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { en } from "@/locales/en";
import { useAppStore } from "@/stores/app";

// Imported statically (vi.mock calls are hoisted above it) so the slow first
// transform of Terminal.vue happens at collection, not inside a timed test.
// A dynamic import in the first test's body took ~2s, ran past the 5s test
// timeout under load, and the orphaned body then mounted a second Terminal
// on the next test's fake clock: two sockets where one is expected.
import Terminal from "./Terminal.vue";
import { PhList } from "@phosphor-icons/vue";
import { NModal } from "naive-ui";

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
  const cleared = { count: 0 };
  // How many times the page asked xterm for the keyboard.
  const focused = { count: 0 };
  const decoder = new TextDecoder();
  // The options the page gave xterm: its OSC 8 `linkHandler` is how a file link is clicked.
  // The key handler the page gave xterm: a key pressed inside the terminal goes through it.
  const captured: { options: any; keyHandler: ((e: KeyboardEvent) => boolean) | null } = {
    options: null,
    keyHandler: null,
  };
  class RecordingTerminal {
    constructor(options?: unknown) {
      captured.options = options;
      const base = make();
      return new Proxy(base, {
        get: (target, prop) =>
          prop === "clear"
            ? () => { cleared.count++; }
            : prop === "focus"
            ? () => { focused.count++; }
            : prop === "buffer"
            ? { active: { length: 1, getLine: () => ({ translateToString: () => "hello" }) } }
            : prop === "write"
            ? (data: Uint8Array | string) => {
                written.push(typeof data === "string" ? data : decoder.decode(data));
              }
            : prop === "attachCustomKeyEventHandler"
              ? (handler: (e: KeyboardEvent) => boolean) => {
                  captured.keyHandler = handler;
                }
              : target[prop],
      });
    }
  }
  return { Stub, RecordingTerminal, written, captured, cleared, focused };
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

// The ping interval the handshake reports; undefined is a server that does not say.
let handshakePingInterval: number | undefined;

const defaultFetch = async (url: string) => {
  if (url === "/api/v1/terminal/handshake") {
    return new Response(
      JSON.stringify({ ws_url: "ws://127.0.0.1:8822/ws/term", ping_interval: handshakePingInterval }),
      { status: 200 },
    );
  }
  return new Response("{}", { status: 200 });
};
const fetchMock = vi.fn(defaultFetch);

beforeEach(async () => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  FakeWebSocket.instances = [];
  Object.values(messageMock).forEach((m) => m.mockClear());
  signInMock.mockClear();
  snapshotMock.mockClear();
  stubs.written.length = 0;
  stubs.cleared.count = 0;
  fetchMock.mockClear();
  fetchMock.mockImplementation(defaultFetch); // a describe may have swapped it
  handshakePingInterval = undefined;
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

  // Mutation killed: a first connect toasts "Connected to {box}" (the terminal showing is the sign).
  it("the first connect shows no toast", async () => {
    const wrapper = await mountConnected();
    socket(0).onopen!();
    await flushPromises();
    expect(messageMock.success).not.toHaveBeenCalled();
    expect(messageMock.info).not.toHaveBeenCalled();
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

describe("Terminal: a clicked file link opens the Files view at the folder above it", () => {
  const stat = (info: { is_directory: boolean; is_file: boolean }) =>
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes("/stat?")
        ? new Response(JSON.stringify({ exists: true, ...info }), { status: 200 })
        : new Response(JSON.stringify({ ws_url: "ws://127.0.0.1:8822/ws/term" }), { status: 200 }));

  async function click(uri: string) {
    const win = { location: { href: "" }, close: vi.fn() };
    vi.spyOn(window, "open").mockReturnValue(win as unknown as Window);
    const wrapper = await mountConnected();
    fetchMock.mockClear();
    stubs.captured.options.linkHandler.activate(new MouseEvent("click"), uri);
    await flushPromises();
    const statUrls = fetchMock.mock.calls.map((c) => decodeURIComponent(String(c[0]))).filter((u) => u.includes("/stat?"));
    wrapper.unmount();
    return { href: win.location.href, statUrls };
  }

  // Mutation: put back `cleanPath.split('/').slice(0, -1).join('/') || '/'` for the folder:
  // a file at the drive root opens folder `C:`, which Windows reads as the current folder of C.
  it.each([
    ["file:///C:/Users/me/a.txt", "C:/Users/me", "a.txt"],
    ["file:///C:/a.txt", "C:/", "a.txt"],
    ["file:///C:/Users/me/../b.txt", "C:/Users", "b.txt"],
    ["file:///home/me/a.txt", "/home/me", "a.txt"],
    ["file:///a.txt", "/", "a.txt"],
  ])("a file link %s opens folder %s and previews %s", async (uri, folder, name) => {
    stat({ is_directory: false, is_file: true });
    const { href } = await click(uri);
    expect(href).toBe(`/app/files?${new URLSearchParams({ box: "box1", path: folder, preview: name }).toString()}`);
  });

  // Mutation: put back `resolved.replace(/\/+$/, '') || '/'` for the stat path: a drive root
  // is asked for as `C:` and opened as `C:`.
  it("a link to a drive root asks the box about C:/ and opens C:/", async () => {
    stat({ is_directory: true, is_file: false });
    const { href, statUrls } = await click("file:///C:/");
    expect(statUrls).toEqual(["/api/v1/boxes/box1/stat?path=C:/"]);
    expect(href).toBe(`/app/files?${new URLSearchParams({ box: "box1", path: "C:/" }).toString()}`);
  });
});

describe("Terminal.vue a phone that sleeps", () => {
  beforeEach(() => {
    handshakePingInterval = 30;
  });

  const ping = (ws: FakeWebSocket) => ws.onmessage!({ data: JSON.stringify({ op: "ping" }) });
  const wake = () => document.dispatchEvent(new Event("visibilitychange"));

  // Every attempt fails until the policy stops trying.
  async function giveUp() {
    for (let i = 0; i < 11; i++) {
      socket(i).onclose!({ code: 1006 });
      await advance(30_000);
    }
  }

  async function openFirst() {
    const wrapper = await mountConnected();
    socket(0).onopen!();
    await flushPromises();
    return wrapper;
  }

  // Mutation: no silence watchdog (a dead socket looks connected for ever).
  it("a socket silent for two and a half ping intervals is let go of and reconnected", async () => {
    const wrapper = await openFirst();
    await advance(74_999);
    expect(socket(0).close).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);

    await advance(1);
    expect(socket(0).close).toHaveBeenCalled();
    expect(wrapper.text()).toContain(en["terminal.reconnecting_to"]!.replace("{box}", "box1"));
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    wrapper.unmount();
  });

  // Mutation: the limit a bare 75 s on the page; it must follow what the server reports.
  it("the silence limit follows the interval the handshake reports", async () => {
    handshakePingInterval = 10;
    const wrapper = await openFirst();
    await advance(24_999);
    expect(socket(0).close).not.toHaveBeenCalled();
    await advance(1);
    expect(socket(0).close).toHaveBeenCalled();
    wrapper.unmount();
  });

  // Mutation: the watchdog counts from the open and not from the last frame.
  it("a frame every 29 s never makes the socket dead", async () => {
    const wrapper = await openFirst();
    for (let i = 0; i < 20; i++) {
      await advance(29_000);
      ping(socket(0));
    }
    expect(socket(0).close).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });

  // Mutation: waking up reconnects a socket that is fine.
  it("the page becoming visible while connected changes nothing", async () => {
    const wrapper = await openFirst();
    wake();
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket(0).close).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  // Mutation: no wake handler (the terminal stays given up).
  it("visible after the attempts ran out connects at once and counts from zero again", async () => {
    const wrapper = await mountConnected();
    for (let i = 0; i < 10; i++) {
      socket(i).onclose!({ code: 1006 });
      await advance(30_000);
    }
    socket(10).onclose!({ code: 1006 });
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(11);

    wake();
    await flushPromises();
    expect(FakeWebSocket.instances).toHaveLength(12);
    socket(11).onopen!();
    await flushPromises();
    socket(11).onclose!({ code: 1006 });
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(13);
    wrapper.unmount();
  });

  // Mutation: each event opens its own socket.
  it("online twice within a second makes one connect", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 1006 });
    window.dispatchEvent(new Event("online"));
    await advance(200);
    window.dispatchEvent(new Event("online"));
    await advance(800);
    expect(FakeWebSocket.instances).toHaveLength(2);
    wrapper.unmount();
  });

  // Mutation: the wake path ignores the close policy.
  it("a 4403 close then visible stays closed", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 4403 });
    wake();
    window.dispatchEvent(new Event("online"));
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });

  // Mutation: the wake path ignores the user's disconnect.
  it("the user's disconnect then visible stays closed", async () => {
    const wrapper = await openFirst();
    (wrapper.vm as any).disconnect();
    socket(0).onclose!({ code: 1000 });
    wake();
    await advance(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });

  // Mutation: the wake path dials while a connect is in flight.
  it("visible while a connect is in flight opens no second socket", async () => {
    const wrapper = await mountConnected();
    socket(0).onclose!({ code: 1006 });
    let release!: () => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          release = () =>
            resolve(new Response(JSON.stringify({ ws_url: "ws://127.0.0.1:8822/ws/term" }), { status: 200 }));
        }),
    );
    await vi.advanceTimersByTimeAsync(1000); // the backoff ends: connect() waits on the handshake
    wake();
    await flushPromises();
    release();
    await flushPromises();
    expect(FakeWebSocket.instances).toHaveLength(2);
    wrapper.unmount();
  });

  // Mutation: the overdue timer drops the socket at once (a frozen page kills a running command).
  it("a timer that fires long after it was due waits one ping interval for queued frames", async () => {
    const wrapper = await openFirst();
    // The page froze: the clock jumps past the limit and no timer has run yet.
    vi.setSystemTime(Date.now() + 200_000);
    await advance(75_000);
    expect(socket(0).close).not.toHaveBeenCalled();
    ping(socket(0)); // the frame queued during the freeze arrives
    await advance(60_000);
    expect(socket(0).close).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
    wrapper.unmount();
  });

  it("after the overdue grace, silence still drops the socket", async () => {
    const wrapper = await openFirst();
    vi.setSystemTime(Date.now() + 200_000);
    await advance(75_000);
    expect(socket(0).close).not.toHaveBeenCalled();
    await advance(29_999);
    expect(socket(0).close).not.toHaveBeenCalled();
    await advance(1);
    expect(socket(0).close).toHaveBeenCalled();
    wrapper.unmount();
  });

  // Mutation: a replaced socket's late close stops the new socket's watchdog.
  it("a replaced socket's late close leaves the new socket's watchdog running", async () => {
    const wrapper = await openFirst();
    (wrapper.vm as any).disconnect();
    (wrapper.vm as any).connect();
    await flushPromises();
    expect(FakeWebSocket.instances).toHaveLength(2);
    socket(1).onopen!();
    await flushPromises();
    socket(0).onclose!({ code: 1000 });
    await advance(75_000);
    expect(socket(1).close).toHaveBeenCalled();
    wrapper.unmount();
  });

  // Mutation: a replaced socket's late close marks the new socket closed for good.
  it("a replaced socket's late 4403 close does not stop a wake from reconnecting the new one", async () => {
    const wrapper = await openFirst();
    (wrapper.vm as any).disconnect();
    (wrapper.vm as any).connect();
    await flushPromises();
    socket(1).onopen!();
    await flushPromises();
    socket(0).onclose!({ code: 4403 });
    socket(1).onclose!({ code: 1006 });
    wake();
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(3);
    wrapper.unmount();
  });

  // Mutation: a replaced socket's late close schedules a reconnect.
  it("a replaced socket's late 1006 close schedules no second connect", async () => {
    const wrapper = await openFirst();
    (wrapper.vm as any).disconnect();
    (wrapper.vm as any).connect();
    await flushPromises();
    socket(1).onopen!();
    await flushPromises();
    socket(0).onclose!({ code: 1006 });
    await advance(30_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    wrapper.unmount();
  });

  // Mutation: onerror clears connecting before onclose ran.
  it("a wake between onerror and onclose opens no second socket", async () => {
    const wrapper = await mountConnected();
    socket(0).onerror!(new Event("error"));
    wake();
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    socket(0).onclose!({ code: 1006 });
    await advance(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    wrapper.unmount();
  });

  it("a handshake with no ping_interval arms no watchdog, and a wake still reconnects a dropped socket", async () => {
    handshakePingInterval = undefined;
    const wrapper = await openFirst();
    await advance(600_000);
    expect(socket(0).close).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
    await giveUp();
    const n = FakeWebSocket.instances.length;
    wake();
    await advance(0);
    expect(FakeWebSocket.instances).toHaveLength(n + 1);
    wrapper.unmount();
  });

  // The brief: waking does nothing while the page is hidden; it connects when it shows.
  it("online while the page is hidden connects nothing; becoming visible does", async () => {
    const wrapper = await openFirst();
    await giveUp();
    const n = FakeWebSocket.instances.length;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    try {
      window.dispatchEvent(new Event("online"));
      await advance(1000);
      expect(FakeWebSocket.instances).toHaveLength(n);
    } finally {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    }
    wake();
    await advance(0);
    expect(FakeWebSocket.instances).toHaveLength(n + 1);
    wrapper.unmount();
  });
});

// The palette in main.css: a theme block's own value of a token.
const paletteToken = (block: '[data-theme="light"] {' | '.dark, [data-theme="dark"] {', token: string): string => {
  const css = readFileSync(resolve(__dirname, "../styles/main.css"), "utf8");
  const start = css.indexOf(block);
  const body = css.slice(start, css.indexOf("\n}", start));
  const value = body.match(new RegExp(`^\\s*${token}:\\s*([^;]+);`, "m"))?.[1];
  if (!value) throw new Error(`${token} is not in ${block}`);
  return value.trim();
};

async function mountOn(pinia: ReturnType<typeof createPinia>, extra: Record<string, unknown> = {}, slots = {}) {
  const wrapper = mount(Terminal, {
    props: { boxName: "box1", sessionName: "main", directory: "/tmp", ...extra },
    slots,
    global: { plugins: [pinia], stubs: { SessionSwitcher: true } },
  });
  await vi.advanceTimersByTimeAsync(100);
  await flushPromises();
  return wrapper;
}

const key = (init: KeyboardEventInit) => new KeyboardEvent("keydown", { cancelable: true, ...init });

describe("Terminal.vue: the rail's keys from inside the terminal", () => {
  // Mutation: no rail branch in the key handler -> Alt+\ goes to the shell as ESC \ and the
  // rail stays closed (the window listener skips keys typed into xterm's textarea).
  it("Alt+\\ toggles the rail, and the shell is not sent the key", async () => {
    const pinia = createPinia();
    const wrapper = await mountOn(pinia);
    const app = useAppStore(pinia);
    expect(app.railCollapsed).toBe(true);

    const first = key({ key: "\\", code: "Backslash", altKey: true });
    expect(stubs.captured.keyHandler!(first)).toBe(false);
    expect(first.defaultPrevented).toBe(true);
    expect(app.railCollapsed).toBe(false);
    expect(stubs.captured.keyHandler!(key({ key: "\\", code: "Backslash", altKey: true }))).toBe(false);
    expect(app.railCollapsed).toBe(true);
    wrapper.unmount();
  });

  // Mutation: Escape left to the shell while the rail is open -> the rail stays open.
  it("Escape closes an open rail and is not sent to the shell", async () => {
    const pinia = createPinia();
    const wrapper = await mountOn(pinia);
    const app = useAppStore(pinia);
    app.toggleRailCollapsed();

    expect(stubs.captured.keyHandler!(key({ key: "Escape", code: "Escape" }))).toBe(false);
    expect(app.railCollapsed).toBe(true);
    wrapper.unmount();
  });

  // Mutation: Escape always eaten by the rail branch -> vim never sees Escape.
  it("Escape with the rail closed goes to the shell, and the rail stays closed", async () => {
    const pinia = createPinia();
    const wrapper = await mountOn(pinia);
    const app = useAppStore(pinia);

    expect(stubs.captured.keyHandler!(key({ key: "Escape", code: "Escape" }))).toBe(true);
    expect(app.railCollapsed).toBe(true);
    wrapper.unmount();
  });
});

describe("Terminal.vue: the calm look", () => {
  // Mutation: the default theme left at cyberpunk -> the background is #0a0e14 (or One
  // Light's #fafafa in light mode), not the palette's surface.
  it.each([
    ["light", '[data-theme="light"] {'],
    ["dark", '.dark, [data-theme="dark"] {'],
  ] as const)("with no saved theme, the %s page's terminal takes the palette's surface, text and accent", async (mode, block) => {
    localStorage.setItem("sshler:ui:color-mode", mode);
    const wrapper = await mountOn(createPinia(), { theme: undefined });
    const theme = stubs.captured.options.theme;

    expect(theme.background).toBe(paletteToken(block, "--surface"));
    expect(theme.foreground).toBe(paletteToken(block, "--text-secondary"));
    expect(theme.cursor).toBe(paletteToken(block, "--accent"));
    wrapper.unmount();
  });

  // Mutation: a saved theme replaced by the new default -> nord's #2e3440 is lost.
  it("a saved theme is kept: nord stays nord", async () => {
    localStorage.setItem("sshler:ui:color-mode", "dark");
    const wrapper = await mountOn(createPinia(), { theme: "nord" });
    expect(stubs.captured.options.theme.background).toBe("#2e3440");
    wrapper.unmount();
  });

  // Mutation: the scanline overlay kept -> one `.scanlines` element over the terminal.
  it("draws no scanline overlay", async () => {
    const wrapper = await mountOn(createPinia());
    expect(wrapper.findAll(".scanlines")).toHaveLength(0);
    wrapper.unmount();
  });
});

describe("Terminal.vue: a title bar that can close its pane", () => {
  // Mutation: no `closable` prop -> no close button, no `close` event.
  it("closable shows one close button, and a click emits close once", async () => {
    const wrapper = await mountOn(createPinia(), { closable: true });
    const buttons = wrapper.findAll('[data-testid="terminal-close"]');
    expect(buttons).toHaveLength(1);
    await buttons[0]!.trigger("click");
    expect(wrapper.emitted("close")).toEqual([[]]);
    wrapper.unmount();
  });

  it("a single terminal has no close button", async () => {
    const wrapper = await mountOn(createPinia());
    expect(wrapper.findAll('[data-testid="terminal-close"]')).toHaveLength(0);
    wrapper.unmount();
  });

  // Mutation: no `titlebar-meta` slot -> the caller's chip is not drawn in the title bar.
  it("the titlebar-meta slot is drawn in the title bar's centre", async () => {
    const wrapper = await mountOn(createPinia(), {}, { "titlebar-meta": '<span class="meta-chip">srv/app</span>' });
    expect(wrapper.find(".titlebar-center .meta-chip").text()).toBe("srv/app");
    wrapper.unmount();
  });
});

// The narrow title bar: one "more" button opens the shared menu with every other action.
describe("Terminal.vue: the narrow title bar's menu", () => {
  const MENU_IDS = [
    "prev-window", "new-window", "next-window",
    "pane-left", "split-vertical", "split-horizontal", "pane-right",
    "mouse", "copy-mode", "scrollback", "copy-all", "paste", "clear", "search",
    "sessions", "fullscreen", "help",
  ];
  const KILL_IDS = ["kill-pane", "kill-window", "kill-session", "force-kill"];
  // The original button of each plain action: its index among `.toolbar-btn` / `.titlebar-btn`.
  const TOOLBAR_BUTTON: Record<string, number> = {
    "prev-window": 0, "new-window": 1, "next-window": 2,
    "pane-left": 3, "split-vertical": 4, "split-horizontal": 5, "pane-right": 6,
  };
  const TITLEBAR_BUTTON: Record<string, number> = {
    mouse: 0, "copy-mode": 1, scrollback: 2, "copy-all": 3, paste: 4, clear: 5,
    search: 6, sessions: 7, fullscreen: 8,
  };
  const KILL_BYTES: Record<string, string> = {
    "kill-pane": "kill-pane\n", "kill-window": "kill-window\n", "kill-session": "kill-session\n",
  };

  let fullscreenCalls = 0;
  // The element each requestFullscreen call was made on.
  let fullscreenTargets: Element[] = [];
  beforeEach(() => {
    fullscreenCalls = 0;
    fullscreenTargets = [];
    (Element.prototype as any).requestFullscreen = async function (this: Element) { fullscreenCalls++; fullscreenTargets.push(this); };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: async () => { throw new Error("no"); }, writeText: async () => {} },
    });
  });

  async function open() {
    const wrapper = mount(Terminal, {
      props: { boxName: "box1", sessionName: "main", directory: "/tmp" },
      global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
      attachTo: document.body,
    });
    await vi.advanceTimersByTimeAsync(100);
    await flushPromises();
    socket(0).readyState = FakeWebSocket.OPEN;
    socket(0).onopen!();
    await flushPromises();
    return wrapper;
  }

  // The bytes sent to the shell; the page's own JSON frames (resize, ping) are not actions.
  const sent = () =>
    socket(0).send.mock.calls.map((c) => c[0]).filter((d) => typeof d !== "string").map((d) => new TextDecoder().decode(d as Uint8Array));
  const observe = (wrapper: ReturnType<typeof mount>) => ({
    sent: sent(),
    search: wrapper.find(".terminal-search-bar").exists(),
    cleared: stubs.cleared.count,
    scrollback: document.querySelectorAll(".scrollback-btn").length,
    switcher: document.querySelectorAll("session-switcher-stub").length,
    fullscreen: fullscreenCalls,
    messages: Object.entries(messageMock).map(([k, m]) => [k, m.mock.calls.map((c) => c[0])]),
  });

  async function viaMenu(wrapper: ReturnType<typeof mount>, id: string) {
    await wrapper.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    const item = document.querySelector(`[data-testid="terminal-menu-${id}"]`) as HTMLElement | null;
    expect(item, `menu item ${id}`).not.toBeNull();
    item!.click();
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
  }

  it("has one more button, and the menu lists every action with its label", async () => {
    const wrapper = await open();
    expect(wrapper.findAll('[data-testid="terminal-more"]')).toHaveLength(1);
    await wrapper.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    const ids = [...document.querySelectorAll("[data-testid^='terminal-menu-']")].map((e) => e.getAttribute("data-testid")!.slice(14));
    expect(ids.sort()).toEqual([...MENU_IDS, ...KILL_IDS].sort());
    const labels = [...document.querySelectorAll(".context-menu-item")].map((e) => e.textContent!.trim());
    // Each item shows its own short menu_* text, never the button's tooltip.
    const menuKeys = ["prev_window", "new_window", "next_window", "pane_left", "split_vertical", "split_horizontal", "pane_right", "kill_pane", "kill_window", "kill_session", "force_kill", "mouse_on", "copy_mode", "scrollback", "copy_all", "paste", "clear", "search", "sessions", "fullscreen", "help"];
    for (const k of menuKeys) {
      const text = (en as Record<string, string>)[`terminal.menu_${k}`];
      expect(text, `terminal.menu_${k}`).toBeTruthy();
      expect(text!.length, `terminal.menu_${k} length`).toBeLessThanOrEqual(22);
      expect(labels).toContain(text);
    }
    expect(labels).not.toContain(en["terminal.scrollback_viewer_tip"]);
    expect(labels).not.toContain(en["terminal.new_window"]);
    expect(wrapper.find('[data-testid="terminal-more"]').attributes("aria-label")).toBe(en["terminal.more_actions"]);
    wrapper.unmount();
  });

  it.each(Object.keys({ ...TOOLBAR_BUTTON, ...TITLEBAR_BUTTON }))("%s from the menu does what its button does", async (id) => {
    const button = await open();
    const before = observe(button);
    const buttons = id in TOOLBAR_BUTTON ? button.findAll(".toolbar-btn") : button.findAll(".titlebar-btn");
    await buttons[(TOOLBAR_BUTTON[id] ?? TITLEBAR_BUTTON[id])!]!.trigger("click");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    const byButton = observe(button);
    button.unmount();
    document.body.innerHTML = "";
    await vi.advanceTimersByTimeAsync(600); // past the paste debounce
    messageMock.error.mockClear(); messageMock.info.mockClear(); messageMock.warning.mockClear(); messageMock.success.mockClear();
    FakeWebSocket.instances = [];
    fullscreenCalls = 0;
    stubs.cleared.count = 0;

    const menu = await open();
    await viaMenu(menu, id);
    const byMenu = observe(menu);
    menu.unmount();

    expect(byMenu).toEqual(byButton);
    expect(byMenu).not.toEqual(before);
  });

  it.each(KILL_IDS)("%s asks first, then does what its button does", async (id) => {
    const wrapper = await open();
    await viaMenu(wrapper, id);
    // Nothing is sent or called before the confirmation.
    expect(sent()).toEqual([]);
    const confirm = document.querySelector('[data-testid="terminal-menu-confirm"]') as HTMLElement | null;
    expect(confirm, "a confirmation").not.toBeNull();
    confirm!.click();
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    if (id === "force-kill") {
      expect(fetchMock.mock.calls.map((c) => String(c[0])).some((u) => u.endsWith("/sessions/by-name/main/force-kill"))).toBe(true);
    } else {
      expect(sent()).toEqual(["\x02:", KILL_BYTES[id]]);
    }
    wrapper.unmount();
  });

  // R4c.r1: the pane that was asked fills the screen, not the first pane on the page.
  it("fullscreen from the menu is requested on that pane's own root, in a grid of panes", async () => {
    const first = await open();
    const second = mount(Terminal, {
      props: { boxName: "box1", sessionName: "second", directory: "/tmp" },
      global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
      attachTo: document.body,
    });
    await vi.advanceTimersByTimeAsync(100);
    await flushPromises();
    await viaMenu(second, "fullscreen");
    expect(fullscreenTargets).toEqual([second.element]);
    expect(fullscreenTargets[0]).not.toBe(first.element);
    first.unmount();
    second.unmount();
  });

  // R4c.r2: "more" is a toggle, and one pane's menu at a time.
  it("a second click on more closes the menu", async () => {
    const wrapper = await open();
    await wrapper.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    expect(document.querySelectorAll(".context-menu")).toHaveLength(1);
    await wrapper.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    expect(document.querySelectorAll(".context-menu")).toHaveLength(0);
    wrapper.unmount();
  });

  it("opening one pane's menu closes the other pane's", async () => {
    const first = await open();
    const second = mount(Terminal, {
      props: { boxName: "box1", sessionName: "second", directory: "/tmp" },
      global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
      attachTo: document.body,
    });
    await vi.advanceTimersByTimeAsync(100);
    await flushPromises();
    await first.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    await second.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    expect(document.querySelectorAll(".context-menu")).toHaveLength(1);
    first.unmount();
    second.unmount();
  });

  // R4c.r3: the keyboard is back in the terminal after an action and after a card closes.
  it.each(["mouse", "copy-mode", "copy-all", "clear", "fullscreen"])("%s from the menu leaves the keyboard in the terminal", async (id) => {
    const wrapper = await open();
    stubs.focused.count = 0;
    await viaMenu(wrapper, id);
    expect(stubs.focused.count).toBeGreaterThan(0);
    wrapper.unmount();
  });

  it("cancelling the kill card, and the kill it confirms, return the keyboard to the terminal", async () => {
    const wrapper = await open();
    await viaMenu(wrapper, "kill-pane");
    stubs.focused.count = 0;
    const cancel = [...document.querySelectorAll(".confirm-btn")].find((b) => !b.classList.contains("danger")) as HTMLElement;
    cancel.click();
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    expect(stubs.focused.count).toBeGreaterThan(0);
    await viaMenu(wrapper, "kill-pane");
    stubs.focused.count = 0;
    (document.querySelector('[data-testid="terminal-menu-confirm"]') as HTMLElement).click();
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    expect(stubs.focused.count).toBeGreaterThan(0);
    wrapper.unmount();
  });

  it("closing the help card returns the keyboard to the terminal", async () => {
    const wrapper = await open();
    await viaMenu(wrapper, "help");
    stubs.focused.count = 0;
    const modals = wrapper.findAllComponents(NModal);
    modals.find((m) => m.props("show"))!.vm.$emit("update:show", false);
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    expect(stubs.focused.count).toBeGreaterThan(0);
    wrapper.unmount();
  });

  // R4c.r6: two different icons in a 32 px bar.
  it("the more button does not wear the sessions button's icon", async () => {
    const wrapper = await open();
    // The sessions button wears PhList; "more" must wear something else (dots), at any weight.
    expect(wrapper.findAll(".titlebar-btn")[7]!.findComponent(PhList).exists()).toBe(true);
    expect(wrapper.find('[data-testid="terminal-more"]').findComponent(PhList).exists()).toBe(false);
    expect(wrapper.find('[data-testid="terminal-more"]').find("svg").exists()).toBe(true);
    wrapper.unmount();
  });

  // R4c.r8: a kill that needs the socket cannot be confirmed without one.
  it.each(["kill-pane", "kill-window", "kill-session"])("%s: Confirm is disabled once the connection is gone", async (id) => {
    const wrapper = await open();
    await viaMenu(wrapper, id);
    const confirm = () => document.querySelector('[data-testid="terminal-menu-confirm"]') as HTMLButtonElement;
    expect(confirm().disabled).toBe(false);
    socket(0).readyState = 3;
    socket(0).onclose!({ code: 1006 });
    await flushPromises();
    expect(confirm().disabled).toBe(true);
    wrapper.unmount();
  });

  it("help from the menu shows the shortcut help", async () => {
    const wrapper = await open();
    await viaMenu(wrapper, "help");
    expect(document.body.textContent).toContain(en["terminal.help_title" as keyof typeof en]);
    wrapper.unmount();
  });

  it("a plain shell has no tmux items in the menu", async () => {
    const wrapper = mount(Terminal, {
      props: { boxName: "box1", sessionName: "main", directory: "/tmp", shell: "powershell" },
      global: { plugins: [createPinia()], stubs: { SessionSwitcher: true } },
      attachTo: document.body,
    });
    await vi.advanceTimersByTimeAsync(100);
    await flushPromises();
    await wrapper.find('[data-testid="terminal-more"]').trigger("click");
    await flushPromises();
    const ids = [...document.querySelectorAll("[data-testid^='terminal-menu-']")].map((e) => e.getAttribute("data-testid")!.slice(14));
    expect(ids).not.toContain("prev-window");
    expect(ids).not.toContain("mouse");
    expect(ids).toContain("search");
    wrapper.unmount();
  });
});
