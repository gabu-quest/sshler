<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import { NAlert, NButton, NCard, NDivider, NIcon, NList, NListItem, NSelect, NSpin, NTag, useMessage } from "naive-ui";
import { PhLightning, PhPulse } from "@phosphor-icons/vue";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { fetchSessions, fetchTerminalHandshake } from "@/api/http";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useBoxesStore } from "@/stores/boxes";

const bootstrapStore = useBootstrapStore();
const boxesStore = useBoxesStore();
const handshake = ref<string | null>(null);
const sessions = ref<string[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const termEl = ref<HTMLElement | null>(null);
const terminal = ref<Terminal | null>(null);
const wsRef = ref<WebSocket | null>(null);
const selectedBox = ref<string | null>(null);
const message = useMessage();

const tokenValue = () => bootstrapStore.token || bootstrapStore.payload?.token || null;

async function loadTerminalMeta() {
  loading.value = true;
  error.value = null;
  try {
    if (!bootstrapStore.payload && !bootstrapStore.loading) {
      await bootstrapStore.bootstrap();
    }
    if (!boxesStore.items.length && !boxesStore.loading) {
      await boxesStore.load(tokenValue());
    }
    if (!selectedBox.value && boxesStore.items.length) {
      const first = boxesStore.items[0];
      if (first) {
        selectedBox.value = first.name;
      }
    }
    const token = tokenValue();
    const [hs, sess] = await Promise.all([
      fetchTerminalHandshake(token),
      fetchSessions(token),
    ]);
    handshake.value = hs.ws_url;
    sessions.value = sess.sessions;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function connectTerminal() {
  if (!handshake.value || !termEl.value || !selectedBox.value) return;
  if (terminal.value) {
    terminal.value.dispose();
  }
  if (wsRef.value) {
    wsRef.value.close();
  }
  const term = new Terminal({ convertEol: true, fontSize: 14 });
  terminal.value = term;
  term.open(termEl.value);

  const token = tokenValue();
  const url = new URL(handshake.value);
  url.searchParams.set("host", selectedBox.value);
  url.searchParams.set("dir", "/");
  url.searchParams.set("session", "spa");
  url.searchParams.set("cols", String(term.cols || 120));
  url.searchParams.set("rows", String(term.rows || 32));
  if (token) {
    url.searchParams.set("token", token);
  }
  const ws = new WebSocket(url.toString());
  wsRef.value = ws;

  ws.onopen = () => {
    message.success("connected");
  };
  ws.onmessage = (event) => {
    const data = event.data;
    if (typeof data === "string") {
      term.write(data);
    } else if (data instanceof Blob) {
      data.text().then((text) => term.write(text));
    }
  };
  ws.onclose = () => {
    message.warning("disconnected");
  };
  term.onData((data) => {
    ws.send(data);
  });
  const resizeHandler = () => {
    ws.send(JSON.stringify({ op: "resize", cols: term.cols, rows: term.rows }));
  };
  window.addEventListener("resize", resizeHandler);
  ws.onclose = () => {
    message.warning("disconnected");
    window.removeEventListener("resize", resizeHandler);
  };
}

onMounted(() => {
  loadTerminalMeta();
});

onBeforeUnmount(() => {
  wsRef.value?.close();
  terminal.value?.dispose();
});
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <p class="eyebrow">terminal</p>
        <h1>WebSocket + tmux bridge</h1>
        <p class="text-muted">
          this view will host xterm and the multi-session layout; we keep the resize throttling and mobile keyboard
          handling that shipped in the legacy ui
        </p>
      </div>
      <NTag type="warning" round>pending api wiring</NTag>
    </header>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18">
          <PhLightning />
        </NIcon>
        <span>control channel requirements</span>
      </div>
      <NList class="list">
        <NListItem>websocket at /ws/term (or /api/v1/ws/term) with token/basic auth headers</NListItem>
        <NListItem>message shapes: resize, select-window, bell/notify, heartbeat</NListItem>
        <NListItem>respect terminal resize throttling and dimension-delta guard to avoid resize storms</NListItem>
        <NListItem>mobile: orientation + visualViewport resize reflow must stay intact</NListItem>
      </NList>
    </NCard>

    <NDivider />

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <NIcon size="18">
          <PhPulse />
        </NIcon>
        <span>session state</span>
      </div>
      <NList class="list">
        <NListItem>pinia store for sessions and panes; persist layouts in localStorage with versioned keys</NListItem>
        <NListItem>defer heavy assets (xterm, addons) with dynamic imports</NListItem>
        <NListItem>carry over notifications (bell + osc 777) and toasts</NListItem>
      </NList>
    </NCard>

    <NCard class="surface-card" size="medium">
      <div class="card-title">
        <span>handshake + sessions</span>
      </div>
      <div class="meta-grid">
        <div>
          <p class="label">ws url</p>
          <p class="value monospace">
            <NSpin v-if="loading" size="small" />
            <span v-else>{{ handshake || "n/a" }}</span>
          </p>
        </div>
        <div>
          <p class="label">sessions</p>
          <p class="value">{{ sessions.length }}</p>
        </div>
        <div>
          <p class="label">box</p>
          <NSelect
            v-model:value="selectedBox"
            :options="boxesStore.items.map((b) => ({ label: b.name, value: b.name }))"
            size="small"
          />
        </div>
      </div>
      <div class="terminal-shell">
        <div ref="termEl" class="term-host" />
        <NButton size="small" type="primary" @click="connectTerminal" :disabled="loading || !handshake">connect</NButton>
      </div>
      <NAlert v-if="error" type="error" closable class="mt">
        {{ error }}
      </NAlert>
      <NButton quaternary size="small" class="mt" @click="loadTerminalMeta">refresh</NButton>
    </NCard>

    <NAlert type="warning" closable>
      local tmux resize now uses refresh-client; keep that behavior when mapping resize events to the backend
    </NAlert>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

h1 {
  margin: 0 0 8px 0;
  font-size: 26px;
}

.surface-card {
  background: var(--surface);
  border-radius: 16px;
  border: 1px solid var(--stroke);
}

.card-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 8px;
}

.list {
  color: var(--muted);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  align-items: center;
}

.label {
  text-transform: uppercase;
  letter-spacing: 0.3px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

.value {
  margin: 0;
  font-weight: 600;
}

.monospace {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

.mt {
  margin-top: 12px;
}

.terminal-shell {
  margin-top: 12px;
  border: 1px solid var(--stroke);
  border-radius: 12px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.03);
}

.term-host {
  height: 320px;
}

@media (max-width: 800px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
