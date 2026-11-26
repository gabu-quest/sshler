<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, type CSSProperties } from "vue";
import { RouterView, useRoute } from "vue-router";

import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NLayout,
  NLayoutContent,
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
import { useResponsive } from "@/composables/useResponsive";
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
const TERMINAL_PATHS = new Set(['/terminal', '/multi-terminal', '/commander']);
const isTerminalRoute = computed(() => TERMINAL_PATHS.has(route.path));
// A view that holds a terminal fades by opacity alone: a transform on xterm's box
// would move it under the cursor and its measurement.
const routeTransition = (path: string) => (TERMINAL_PATHS.has(path) ? 'route-fade-plain' : 'route-fade');

// Desktop (768 px and wider): the side rail on the left of every view, no top bar.
// Phone: a bar on top. The sign-in page has neither. The width is read at once, so a
// phone never draws the rail first.
const { width, breakpoints } = useResponsive();
// The page mounts before the router has read the address (its first route matches
// nothing): no navigation is drawn until it has, so the sign-in page never flashes the
// rail and a phone's terminal never flashes the 36 px bar.
const showChrome = computed(() => route.matched.length > 0 && route.path !== '/login');
const railShell = computed(() => showChrome.value && width.value >= breakpoints.tablet);
const frameClass = computed(() => `app-frame ${railShell.value ? 'app-frame--rail' : 'app-frame--bar'}`);
// The main area fills what the header and the progress strip leave. A terminal route
// sizes itself to that area and never scrolls the page; every other view scrolls in it.
// Naive applies this to the area's own scroll container.
const mainScrollStyle = computed<CSSProperties>(() => ({
  minHeight: '0px',
  overflowY: isTerminalRoute.value ? 'hidden' : 'auto',
  overflowX: 'hidden',
}));

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
    // Silent on purpose: this check runs again in fifteen seconds, and the modal opens
    // only for lost sessions it found.
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
  // Everything below needs a session: the sign-in page starts none of it. A good
  // sign-in loads the page afresh, so it starts then, with the token.
  if (!bootstrapStore.signedIn) return;
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
      <NLayout class="app-layout" :content-class="frameClass">
        <AppHeader v-if="showChrome" />
        <div class="app-stage">
          <div class="app-frame-fixed">
            <ProgressStrip />
          </div>
          <NLayoutContent class="app-main" :content-style="mainScrollStyle">
            <div class="app-content" :class="{ 'terminal-mode': isTerminalRoute }">
              <!-- Keyed by path: a change of ?box= or ?dir= alone keeps the view. -->
              <RouterView v-slot="{ Component, route: viewRoute }">
                <Transition :name="routeTransition(viewRoute.path)" mode="out-in">
                  <component :is="Component" :key="viewRoute.path" />
                </Transition>
              </RouterView>
            </div>
          </NLayoutContent>
        </div>
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
/* The page frame is the height of the window. On a desktop it is a row: the side rail,
   then the stage. On a phone it is a column: the bar, then the stage. The stage is a
   column: the progress strip keeps its own height (none while it is empty), the main
   area takes the rest. */
.app-layout {
  height: var(--vh-full, 100vh);
}

.app-layout :deep(.app-frame) {
  display: flex;
  height: 100%;
}

.app-layout :deep(.app-frame--rail) {
  flex-direction: row;
}

.app-layout :deep(.app-frame--bar) {
  flex-direction: column;
}

.app-stage {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.app-frame-fixed {
  flex: 0 0 auto;
}

.app-main {
  flex: 1 1 auto;
  min-height: 0;
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

@media (max-width: 767px) {
  .app-content {
    padding: 8px;
  }

  .app-content.terminal-mode {
    padding: 0;
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
