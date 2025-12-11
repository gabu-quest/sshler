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
          <RouterView />
        </NLayoutContent>
      </NLayout>
    </NMessageProvider>
  </NConfigProvider>
</template>
