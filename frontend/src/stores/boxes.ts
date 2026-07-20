import { ref } from "vue";

import { defineStore } from "pinia";

import { fetchBoxes } from "@/api/http";
import type { ApiBox } from "@/api/types";

export const useBoxesStore = defineStore("boxes", () => {
  const items = ref<ApiBox[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function load(token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      items.value = await fetchBoxes(token);
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  function setBoxes(newItems: ApiBox[]) {
    items.value = newItems;
  }

  return {
    items,
    loading,
    error,
    load,
    setBoxes,
  };
});
