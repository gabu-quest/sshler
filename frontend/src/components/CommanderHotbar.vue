<script setup lang="ts">
import { computed } from "vue";

import { useI18n } from "@/i18n";

const emit = defineEmits<{ "action": [key: string] }>();

const { t } = useI18n();

const keys = computed(() => [
  { key: "F1", label: "Help" },
  { key: "F3", label: t("commander.compare") },
  { key: "F4", label: "Edit" },
  { key: "F5", label: t("commander.copy") + "→" },
  { key: "F6", label: t("commander.move") + "→" },
  { key: "F7", label: "Mkdir" },
  { key: "F8", label: "Delete" },
  { key: "F10", label: "Quit" },
]);
</script>

<template>
  <div class="hotbar">
    <div v-for="item in keys" :key="item.key" class="hotbar-key" @click="emit('action', item.key)">
      <span class="key-num">{{ item.key }}</span>
      <span class="key-label">{{ item.label }}</span>
    </div>
  </div>
</template>

<style scoped>
.hotbar {
  display: flex;
  height: 24px;
  background: #0d0d14;
  border-top: 1px solid #1a1a24;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 12px;
}

.hotbar-key {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;
}

.hotbar-key:hover {
  background: #111118;
}

.key-num {
  color: #fbbf24;
  font-weight: bold;
}

.key-label {
  color: #888;
}

@media (max-width: 767px) {
  .hotbar {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .hotbar::-webkit-scrollbar {
    display: none;
  }

  .hotbar-key {
    min-width: 60px;
    flex-shrink: 0;
  }
}
</style>
