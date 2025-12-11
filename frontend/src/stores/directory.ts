import { ref } from "vue";

import { defineStore } from "pinia";

import { fetchDirectory } from "@/api/http";
import type { DirectoryListing } from "@/api/types";

export const useDirectoryStore = defineStore("directory", () => {
  const listing = ref<DirectoryListing | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const filter = ref("");
  const recents = ref<Record<string, string[]>>({});

  async function load(box: string, directory: string, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      listing.value = await fetchDirectory(box, directory, token);
      remember(box, directory);
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  function setFilter(value: string) {
    filter.value = value;
  }

  function remember(box: string, path: string) {
    const bucket = recents.value[box] || [];
    const normalized = path || "/";
    const existing = bucket.filter((item) => item !== normalized);
    const next = [normalized, ...existing].slice(0, 8);
    recents.value[box] = next;
  }

  function recentForBox(box: string | null): string[] {
    if (!box) return [];
    return recents.value[box] || [];
  }

  return {
    listing,
    loading,
    error,
    filter,
    setFilter,
    recentForBox,
    load,
  };
});
