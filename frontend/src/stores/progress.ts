import { computed, ref } from "vue";
import { defineStore } from "pinia";

import {
  deleteProgress as apiDeleteProgress,
  fetchProgress as apiFetchProgress,
} from "@/api/http";
import type { ProgressBar, ProgressEvent } from "@/api/types";
import { useAppStore } from "@/stores/app";

const SUBSCRIBED_KEY = "sshler:progress:subscribed";
const STALE_THRESHOLD_SEC = 300;
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];

// Subscriptions are scoped per box: { "<box>": ["name1", "name2"], ... }.
// The old global shape was a flat string[]; if we see that on read we drop it
// (single-user tool — re-subscribing once on upgrade is acceptable).
function readStoredSubscriptions(): Record<string, string[]> {
  if (typeof localStorage === "undefined") return {};
  try {
    const raw = localStorage.getItem(SUBSCRIBED_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      // Old global shape — discard.
      return {};
    }
    if (parsed && typeof parsed === "object") {
      const out: Record<string, string[]> = {};
      for (const [scope, names] of Object.entries(parsed)) {
        if (Array.isArray(names)) {
          out[scope] = names.filter((x): x is string => typeof x === "string");
        }
      }
      return out;
    }
  } catch {
    /* ignore corrupt localStorage */
  }
  return {};
}

function writeStoredSubscriptions(byScope: Record<string, string[]>): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(SUBSCRIBED_KEY, JSON.stringify(byScope));
  } catch {
    /* quota / private mode — ignore */
  }
}

function buildProgressWsUrl(token: string | null): string {
  const proto = typeof location !== "undefined" && location.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof location !== "undefined" ? location.host : "localhost";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${host}/ws/progress${query}`;
}

export const useProgressStore = defineStore("progress", () => {
  const appStore = useAppStore();

  // ---- state ----
  const bars = ref<Record<string, ProgressBar>>({});
  const subscriptionsByScope = ref<Record<string, string[]>>(readStoredSubscriptions());
  const connected = ref(false);
  const connecting = ref(false);
  const lastError = ref<string | null>(null);

  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let backoffIndex = 0;
  let intentionalDisconnect = false;
  let lastToken: string | null = null;

  // The current subscription scope is the active box. Null on routes with no
  // box context (Settings, /app/progress) — subscribe/unsubscribe are no-ops there.
  const currentScope = computed<string | null>(() => appStore.activeBox);

  // ---- computed ----
  const allBars = computed<ProgressBar[]>(() =>
    Object.values(bars.value).sort((a, b) => b.updated_at - a.updated_at),
  );

  const subscribedNames = computed<string[]>(() => {
    const scope = currentScope.value;
    if (scope === null) return [];
    return subscriptionsByScope.value[scope] ?? [];
  });

  const subscribedBars = computed<ProgressBar[]>(() => {
    const names = new Set(subscribedNames.value);
    return allBars.value.filter((b) => names.has(b.name));
  });

  function isStale(bar: ProgressBar, thresholdSec = STALE_THRESHOLD_SEC): boolean {
    return Date.now() / 1000 - bar.updated_at > thresholdSec;
  }

  // ---- subscription actions (operate on currentScope) ----
  function isSubscribed(name: string): boolean {
    const scope = currentScope.value;
    if (scope === null) return false;
    return (subscriptionsByScope.value[scope] ?? []).includes(name);
  }

  function subscribe(name: string): void {
    const scope = currentScope.value;
    if (scope === null) return;
    const existing = subscriptionsByScope.value[scope] ?? [];
    if (existing.includes(name)) return;
    const next = { ...subscriptionsByScope.value, [scope]: [...existing, name] };
    subscriptionsByScope.value = next;
    writeStoredSubscriptions(next);
  }

  function unsubscribe(name: string): void {
    const scope = currentScope.value;
    if (scope === null) return;
    const existing = subscriptionsByScope.value[scope] ?? [];
    if (!existing.includes(name)) return;
    const next = {
      ...subscriptionsByScope.value,
      [scope]: existing.filter((n) => n !== name),
    };
    subscriptionsByScope.value = next;
    writeStoredSubscriptions(next);
  }

  // ---- REST actions ----
  async function refresh(token: string | null): Promise<void> {
    const payload = await apiFetchProgress(token);
    const next: Record<string, ProgressBar> = {};
    for (const bar of payload.bars) {
      next[bar.name] = bar;
    }
    bars.value = next;
  }

  async function remove(name: string, token: string | null): Promise<boolean> {
    const result = await apiDeleteProgress(name, token);
    if (result.removed) {
      const next = { ...bars.value };
      delete next[name];
      bars.value = next;
    }
    return result.removed;
  }

  // ---- WS lifecycle ----
  function _handleEvent(event: ProgressEvent): void {
    if (event.type === "snapshot") {
      const next: Record<string, ProgressBar> = {};
      for (const bar of event.bars) {
        next[bar.name] = bar;
      }
      bars.value = next;
      return;
    }
    if (event.type === "upsert") {
      bars.value = { ...bars.value, [event.name]: event.bar };
      return;
    }
    if (event.type === "delete") {
      if (event.name in bars.value) {
        const next = { ...bars.value };
        delete next[event.name];
        bars.value = next;
      }
      return;
    }
  }

  function _scheduleReconnect(tokenProvider: () => string | null): void {
    if (intentionalDisconnect) return;
    if (reconnectTimer !== null) return;
    const delay = RECONNECT_BACKOFF_MS[Math.min(backoffIndex, RECONNECT_BACKOFF_MS.length - 1)];
    backoffIndex += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect(tokenProvider());
    }, delay);
  }

  function connect(token: string | null): void {
    // Idempotent: if already open or connecting to the same socket, no-op.
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    intentionalDisconnect = false;
    lastToken = token;
    connecting.value = true;
    lastError.value = null;
    const ws = new WebSocket(buildProgressWsUrl(token));
    socket = ws;

    ws.onopen = () => {
      connected.value = true;
      connecting.value = false;
      backoffIndex = 0;
    };

    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as ProgressEvent;
        _handleEvent(parsed);
      } catch (err) {
        lastError.value = `bad ws payload: ${err instanceof Error ? err.message : String(err)}`;
      }
    };

    ws.onerror = () => {
      lastError.value = "websocket error";
    };

    ws.onclose = () => {
      connected.value = false;
      connecting.value = false;
      if (socket === ws) socket = null;
      _scheduleReconnect(() => lastToken);
    };
  }

  function disconnect(): void {
    intentionalDisconnect = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      try {
        socket.close();
      } catch {
        /* already closing */
      }
      socket = null;
    }
    connected.value = false;
    connecting.value = false;
    backoffIndex = 0;
  }

  // Test-only escape hatch: synchronously inject an event so unit tests can
  // assert reducer behavior without touching the WS machinery.
  function _injectEventForTest(event: ProgressEvent): void {
    _handleEvent(event);
  }

  return {
    bars,
    subscriptionsByScope,
    currentScope,
    subscribedNames,
    connected,
    connecting,
    lastError,
    allBars,
    subscribedBars,
    isStale,
    isSubscribed,
    subscribe,
    unsubscribe,
    refresh,
    remove,
    connect,
    disconnect,
    _injectEventForTest,
  };
});
