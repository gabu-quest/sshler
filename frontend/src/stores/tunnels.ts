import { ref } from "vue";
import { defineStore } from "pinia";
import {
  fetchTunnels,
  createTunnel as apiCreate,
  deleteTunnel as apiDelete,
} from "@/api/http";
import type { ApiTunnel } from "@/api/types";

export const useTunnelsStore = defineStore("tunnels", () => {
  const items = ref<ApiTunnel[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const loadedBox = ref<string | null>(null);

  async function load(box: string, token: string | null) {
    loading.value = true;
    error.value = null;
    try {
      items.value = await fetchTunnels(box, token);
      loadedBox.value = box;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  async function create(
    box: string,
    data: {
      tunnel_type: string;
      local_host: string;
      local_port: number;
      remote_host: string;
      remote_port: number;
    },
    token: string | null,
  ) {
    error.value = null;
    try {
      const tunnel = await apiCreate(box, data, token);
      items.value.push(tunnel);
      return tunnel;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return null;
    }
  }

  async function remove(box: string, tunnelId: string, token: string | null) {
    error.value = null;
    try {
      await apiDelete(box, tunnelId, token);
      items.value = items.value.filter((t) => t.id !== tunnelId);
      return true;
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
      return false;
    }
  }

  return { items, loading, error, loadedBox, load, create, remove };
});
