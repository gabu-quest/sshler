<script setup lang="ts">
import { computed } from "vue";
import { NModal, NCard, NButton, NIcon, NEmpty, NCheckbox } from "naive-ui";
import {
  PhCheckCircle,
  PhCircleDashed,
  PhMinusCircle,
  PhX,
  PhXCircle,
} from "@phosphor-icons/vue";

import { useI18n } from "@/i18n";
import { useProgressStore } from "@/stores/progress";
import type { ProgressBar, ProgressStatus } from "@/api/types";

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{ (e: "update:show", value: boolean): void }>();

const { t } = useI18n();
const progressStore = useProgressStore();

const scope = computed(() => progressStore.currentScope ?? "");
const bars = computed<ProgressBar[]>(() => progressStore.allBars);
const isEmpty = computed(() => bars.value.length === 0);

const KNOWN_COLOR_MAP: Record<string, string> = {
  blue: "#3b82f6",
  green: "#22c55e",
  red: "#ef4444",
  yellow: "#eab308",
  orange: "#f97316",
  purple: "#a855f7",
  pink: "#ec4899",
  teal: "#14b8a6",
};

function resolveColor(bar: ProgressBar): string {
  if (!bar.color) return "var(--n-primary-color, #3b82f6)";
  if (bar.color.startsWith("#") || bar.color.startsWith("rgb")) return bar.color;
  return KNOWN_COLOR_MAP[bar.color.toLowerCase()] ?? bar.color;
}

const STATUS_ICONS: Record<ProgressStatus, ReturnType<typeof Object>> = {
  running: PhCircleDashed,
  done: PhCheckCircle,
  failed: PhXCircle,
  cancelled: PhMinusCircle,
};

function statusIcon(status: ProgressStatus) {
  return STATUS_ICONS[status] ?? PhCircleDashed;
}

function displayLabel(bar: ProgressBar): string {
  return bar.label && bar.label.trim() ? bar.label : bar.name;
}

function toggle(bar: ProgressBar, checked: boolean): void {
  if (checked) {
    progressStore.subscribe(bar.name);
  } else {
    progressStore.unsubscribe(bar.name);
  }
}

function close(): void {
  emit("update:show", false);
}
</script>

<template>
  <NModal
    :show="props.show"
    @update:show="(v: boolean) => { if (!v) close() }"
  >
    <NCard
      class="progress-picker"
      :title="t('progress.picker.title', { box: scope })"
      :bordered="false"
      size="small"
      role="dialog"
      aria-modal="true"
    >
      <template #header-extra>
        <NButton quaternary circle size="small" :aria-label="t('progress.picker.close')" @click="close">
          <template #icon>
            <NIcon :component="PhX" />
          </template>
        </NButton>
      </template>

      <NEmpty v-if="isEmpty" :description="t('progress.picker.empty')" />

      <ul v-else class="picker-list">
        <li v-for="bar in bars" :key="bar.name" class="picker-row">
          <NCheckbox
            :checked="progressStore.isSubscribed(bar.name)"
            @update:checked="(c: boolean) => toggle(bar, c)"
          />
          <span
            class="picker-row__stripe"
            :style="{ background: resolveColor(bar) }"
            aria-hidden="true"
          />
          <div class="picker-row__text">
            <span class="picker-row__label">{{ displayLabel(bar) }}</span>
            <span v-if="bar.label" class="picker-row__name">{{ bar.name }}</span>
          </div>
          <span class="picker-row__numbers">{{ bar.current }}/{{ bar.total }}</span>
          <NIcon
            :component="statusIcon(bar.status)"
            :class="`picker-row__pip--${bar.status}`"
            :title="bar.status"
          />
        </li>
      </ul>
    </NCard>
  </NModal>
</template>

<style scoped>
.progress-picker {
  width: 440px;
  max-width: 92vw;
}

.picker-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 60vh;
  overflow-y: auto;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
}
.picker-row:hover {
  background: var(--n-action-color, color-mix(in srgb, currentColor 8%, transparent));
}

.picker-row__stripe {
  width: 3px;
  height: 20px;
  border-radius: 2px;
  flex-shrink: 0;
}

.picker-row__text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.picker-row__label {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker-row__name {
  font-size: 11px;
  opacity: 0.6;
  font-family: var(--font-family-mono, monospace);
}

.picker-row__numbers {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  opacity: 0.7;
}

.picker-row__pip--running { color: var(--n-primary-color, #3b82f6); }
.picker-row__pip--done { color: #22c55e; }
.picker-row__pip--failed { color: #ef4444; }
.picker-row__pip--cancelled { color: #6b7280; }
</style>
