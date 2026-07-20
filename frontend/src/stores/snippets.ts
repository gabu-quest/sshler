import { ref } from "vue";
import { defineStore } from "pinia";
import {
  fetchSnippets,
  createSnippet as apiCreate,
  updateSnippet as apiUpdate,
  deleteSnippet as apiDelete,
} from "@/api/http";
import type { ApiSnippet } from "@/api/types";

export const useSnippetsStore = defineStore("snippets", () => {
  const items = ref<ApiSnippet[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const loadedBox = ref<string | null>(null);

  async function load(box: string, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      items.value = await fetchSnippets(box, token);
      loadedBox.value = box;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  async function create(
    box: string,
    label: string,
    command: string,
    category: string,
    token: string | null,
  ) {
    error.value = null;
    try {
      const snippet = await apiCreate(box, label, command, category, token);
      items.value.push(snippet);
      return snippet;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return null;
    }
  }

  async function update(
    snippetId: string,
    data: { label?: string; command?: string; category?: string; sort_order?: number },
    token: string | null,
  ) {
    error.value = null;
    try {
      const updated = await apiUpdate(snippetId, data, token);
      const idx = items.value.findIndex((s) => s.id === snippetId);
      if (idx !== -1) items.value[idx] = updated;
      return updated;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return null;
    }
  }

  async function remove(snippetId: string, token: string | null) {
    error.value = null;
    try {
      await apiDelete(snippetId, token);
      items.value = items.value.filter((s) => s.id !== snippetId);
      return true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return false;
    }
  }

  return { items, loading, error, loadedBox, load, create, update, remove };
});
