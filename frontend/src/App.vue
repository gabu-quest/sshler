<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterView, useRoute } from "vue-router";

import {
  NConfigProvider,
  NGlobalStyle,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NMessageProvider,
  darkTheme,
  lightTheme,
} from "naive-ui";

import AppHeader from "@/components/AppHeader.vue";
import RecoveryModal from "@/components/RecoveryModal.vue";
import { useAppStore } from "@/stores/app";
import { useBootstrapStore } from "@/stores/bootstrap";
import { lightThemeOverrides, darkThemeOverrides } from "@/config/naive-theme";
import { fetchRecovery } from "@/api/http";
import type { LostSession } from "@/api/types";

const appStore = useAppStore();
const bootstrapStore = useBootstrapStore();
const route = useRoute();
const theme = computed(() => (appStore.isDark ? darkTheme : lightTheme));
const themeOverrides = computed(() =>
  appStore.isDark ? darkThemeOverrides : lightThemeOverrides
);
const isTerminalRoute = computed(() => route.path === '/terminal' || route.path === '/multi-terminal');

const showRecovery = ref(false);
const recoverySessions = ref<LostSession[]>([]);
const recoveryToken = computed(() => bootstrapStore.token);

onMounted(async () => {
  // Wait for bootstrap to complete before checking recovery
  if (!bootstrapStore.payload) {
    await bootstrapStore.bootstrap();
  }
  try {
    const sessions = await fetchRecovery(bootstrapStore.token);
    if (sessions.length > 0) {
      recoverySessions.value = sessions;
      showRecovery.value = true;
    }
  } catch {
    // Recovery check is best-effort, don't block app startup
  }
});
</script>

<template>
  <NConfigProvider :theme="theme" :theme-overrides="themeOverrides">
    <NGlobalStyle />
    <NMessageProvider>
      <NLayout class="app-layout">
        <NLayoutHeader bordered>
          <AppHeader />
        </NLayoutHeader>
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
        @update:show="showRecovery = $event"
        @updated="recoverySessions = $event"
      />
    </NMessageProvider>
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
