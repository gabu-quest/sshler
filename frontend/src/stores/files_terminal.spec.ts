import { describe, expect, it, vi, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useFilesStore } from "./files";
import { useBootstrapStore } from "./bootstrap";

vi.mock("@/api/http", () => ({
  fetchDirectory: vi.fn().mockResolvedValue({ box: "local", directory: "/", entries: [] }),
  touchFile: vi.fn().mockResolvedValue({ status: "ok", message: "created" }),
  deleteFile: vi.fn().mockResolvedValue({ status: "ok", message: "deleted" }),
  renameFile: vi.fn().mockResolvedValue({ status: "ok", message: "renamed" }),
  moveFile: vi.fn().mockResolvedValue({ status: "ok", message: "moved" }),
  copyFile: vi.fn().mockResolvedValue({ status: "ok", message: "copied" }),
  uploadFile: vi.fn().mockResolvedValue({ status: "ok", message: "uploaded" }),
  fetchBootstrap: vi.fn().mockResolvedValue({ token_header: "X-SSHLER-TOKEN", token: "t", version: "1", allow_origins: [], basic_auth_required: false, spa_base: "/app/" }),
}));

describe("files + terminal stores", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("bootstrap sets token", async () => {
    const store = useBootstrapStore();
    await store.bootstrap();
    expect(store.token).toBe("t");
  });

  it("files store loads listing", async () => {
    const files = useFilesStore();
    await files.load("local", "/", null);
    expect(files.listing?.box).toBe("local");
  });
});
