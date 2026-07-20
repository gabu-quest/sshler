<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NIcon, NTooltip } from "naive-ui";
import {
  PhCheckCircle,
  PhCircleDashed,
  PhMinusCircle,
  PhPlus,
  PhWarningCircle,
  PhXCircle,
} from "@phosphor-icons/vue";

import { useI18n } from "@/i18n";
import { useProgressStore } from "@/stores/progress";
import type { ProgressBar, ProgressStatus } from "@/api/types";
import ProgressPicker from "./ProgressPicker.vue";

const { t } = useI18n();
const progressStore = useProgressStore();

const pickerOpen = ref(false);

const scope = computed(() => progressStore.currentScope);
const bars = computed<ProgressBar[]>(() => progressStore.subscribedBars);
// The strip only exists when a box is active. The + button stays available
// even with no subscriptions so the user can open the picker to add some.
const visible = computed(() => scope.value !== null);

// --- finish blink: flash a bar once when it transitions into `done` ---
const flashing = ref<Set<string>>(new Set());
const prevStatus = new Map<string, ProgressStatus>();

watch(
  bars,
  (current) => {
    for (const bar of current) {
      const prev = prevStatus.get(bar.name);
      if (bar.status === "done" && prev !== undefined && prev !== "done") {
        flashing.value = new Set(flashing.value).add(bar.name);
      }
      prevStatus.set(bar.name, bar.status);
    }
  },
  // immediate so a bar already running at mount records its status; the flash
  // then fires on the first running→done transition rather than being missed.
  { deep: true, immediate: true },
);

function onFlashEnd(name: string): void {
  if (!flashing.value.has(name)) return;
  const next = new Set(flashing.value);
  next.delete(name);
  flashing.value = next;
}

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

function percent(bar: ProgressBar): number {
  if (!bar.total || bar.total <= 0) return 0;
  return Math.max(0, Math.min(100, (bar.current / bar.total) * 100));
}

// Floored for display: 3299/3300 reads 99%, never 100% until current >= total.
function percentLabel(bar: ProgressBar): number {
  return Math.floor(percent(bar));
}

function displayLabel(bar: ProgressBar): string {
  return bar.label && bar.label.trim() ? bar.label : bar.name;
}

function metaEntries(bar: ProgressBar): [string, string][] {
  return Object.entries(bar.metadata ?? {}).map(([k, v]) => [
    k,
    typeof v === "string" ? v : JSON.stringify(v),
  ]);
}

function formatTime(epochSeconds: number): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
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
</script>

<template>
  <div v-if="visible" class="progress-strip">
    <div class="progress-strip__bars">
      <NTooltip
        v-for="bar in bars"
        :key="bar.name"
        trigger="hover"
        :delay="150"
        placement="bottom-start"
      >
        <template #trigger>
          <div
            class="strip-bar"
            :class="{
              'strip-bar--stale': progressStore.isStale(bar),
              'strip-bar--flash': flashing.has(bar.name),
            }"
            @animationend="onFlashEnd(bar.name)"
          >
            <NIcon
              :component="statusIcon(bar.status)"
              :class="`strip-bar__pip--${bar.status}`"
              :size="13"
            />
            <span class="strip-bar__label">{{ displayLabel(bar) }}</span>
            <span class="strip-bar__track">
              <span
                class="strip-bar__fill"
                :style="{ width: percent(bar) + '%', background: resolveColor(bar) }"
              />
            </span>
            <span class="strip-bar__numbers">{{ percentLabel(bar) }}%</span>
            <PhWarningCircle
              v-if="bar.metadata_error"
              class="strip-bar__meta-warn"
              weight="fill"
              :size="12"
            />
          </div>
        </template>

        <div class="strip-tip">
          <div class="strip-tip__title">{{ displayLabel(bar) }}</div>
          <div v-if="bar.label && bar.label.trim()" class="strip-tip__name">{{ bar.name }}</div>
          <div class="strip-tip__row">
            <span :class="`strip-tip__status strip-tip__status--${bar.status}`">{{ bar.status }}</span>
            <span class="strip-tip__count">
              {{ bar.current }}/{{ bar.total }} ({{ percentLabel(bar) }}%)
            </span>
          </div>
          <dl v-if="metaEntries(bar).length" class="strip-tip__meta">
            <template v-for="[k, v] in metaEntries(bar)" :key="k">
              <dt>{{ k }}</dt>
              <dd>{{ v }}</dd>
            </template>
          </dl>
          <div v-if="bar.metadata_error" class="strip-tip__error">
            {{ t('progress.meta.error', { reason: bar.metadata_error }) }}
          </div>
          <div class="strip-tip__time">{{ t('progress.tip.updated', { time: formatTime(bar.updated_at) }) }}</div>
        </div>
      </NTooltip>
    </div>
    <button
      type="button"
      class="strip-add"
      :aria-label="t('progress.strip.add')"
      :title="t('progress.strip.manage', { box: scope ?? '' })"
      @click="pickerOpen = true"
    >
      <PhPlus weight="bold" :size="14" />
    </button>

    <ProgressPicker v-model:show="pickerOpen" />
  </div>
</template>

<style scoped>
.progress-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 22px;
  padding: 0 10px;
  background: var(--n-card-color, color-mix(in srgb, currentColor 5%, transparent));
  border-bottom: 1px solid var(--n-divider-color, color-mix(in srgb, currentColor 10%, transparent));
  font-size: 11px;
  overflow: hidden;
}

.progress-strip__bars {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.progress-strip__bars::-webkit-scrollbar {
  display: none;
}

.strip-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 260px;
  border-radius: 4px;
  padding: 0 2px;
}
.strip-bar--stale {
  opacity: 0.45;
}

/* One bright-white pulse when a bar transitions into `done`. */
.strip-bar--flash {
  animation: strip-flash 550ms ease-out 1;
}
@keyframes strip-flash {
  0% {
    background: #ffffff;
    box-shadow: 0 0 8px 2px rgba(255, 255, 255, 0.9);
  }
  100% {
    background: transparent;
    box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);
  }
}

.strip-bar__meta-warn {
  color: #f59e0b;
  flex-shrink: 0;
}

.strip-bar__label {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}

.strip-bar__track {
  width: 64px;
  height: 3px;
  border-radius: 2px;
  background: var(--n-action-color, color-mix(in srgb, currentColor 14%, transparent));
  overflow: hidden;
  flex-shrink: 0;
}
.strip-bar__fill {
  display: block;
  height: 100%;
  border-radius: 2px;
  transition: width 200ms ease;
}

.strip-bar__numbers {
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
  flex-shrink: 0;
  min-width: 30px;
  text-align: right;
}

.strip-bar__pip--running { color: var(--n-primary-color, #3b82f6); }
.strip-bar__pip--done { color: #22c55e; }
.strip-bar__pip--failed { color: #ef4444; }
.strip-bar__pip--cancelled { color: #6b7280; }

.strip-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  background: transparent;
  color: inherit;
  opacity: 0.6;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  padding: 0;
}
.strip-add:hover {
  opacity: 1;
  background: var(--n-action-color, color-mix(in srgb, currentColor 12%, transparent));
}

/* Rich tooltip content */
.strip-tip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 160px;
  max-width: 320px;
  font-size: 12px;
}
.strip-tip__title {
  font-weight: 600;
  font-size: 13px;
}
.strip-tip__name {
  font-family: var(--font-family-mono, monospace);
  font-size: 11px;
  opacity: 0.7;
}
.strip-tip__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.strip-tip__count {
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.strip-tip__status {
  text-transform: capitalize;
  font-weight: 500;
}
.strip-tip__status--running { color: #60a5fa; }
.strip-tip__status--done { color: #4ade80; }
.strip-tip__status--failed { color: #f87171; }
.strip-tip__status--cancelled { color: #9ca3af; }

.strip-tip__meta {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 10px;
  margin: 2px 0 0;
  padding: 4px 0 0;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
}
.strip-tip__meta dt {
  font-weight: 500;
  opacity: 0.7;
}
.strip-tip__meta dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}

.strip-tip__error {
  color: #f87171;
  font-weight: 500;
}
.strip-tip__time {
  opacity: 0.55;
  font-size: 11px;
}
</style>
