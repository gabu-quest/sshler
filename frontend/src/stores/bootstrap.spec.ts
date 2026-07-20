import { beforeEach, describe, expect, it } from "vitest";
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
      basic_auth_required: false,
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
