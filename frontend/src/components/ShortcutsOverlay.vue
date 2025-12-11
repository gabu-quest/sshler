<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import { NButton, NIcon, NList, NListItem, NModal } from "naive-ui";
import { PhKeyboard, PhLightning, PhMagnifyingGlass, PhTerminal } from "@phosphor-icons/vue";

import { useRouter } from "vue-router";

const router = useRouter();
const show = ref(false);

const shortcuts = [
  { label: "Command Palette", combo: "Cmd/Ctrl + K", icon: PhMagnifyingGlass },
  { label: "Files", combo: "Alt + F", icon: PhLightning },
  { label: "Terminal", combo: "Alt + T", icon: PhTerminal },
];

function onKeydown(e: KeyboardEvent) {
  const key = e.key.toLowerCase();
  if (e.altKey && key === "f") {
    e.preventDefault();
    router.push("/files");
  }
  if (e.altKey && key === "t") {
    e.preventDefault();
    router.push("/terminal");
  }
  if (e.shiftKey && key === "/") {
    e.preventDefault();
    show.value = true;
  }
}

onMounted(() => document.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <NButton quaternary circle size="small" @click="show = true">
    <NIcon size="18"><PhKeyboard /></NIcon>
  </NButton>
  <NModal v-model:show="show" preset="card" style="max-width: 480px">
    <template #header>
      <div class="title">
        <NIcon size="18"><PhKeyboard /></NIcon>
        <span>Shortcuts</span>
      </div>
    </template>
    <NList>
      <NListItem v-for="item in shortcuts" :key="item.label" class="row">
        <NIcon size="16"><component :is="item.icon" /></NIcon>
        <span class="label">{{ item.label }}</span>
        <span class="combo">{{ item.combo }}</span>
      </NListItem>
    </NList>
  </NModal>
</template>

<style scoped>
.title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 8px;
  align-items: center;
}
.combo {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
}
.label {
  text-transform: capitalize;
}
</style>
