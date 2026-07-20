<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import { useI18n } from "@/i18n";

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{ "update:show": [value: boolean] }>();
const { t } = useI18n();

const shortcuts = [
  { key: "Tab", action: "Switch active pane" },
  { key: "↑ / ↓", action: "Navigate files" },
  { key: "Enter", action: "Open directory / preview file" },
  { key: "Backspace", action: "Go to parent directory" },
  { key: "Home", action: "Go to home (~)" },
  { key: "Space", action: "Toggle select file" },
  { key: "*", action: "Select all / deselect all" },
  { key: "Shift+Click", action: "Range select" },
  { key: "Ctrl+Click", action: "Toggle select" },
  { key: "F1", action: "This help" },
  { key: "F3", action: "Compare files between panes" },
  { key: "F4", action: "Edit file in CodeMirror editor" },
  { key: "F5", action: "Copy to other pane" },
  { key: "F6", action: "Move to other pane" },
  { key: "F7", action: "Create new directory" },
  { key: "F8", action: "Delete selected files" },
  { key: "F10", action: "Quit (go back)" },
  { key: "Ctrl+F", action: "Search/filter files in pane" },
  { key: "Ctrl+G", action: "Git log / branch picker" },
  { key: "Ctrl+B", action: "Git blame for cursor file" },
  { key: "Escape", action: "Close overlay / clear filter" },
];

function close() { emit("update:show", false); }

function handleKeydown(event: KeyboardEvent) {
  if (!props.show) return;
  if (event.key === "Escape" || event.key === "F1") {
    event.preventDefault();
    event.stopPropagation();
    close();
  }
}

watch(() => props.show, (showing) => {
  if (showing) document.addEventListener("keydown", handleKeydown, true);
  else document.removeEventListener("keydown", handleKeydown, true);
}, { immediate: true });

onUnmounted(() => document.removeEventListener("keydown", handleKeydown, true));
</script>

<template>
  <div v-if="show" class="help-overlay" @click.self="close">
    <div class="help-panel">
      <div class="help-header">
        <span class="help-title">{{ t("commander.help_title") }}</span>
        <button class="help-close" @click="close">×</button>
      </div>
      <div class="help-body">
        <div v-for="s in shortcuts" :key="s.key" class="help-row">
          <span class="help-key">{{ s.key }}</span>
          <span class="help-action">{{ s.action }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.help-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
  font-family: var(--font-mono);
}
.help-panel {
  width: 440px; max-width: 90vw; max-height: 80vh;
  background: #0d0d14; border: 1px solid #1a1a24; border-radius: 6px;
  display: flex; flex-direction: column; overflow: hidden;
}
.help-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 16px; border-bottom: 1px solid #1a1a24;
  font-size: 14px; font-weight: 600; color: #e0e0e6;
}
.help-close {
  background: none; border: none; color: #f87171; cursor: pointer;
  font-size: 18px; font-family: var(--font-mono); padding: 2px 6px;
}
.help-body { overflow-y: auto; padding: 8px 0; }
.help-row {
  display: flex; padding: 4px 16px; font-size: 12px; color: #d4d4d8;
}
.help-row:hover { background: #111118; }
.help-key {
  width: 120px; flex-shrink: 0; color: #a78bfa; font-weight: 600;
}
.help-action { flex: 1; color: #aaa; }
</style>
