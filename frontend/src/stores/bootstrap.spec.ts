import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useBootstrapStore } from "./bootstrap";

describe("bootstrap store", () => {
  beforeEach(() => {
    // localStorage is provided + cleared per-test by vitest.setup.ts.
    setActivePinia(createPinia());
  });

  it("persists token via setToken", () => {
    const store = useBootstrapStore();
    store.setToken("abc");
    expect(store.token).toBe("abc");
    expect(globalThis.localStorage.getItem("sshler:token")).toBe("abc");
  });

  it("signedIn: false with no answer, true with no password, by the answer with one", () => {
    const store = useBootstrapStore();
    const answer = (auth_required: boolean, authenticated: boolean) => ({
      version: "1", token_header: "X-SSHLER-TOKEN", token: null, auth_required, authenticated,
      allow_origins: [], spa_base: "/app/", spa_enabled: true, platform: "posix",
      default_shell: null, windows_shells: [],
    }) as unknown as typeof store.payload;
    const seen = [store.signedIn];
    for (const [required, authed] of [[false, true], [true, false], [true, true]] as const) {
      store.payload = answer(required, authed);
      seen.push(store.signedIn);
    }
    expect(seen).toEqual([false, true, false, true]);
  });

  it("defaults platform to posix with no Windows shells", () => {
    const store = useBootstrapStore();
    expect(store.platform).toBe("posix");
    expect(store.isWindows).toBe(false);
    expect(store.windowsShells).toEqual([]);
    expect(store.defaultShell).toBeNull();
  });

  it("exposes Windows shells and default when platform is windows", () => {
    const store = useBootstrapStore();
    store.payload = {
      version: "0.12.0",
      token_header: "X-SSHLER-TOKEN",
      token: null,
      auth_required: false,
      authenticated: true,
      allow_origins: [],
      spa_base: "/app/",
      spa_enabled: true,
      platform: "windows",
      default_shell: "pwsh",
      windows_shells: [
        { id: "pwsh", label: "PowerShell 7", available: true },
        { id: "cmd", label: "Command Prompt", available: true },
        { id: "wsl", label: "WSL", available: false },
      ],
    };

    expect(store.platform).toBe("windows");
    expect(store.isWindows).toBe(true);
    expect(store.defaultShell).toBe("pwsh");
    expect(store.windowsShells).toHaveLength(3);
    expect(store.windowsShells[1]).toEqual({
      id: "cmd",
      label: "Command Prompt",
      available: true,
    });
    expect(store.windowsShells[2]?.available).toBe(false);
  });
});

describe("bootstrap store: who is asking", () => {
  const fetchMock = vi.fn();

  function bootstrapAnswer(fields: { token: string | null; auth_required: boolean; authenticated: boolean }): Response {
    return new Response(
      JSON.stringify({
        version: "1", token_header: "X-SSHLER-TOKEN", allow_origins: [], spa_base: "/app/",
        spa_enabled: true, ...fields,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  beforeEach(() => {
    setActivePinia(createPinia());
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads auth_required and authenticated, and keeps the token of a signed-in page", async () => {
    fetchMock.mockResolvedValueOnce(bootstrapAnswer({ token: "tok", auth_required: true, authenticated: true }));
    const store = useBootstrapStore();
    await store.bootstrap();
    store.cleanup();
    expect([store.authRequired, store.authenticated, store.token]).toEqual([true, true, "tok"]);
    expect(globalThis.localStorage.getItem("sshler:token")).toBe("tok");
  });

  it("a signed-out answer drops a token kept from an earlier session", async () => {
    globalThis.localStorage.setItem("sshler:token", "old");
    fetchMock.mockResolvedValueOnce(bootstrapAnswer({ token: null, auth_required: true, authenticated: false }));
    const store = useBootstrapStore();
    expect(store.token).toBe("old");
    await store.bootstrap();
    store.cleanup();
    expect([store.authRequired, store.authenticated, store.token]).toEqual([true, false, null]);
    expect(globalThis.localStorage.getItem("sshler:token")).toBeNull();
  });

  it("calls made while one is in flight share its answer", async () => {
    fetchMock.mockResolvedValueOnce(bootstrapAnswer({ token: "tok", auth_required: false, authenticated: true }));
    const store = useBootstrapStore();
    await Promise.all([store.bootstrap(), store.bootstrap()]);
    store.cleanup();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("forget drops the bootstrap and the stored token", async () => {
    fetchMock.mockResolvedValueOnce(bootstrapAnswer({ token: "tok", auth_required: true, authenticated: true }));
    const store = useBootstrapStore();
    await store.bootstrap();
    store.forget();
    expect([store.payload, store.token, store.authRequired, store.authenticated]).toEqual([null, null, false, false]);
    expect(globalThis.localStorage.getItem("sshler:token")).toBeNull();
  });
});
