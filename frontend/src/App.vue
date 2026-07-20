<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterView, useRoute } from "vue-router";

import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLoadingBarProvider,
  NMessageProvider,
  NNotificationProvider,
  darkTheme,
  lightTheme,
} from "naive-ui";

import AppHeader from "@/components/AppHeader.vue";
import PingNotificationHandler from "@/components/PingNotificationHandler.vue";
import ProgressStrip from "@/components/ProgressStrip.vue";
import RecoveryModal from "@/components/RecoveryModal.vue";
import { useAppStore } from "@/stores/app";
import { useBootstrapStore } from "@/stores/bootstrap";
import { usePingStore } from "@/stores/ping";
import { useProgressStore } from "@/stores/progress";
import { lightThemeOverrides, darkThemeOverrides } from "@/config/naive-theme";
import { fetchRecovery } from "@/api/http";
import type { LostSession } from "@/api/types";

const appStore = useAppStore();
const bootstrapStore = useBootstrapStore();
const pingStore = usePingStore();
const progressStore = useProgressStore();
const route = useRoute();
const theme = computed(() => (appStore.isDark ? darkTheme : lightTheme));
const themeOverrides = computed(() =>
  appStore.isDark ? darkThemeOverrides : lightThemeOverrides
);
const isTerminalRoute = computed(() => route.path === '/terminal' || route.path === '/multi-terminal' || route.path === '/commander');

const showRecovery = ref(false);
const recoverySessions = ref<LostSession[]>([]);
const recoveryToken = computed(() => bootstrapStore.token);
// Session IDs the user closed the modal on — don't reopen for these
const dismissedRecoveryIds = ref<Set<string>>(new Set());

let recoveryPollInterval: number | null = null;

async function checkRecovery() {
  try {
    const sessions = await fetchRecovery(bootstrapStore.token);
    if (showRecovery.value) return; // Don't touch state while modal is open
    // Only show modal for sessions the user hasn't already dismissed
    const unseen = sessions.filter(s => !dismissedRecoveryIds.value.has(s.id));
    if (unseen.length > 0) {
      recoverySessions.value = unseen;
      showRecovery.value = true;
    }
  } catch {
    // Best-effort
  }
}

function handleRecoveryClose(value: boolean) {
  showRecovery.value = value;
  if (!value && recoverySessions.value.length > 0) {
    // User closed modal — suppress these IDs so the poll doesn't reopen
    for (const s of recoverySessions.value) {
      dismissedRecoveryIds.value.add(s.id);
    }
  }
}

// Only poll when tab is visible — background tabs waste the browser's
// 6-connection-per-origin limit, starving active tabs.
function startRecoveryPolling() {
  if (recoveryPollInterval) return;
  checkRecovery();
  recoveryPollInterval = window.setInterval(checkRecovery, 15000);
}

function stopRecoveryPolling() {
  if (recoveryPollInterval) {
    clearInterval(recoveryPollInterval);
    recoveryPollInterval = null;
  }
}

function handleVisibility() {
  if (document.hidden) {
    stopRecoveryPolling();
  } else {
    startRecoveryPolling();
  }
}

onMounted(async () => {
  if (!bootstrapStore.payload) {
    await bootstrapStore.bootstrap();
  }
  // The progress WebSocket lives at the app level so the header strip stays
  // live on every route (the strip itself mounts/unmounts with box context).
  progressStore.connect(bootstrapStore.token);
  progressStore.refresh(bootstrapStore.token).catch(() => {
    /* WS snapshot will fill us in soon enough */
  });
  pingStore.connect(bootstrapStore.token);
  document.addEventListener('visibilitychange', handleVisibility);
  if (!document.hidden) {
    startRecoveryPolling();
  }
});

onUnmounted(() => {
  progressStore.disconnect();
  pingStore.disconnect();
  stopRecoveryPolling();
  document.removeEventListener('visibilitychange', handleVisibility);
});
</script>

<template>
  <NConfigProvider :theme="theme" :theme-overrides="themeOverrides">
    <NGlobalStyle />
    <NLoadingBarProvider>
    <NDialogProvider>
    <NNotificationProvider placement="top-right" :max="5">
    <NMessageProvider>
      <PingNotificationHandler />
      <NLayout class="app-layout">
        <NLayoutHeader bordered>
          <AppHeader />
        </NLayoutHeader>
        <ProgressStrip />
        <NLayoutContent class="app-main">
          <div class="app-content" :class="{ 'terminal-mode': isTerminalRoute }">
            <RouterView />
          </div>
        </NLayoutContent>
      </NLayout>
      <RecoveryModal
        :show="showRecovery"
        :sessions="recoverySessions"
        :token="recoveryToken"
        @update:show="handleRecoveryClose"
        @updated="(sessions: LostSession[]) => { recoverySessions = sessions; dismissedRecoveryIds = new Set(); }"
      />
    </NMessageProvider>
    </NNotificationProvider>
    </NDialogProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>

<style scoped>
.app-layout {
  height: var(--vh-full, 100vh);
}

.app-main {
  height: calc(var(--vh-full, 100vh) - 64px);
  overflow: auto;
}

.app-content {
  padding: 16px;
  max-width: 1400px;
  margin: 0 auto;
}

.app-content.terminal-mode {
  padding: 0;
  max-width: none;
  height: 100%;
}

@media (max-width: 768px) {
  .app-main {
    height: calc(var(--vh-full, 100vh) - 16px);
    overflow: hidden;
  }

  .app-content {
    padding: 8px;
  }

  .app-content.terminal-mode {
    padding: 0;
    height: 100%;
    overflow: hidden;
  }
}

@media (max-width: 480px) {
  .app-content {
    padding: 4px;
  }

  .app-content.terminal-mode {
    padding: 0;
  }
}
</style>
