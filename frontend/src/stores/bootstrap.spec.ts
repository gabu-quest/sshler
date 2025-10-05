import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useBootstrapStore } from "./bootstrap";

describe("bootstrap store", () => {
  beforeEach(() => {
    const storage: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage[key] ?? null,
      setItem: (key: string, value: string) => {
        storage[key] = value;
      },
      removeItem: (key: string) => {
        delete storage[key];
      },
    });
    setActivePinia(createPinia());
  });

  it("persists token via setToken", () => {
    const store = useBootstrapStore();
    store.setToken("abc");
    expect(store.token).toBe("abc");
    expect(globalThis.localStorage.getItem("sshler:token")).toBe("abc");
  });
});
