import { ref, shallowRef } from "vue";
import { defineStore } from "pinia";

import type { PingEvent } from "@/api/types";

const RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];

function buildPingWsUrl(token: string | null): string {
  const proto = typeof location !== "undefined" && location.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof location !== "undefined" ? location.host : "localhost";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}//${host}/ws/ping${query}`;
}

export const usePingStore = defineStore("ping", () => {
  const connected = ref(false);
  const connecting = ref(false);
  const lastError = ref<string | null>(null);

  // Queue of received pings — App.vue watches this and drains it using
  // useNotification(). Using shallowRef so the watch triggers on reference
  // change (when we replace the array), not on internal mutation.
  const pendingPings = shallowRef<PingEvent[]>([]);

  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let backoffIndex = 0;
  let intentionalDisconnect = false;
  let lastToken: string | null = null;

  function drainPings(): PingEvent[] {
    const all = pendingPings.value;
    if (all.length === 0) return all;
    pendingPings.value = [];
    return all;
  }

  function _handleEvent(event: PingEvent): void {
    if (event.type === "ping") {
      pendingPings.value = [...pendingPings.value, event];
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
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    intentionalDisconnect = false;
    lastToken = token;
    connecting.value = true;
    lastError.value = null;
    const ws = new WebSocket(buildPingWsUrl(token));
    socket = ws;

    ws.onopen = () => {
      connected.value = true;
      connecting.value = false;
      backoffIndex = 0;
    };

    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as PingEvent;
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

  function _injectEventForTest(event: PingEvent): void {
    _handleEvent(event);
  }

  return {
    connected,
    connecting,
    lastError,
    pendingPings,
    drainPings,
    connect,
    disconnect,
    _injectEventForTest,
  };
});
