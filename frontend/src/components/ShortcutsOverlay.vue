<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { Component } from "vue";

import { NButton, NIcon, NList, NListItem, NModal } from "naive-ui";
import { PhKeyboard, PhMagnifyingGlass } from "@phosphor-icons/vue";

import { useI18n } from "@/i18n";

export interface NavShortcutLink {
  to: string;
  label: string;
  icon: Component;
  shortcut: string;
}

const props = defineProps<{ links: NavShortcutLink[] }>();

const { t } = useI18n();
const show = ref(false);

// Single source of truth: every entry is derived from AppHeader's own `links`
// table (passed in as a prop) plus the one shortcut ShortcutsOverlay itself
// owns (Cmd/Ctrl+K for the command palette). This is the only place the full
// shortcut list is assembled, so it cannot silently drift out of sync with
// what AppHeader actually implements.
const shortcuts = computed(() => [
  { label: t("shortcuts.command_palette"), combo: "Cmd/Ctrl + K", icon: PhMagnifyingGlass },
  ...props.links.map((link) => ({
    label: link.label,
    combo: link.shortcut.replace(/\+/g, " + "),
    icon: link.icon,
  })),
]);

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

// NOTE: navigation shortcuts (Alt+F, Alt+T, etc.) are handled exclusively by
// AppHeader.handleKeydown. This component only owns the "open this overlay"
// shortcut — it must never re-implement navigation, or both handlers fire.
function onKeydown(e: KeyboardEvent) {
  if (isTypingTarget(e.target)) return;
  const key = e.key.toLowerCase();
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
    <NIcon size="18"><PhKeyboard weight="duotone" /></NIcon>
  </NButton>
  <NModal v-model:show="show" preset="card" style="max-width: 480px">
    <template #header>
      <div class="title">
        <NIcon size="18"><PhKeyboard weight="duotone" /></NIcon>
        <span>{{ t("shortcuts.title") }}</span>
      </div>
    </template>
    <NList>
      <NListItem v-for="item in shortcuts" :key="item.label" class="row">
        <NIcon size="16"><component :is="item.icon" weight="duotone" /></NIcon>
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
