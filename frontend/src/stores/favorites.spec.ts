import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { useFavoritesStore } from "./favorites";

vi.mock("@/api/http", () => ({
  toggleFavorite: vi.fn().mockResolvedValue({ path: "/tmp", favorite: true }),
  togglePin: vi.fn().mockResolvedValue({ name: "local", pinned: true }),
  fetchBox: vi.fn().mockResolvedValue({ name: "local", favorites: ["/tmp"], pinned: true }),
}));

describe("favorites store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("sets favorites from list", () => {
    const store = useFavoritesStore();
    store.setFavoritesFromList("box-a", ["/a", "/b"]);
    expect(store.isFavorite("box-a", "/a")).toBe(true);
  });

  it("tracks favorites per box", async () => {
    const store = useFavoritesStore();
    await store.setFavorite("box-a", "/tmp", true, null);
    await store.setFavorite("box-b", "/var", true, null);
    expect(store.isFavorite("box-a", "/tmp")).toBe(true);
    expect(store.isFavorite("box-b", "/tmp")).toBe(false);
  });

  it("tracks pinned boxes separately", async () => {
    const store = useFavoritesStore();
    await store.setPinned("box-a", null);
    expect(store.isPinned("box-a")).toBe(true);
    expect(store.isPinned("box-b")).toBe(false);
  });
});
