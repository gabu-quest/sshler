import { errorText } from "@/api/apiError";
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
  // The bootstrap in flight: callers that arrive meanwhile wait for the same answer.
  let pending: Promise<void> | null = null;

  const tokenHeader = computed(() => payload.value?.token_header ?? "X-SSHLER-TOKEN");
  const version = computed(() => payload.value?.version ?? "");
  const spaBase = computed(() => payload.value?.spa_base ?? "/app/");
  const spaEnabled = computed(() => payload.value?.spa_enabled ?? true);
  // A password is set: the page is used signed in.
  const authRequired = computed(() => payload.value?.auth_required ?? false);
  // This page's requests carry a live session (true when no password is set).
  const authenticated = computed(() => payload.value?.authenticated ?? false);
  // The bootstrap answer is here and this page may call the server: no password is set,
  // or the answer says the page is signed in.
  const signedIn = computed(
    () => payload.value !== null && (!authRequired.value || authenticated.value),
  );
  const wslDistro = computed(() => payload.value?.wsl_distro ?? null);
  const pdfAvailable = computed(() => payload.value?.pdf_available ?? false);
  const platform = computed(() => payload.value?.platform ?? "posix");
  const isWindows = computed(() => platform.value === "windows");
  const windowsShells = computed(() => payload.value?.windows_shells ?? []);
  const defaultShell = computed(() => payload.value?.default_shell ?? null);

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

  /** Ask the server for the bootstrap; a call made while one is in flight shares it. */
  function bootstrap(): Promise<void> {
    if (!pending) {
      pending = load().finally(() => {
        pending = null;
      });
    }
    return pending;
  }

  async function load() {
    loading.value = true;
    error.value = null;
    try {
      payload.value = await fetchBootstrap();
      // Always use the fresh token from server, ignore cached token
      if (payload.value.token) {
        token.value = payload.value.token;
        persistToken(token.value);
      } else if (payload.value.auth_required && !payload.value.authenticated) {
        // Signed out: a token kept from an earlier session is not this page's.
        setToken(null);
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
          }
        }).catch(err => {
          // Silent on purpose: the page keeps its token, and a request it refuses
          // refreshes the token on its own.
          console.warn('Background token refresh failed:', err);
        });
      }, 30 * 60 * 1000); // 30 minutes

    } catch (err) {
      error.value = errorText(err);
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

  /** Forget the signed-in page: the bootstrap, the token (also the stored one) and its
   * refresh. The next navigation asks the server afresh. */
  function forget() {
    cleanup();
    payload.value = null;
    error.value = null;
    setToken(null);
  }

  async function refreshToken() {
    token.value = null;
    persistToken(null);
    await bootstrap();
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
    authRequired,
    authenticated,
    signedIn,
    wslDistro,
    pdfAvailable,
    platform,
    isWindows,
    windowsShells,
    defaultShell,
    bootstrap,
    refreshToken,
    setToken,
    forget,
    shortcuts,
    cleanup,
  };
});
