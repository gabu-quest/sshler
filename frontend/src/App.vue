<script setup lang="ts">
import { computed } from "vue";
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
import { useAppStore } from "@/stores/app";

const appStore = useAppStore();
const route = useRoute();
const theme = computed(() => (appStore.isDark ? darkTheme : lightTheme));
const isTerminalRoute = computed(() => route.path === '/terminal' || route.path === '/multi-terminal');
</script>

<template>
  <NConfigProvider :theme="theme">
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
    height: calc(var(--vh-full, 100vh) - 40px);
  }

  .app-content {
    padding: 8px;
  }

  .app-content.terminal-mode {
    padding: 0;
  }
}

@media (max-width: 480px) {
  .app-main {
    height: calc(var(--vh-full, 100vh) - 34px);
  }

  .app-content {
    padding: 4px;
  }

  .app-content.terminal-mode {
    padding: 0;
  }
}
</style>
