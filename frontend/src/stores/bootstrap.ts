import { computed, ref } from "vue";

import { defineStore } from "pinia";

import { fetchBootstrap } from "@/api/http";
import type { BootstrapPayload } from "@/api/types";

const TOKEN_STORAGE_KEY = "sshler:token";

export const useBootstrapStore = defineStore("bootstrap", () => {
  const payload = ref<BootstrapPayload | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const token = ref<string | null>(readStoredToken());
  const shortcuts = ref<string[]>(["Cmd/Ctrl+K: Command Palette", "Alt+F: Files", "Alt+T: Terminal", "Alt+M: Multi-Terminal"]);
  let refreshInterval: number | null = null;

  const tokenHeader = computed(() => payload.value?.token_header ?? "X-SSHLER-TOKEN");
  const version = computed(() => payload.value?.version ?? "");
  const spaBase = computed(() => payload.value?.spa_base ?? "/app/");
  const spaEnabled = computed(() => payload.value?.spa_enabled ?? true);
  const basicAuthRequired = computed(() => payload.value?.basic_auth_required ?? false);

  function readStoredToken(): string | null {
    if (typeof localStorage === "undefined") {
      return null;
    }
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  }

  function persistToken(value: string | null) {
    if (typeof localStorage === "undefined") {
      return;
    }
    if (value) {
      localStorage.setItem(TOKEN_STORAGE_KEY, value);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  }

  async function bootstrap() {
    loading.value = true;
    error.value = null;
    try {
      payload.value = await fetchBootstrap();
      // Always use the fresh token from server, ignore cached token
      if (payload.value.token) {
        token.value = payload.value.token;
        persistToken(token.value);
      }
      
      // Set up periodic refresh (every 30 minutes)
      if (refreshInterval) {
        clearInterval(refreshInterval);
      }
      refreshInterval = window.setInterval(() => {
        // Silently refresh token in background
        fetchBootstrap().then(newPayload => {
          if (newPayload.token) {
            token.value = newPayload.token;
            persistToken(token.value);
            console.log('Token refreshed automatically');
          }
        }).catch(err => {
          console.warn('Background token refresh failed:', err);
        });
      }, 30 * 60 * 1000); // 30 minutes
      
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err);
    } finally {
      loading.value = false;
    }
  }

  function cleanup() {
    if (refreshInterval) {
      clearInterval(refreshInterval);
      refreshInterval = null;
    }
  }

  async function refreshToken() {
    console.log('Refreshing token...');
    token.value = null;
    persistToken(null);
    await bootstrap();
    if (token.value) {
      console.log('Token refreshed successfully');
      // Don't reload the page automatically - let the app continue with new token
    }
  }

  function setToken(value: string | null) {
    token.value = value;
    persistToken(value);
  }

  return {
    payload,
    loading,
    error,
    token,
    tokenHeader,
    version,
    spaBase,
    spaEnabled,
    basicAuthRequired,
    bootstrap,
    refreshToken,
    setToken,
    shortcuts,
    cleanup,
  };
});
