<script setup lang="ts">
import { computed } from "vue";
import { RouterView } from "vue-router";

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
const theme = computed(() => (appStore.isDark ? darkTheme : lightTheme));
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
          <div class="app-content">
            <RouterView />
          </div>
        </NLayoutContent>
      </NLayout>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style scoped>
.app-layout {
  height: 100vh;
}

.app-main {
  height: calc(100vh - 64px);
  overflow: auto;
}

.app-content {
  padding: 16px;
  max-width: 1200px;
  margin: 0 auto;
}

@media (max-width: 768px) {
  .app-content {
    padding: 8px;
  }
}
</style>
