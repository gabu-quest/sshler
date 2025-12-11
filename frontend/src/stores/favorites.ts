import { ref } from "vue";

import { defineStore } from "pinia";

import { fetchBox, toggleFavorite, togglePin } from "@/api/http";
import type { ApiBox } from "@/api/types";

export const useFavoritesStore = defineStore("favorites", () => {
  const favoritesByBox = ref<Map<string, Set<string>>>(new Map());
  const pinnedBoxes = ref<Set<string>>(new Set());
  const error = ref<string | null>(null);
  const loading = ref(false);

  function favoritesForBox(box: string | null | undefined): Set<string> {
    if (!box) return new Set();
    return favoritesByBox.value.get(box) || new Set();
  }

  function isFavorite(box: string | null | undefined, path: string): boolean {
    return favoritesForBox(box).has(path);
  }

  function isPinned(box: string | null | undefined): boolean {
    if (!box) return false;
    return pinnedBoxes.value.has(box);
  }

  function setPinnedFlag(box: string, pinned: boolean) {
    const nextPinned = new Set(pinnedBoxes.value);
    if (pinned) {
      nextPinned.add(box);
    } else {
      nextPinned.delete(box);
    }
    pinnedBoxes.value = nextPinned;
  }

  function setFavoritesFromList(box: string, paths: string[]) {
    const next = new Map(favoritesByBox.value);
    next.set(box, new Set(paths || []));
    favoritesByBox.value = next;
  }

  function hydrateFromBoxes(boxes: ApiBox[]) {
    const nextFavorites = new Map(favoritesByBox.value);
    const nextPinned = new Set(pinnedBoxes.value);
    boxes.forEach((box) => {
      nextFavorites.set(box.name, new Set(box.favorites || []));
      if (box.pinned) {
        nextPinned.add(box.name);
      } else {
        nextPinned.delete(box.name);
      }
    });
    favoritesByBox.value = nextFavorites;
    pinnedBoxes.value = nextPinned;
  }

  async function loadBox(box: string, token: string | null): Promise<ApiBox | null> {
    loading.value = true;
    error.value = null;
    try {
      const payload = await fetchBox(box, token);
      setFavoritesFromList(box, payload.favorites || []);
      setPinnedFlag(box, !!payload.pinned);
      return payload;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return null;
    } finally {
      loading.value = false;
    }
  }

  async function setFavorite(box: string, path: string, favorite: boolean, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      await toggleFavorite(box, path, favorite, token);
      const next = new Set(favoritesForBox(box));
      if (favorite) {
        next.add(path);
      } else {
        next.delete(path);
      }
      const updated = new Map(favoritesByBox.value);
      updated.set(box, next);
      favoritesByBox.value = updated;
      return favorite;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return !favorite;
    } finally {
      loading.value = false;
    }
  }

  async function setPinned(box: string, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      const result = await togglePin(box, token);
      setPinnedFlag(box, result.pinned);
      return result.pinned;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return false;
    } finally {
      loading.value = false;
    }
  }

  return {
    favoritesByBox,
    pinnedBoxes,
    error,
    loading,
    favoritesForBox,
    isFavorite,
    isPinned,
    hydrateFromBoxes,
    loadBox,
    setFavorite,
    setFavoritesFromList,
    setPinned,
  };
});
