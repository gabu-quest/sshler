import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/apiError";
import { useDirectoryStore } from "./directory";

vi.mock("@/api/http", () => ({
  fetchDirectory: vi.fn(),
}));

import { fetchDirectory } from "@/api/http";

describe("the directory store: a folder's children", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("a folder that could not be read answers null and puts the failure's message in the store's error", async () => {
    vi.mocked(fetchDirectory).mockRejectedValue(new ApiError(403, "denied", "/srv/secret: permission denied"));
    const store = useDirectoryStore();

    expect(await store.fetchChildren("build-box", "/srv/secret", "t")).toBe(null);
    expect(store.error).toBe("Permission denied — /srv/secret: permission denied");
  });

  it("a failed folder then one that is read leaves the store's error empty", async () => {
    vi.mocked(fetchDirectory)
      .mockRejectedValueOnce(new ApiError(403, "denied", "/srv/secret: permission denied"))
      .mockResolvedValueOnce({ box: "build-box", directory: "/srv/open", entries: [] });
    const store = useDirectoryStore();

    expect(await store.fetchChildren("build-box", "/srv/secret", "t")).toBe(null);
    expect(await store.fetchChildren("build-box", "/srv/open", "t")).toEqual({
      box: "build-box", directory: "/srv/open", entries: [],
    });
    expect(store.error).toBe(null);
  });
});
