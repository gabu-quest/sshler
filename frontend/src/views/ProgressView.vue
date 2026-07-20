<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "@/i18n";

import {
  NButton,
  NCard,
  NEmpty,
  NIcon,
  NPopconfirm,
  NProgress,
  NSpace,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import {
  PhArrowsClockwise,
  PhChartBar,
  PhCheckCircle,
  PhCircleDashed,
  PhMinusCircle,
  PhTrash,
  PhXCircle,
} from "@phosphor-icons/vue";

import type { ProgressBar, ProgressStatus } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import { useProgressStore } from "@/stores/progress";
import { useAppStore } from "@/stores/app";

const { t } = useI18n();
const progressStore = useProgressStore();
const bootstrapStore = useBootstrapStore();
const appStore = useAppStore();
const message = useMessage();

const scope = computed<string | null>(() => appStore.activeBox);
const scopeLabel = computed(() =>
  scope.value
    ? t("progress.scope.current", { box: scope.value })
    : t("progress.scope.none"),
);

const refreshing = ref(false);

const bars = computed<ProgressBar[]>(() => progressStore.allBars);
const isEmpty = computed(() => bars.value.length === 0);
const connectionState = computed(() => {
  if (progressStore.connected) return "connected";
  if (progressStore.connecting) return "connecting";
  return "disconnected";
});

function percent(bar: ProgressBar): number {
  if (!bar.total || bar.total <= 0) return 0;
  return Math.max(0, Math.min(100, (bar.current / bar.total) * 100));
}

function formatRelative(ts: number): string {
  const delta = Math.max(0, Date.now() / 1000 - ts);
  if (delta < 60) return `${Math.round(delta)}s ago`;
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
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

const STATUS_TAG_TYPE: Record<ProgressStatus, "default" | "success" | "error" | "warning" | "info"> = {
  running: "info",
  done: "success",
  failed: "error",
  cancelled: "default",
};

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

function resolveColor(bar: ProgressBar): string | undefined {
  if (!bar.color) return undefined;
  if (bar.color.startsWith("#") || bar.color.startsWith("rgb")) return bar.color;
  return KNOWN_COLOR_MAP[bar.color.toLowerCase()] ?? bar.color;
}

function displayLabel(bar: ProgressBar): string {
  return bar.label && bar.label.trim() ? bar.label : bar.name;
}

async function handleRefresh() {
  refreshing.value = true;
  try {
    await progressStore.refresh(bootstrapStore.token);
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    refreshing.value = false;
  }
}

function handleToggleSubscribe(name: string, next: boolean) {
  if (next) {
    progressStore.subscribe(name);
  } else {
    progressStore.unsubscribe(name);
  }
}

async function handleDelete(name: string) {
  try {
    const removed = await progressStore.remove(name, bootstrapStore.token);
    if (removed) {
      message.success(t("progress.deleted", { name }));
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  }
}

onMounted(() => {
  progressStore.connect(bootstrapStore.token);
  handleRefresh();
});
</script>

<template>
  <div class="progress-view">
    <header class="progress-view__header">
      <div class="progress-view__title">
        <NIcon size="22" :component="PhChartBar" />
        <h1>{{ t("progress.title") }}</h1>
      </div>
      <div class="progress-view__meta">
        <span class="progress-view__scope" :class="{ 'progress-view__scope--none': !scope }">
          {{ scopeLabel }}
        </span>
        <span class="progress-view__count">
          {{ t("progress.bar_count", { n: bars.length }) }}
        </span>
        <span class="progress-view__conn" :class="`conn--${connectionState}`">
          {{ t(`progress.conn.${connectionState}`) }}
        </span>
        <NButton size="small" :loading="refreshing" @click="handleRefresh">
          <template #icon>
            <NIcon :component="PhArrowsClockwise" />
          </template>
          {{ t("common.refresh") }}
        </NButton>
      </div>
    </header>

    <div v-if="isEmpty" class="progress-view__empty">
      <NEmpty :description="t('progress.empty')">
        <template #extra>
          <code class="progress-view__hint">{{ t("progress.push_hint") }}</code>
        </template>
      </NEmpty>
    </div>

    <div v-else class="progress-view__list">
      <NCard
        v-for="bar in bars"
        :key="bar.name"
        size="small"
        class="progress-row"
        :class="{ 'progress-row--stale': progressStore.isStale(bar) }"
      >
        <div class="progress-row__top">
          <span
            class="progress-row__stripe"
            :style="{ background: resolveColor(bar) ?? 'var(--n-primary-color, #3b82f6)' }"
            aria-hidden="true"
          />
          <div class="progress-row__name-block">
            <div class="progress-row__name">{{ displayLabel(bar) }}</div>
            <div v-if="bar.label" class="progress-row__sub">{{ bar.name }}</div>
          </div>
          <NTag :type="STATUS_TAG_TYPE[bar.status]" size="small" round>
            <template #icon>
              <NIcon :component="statusIcon(bar.status)" />
            </template>
            {{ bar.status }}
          </NTag>
          <NSpace align="center" :size="8" class="progress-row__actions">
            <NSwitch
              :value="progressStore.isSubscribed(bar.name)"
              :disabled="!scope"
              size="small"
              :title="!scope ? t('progress.scope.none') : undefined"
              @update:value="handleToggleSubscribe(bar.name, $event as boolean)"
            >
              <template #checked>{{ t("progress.subscribed") }}</template>
              <template #unchecked>{{ t("progress.unsubscribed") }}</template>
            </NSwitch>
            <NPopconfirm
              placement="left"
              @positive-click="handleDelete(bar.name)"
            >
              <template #trigger>
                <NButton size="small" quaternary type="error" :title="t('progress.delete')">
                  <template #icon>
                    <NIcon :component="PhTrash" />
                  </template>
                </NButton>
              </template>
              {{ t("progress.delete_confirm", { name: bar.name }) }}
            </NPopconfirm>
          </NSpace>
        </div>
        <div class="progress-row__bar">
          <NProgress
            type="line"
            :percentage="percent(bar)"
            :height="8"
            :color="resolveColor(bar)"
            :show-indicator="false"
          />
          <span class="progress-row__numbers">
            {{ bar.current }}/{{ bar.total }} ({{ Math.floor(percent(bar)) }}%)
          </span>
        </div>
        <div class="progress-row__times">
          <span>{{ t("progress.created") }}: {{ formatRelative(bar.created_at) }}</span>
          <span>{{ t("progress.updated") }}: {{ formatRelative(bar.updated_at) }}</span>
          <span v-if="progressStore.isStale(bar)" class="progress-row__stale-flag">
            {{ t("progress.stale") }}
          </span>
        </div>
      </NCard>
    </div>
  </div>
</template>

<style scoped>
.progress-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.progress-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.progress-view__title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.progress-view__title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.progress-view__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}

.progress-view__count {
  opacity: 0.7;
}

.progress-view__scope {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--n-action-color, color-mix(in srgb, currentColor 8%, transparent));
}
.progress-view__scope--none {
  opacity: 0.6;
  font-style: italic;
}

.progress-view__conn {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: rgba(107, 114, 128, 0.15);
}
.progress-view__conn.conn--connected { color: #22c55e; }
.progress-view__conn.conn--connecting { color: #f59e0b; }
.progress-view__conn.conn--disconnected { color: #6b7280; }

.progress-view__empty {
  padding: 48px 0;
}

.progress-view__hint {
  display: inline-block;
  background: var(--n-action-color, color-mix(in srgb, currentColor 8%, transparent));
  padding: 4px 8px;
  border-radius: 4px;
  font-family: var(--font-family-mono, monospace);
}

.progress-view__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.progress-row {
  transition: opacity 200ms ease;
}
.progress-row--stale {
  opacity: 0.6;
}

.progress-row__top {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.progress-row__stripe {
  width: 4px;
  height: 24px;
  border-radius: 2px;
  flex-shrink: 0;
}
.progress-row__name-block {
  flex: 1;
  min-width: 0;
}
.progress-row__name {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.progress-row__sub {
  font-size: 11px;
  opacity: 0.6;
  font-family: var(--font-family-mono, monospace);
}
.progress-row__actions {
  flex-shrink: 0;
}

.progress-row__bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.progress-row__numbers {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  opacity: 0.7;
  min-width: 110px;
  text-align: right;
}

.progress-row__times {
  display: flex;
  gap: 12px;
  font-size: 11px;
  opacity: 0.65;
  margin-top: 6px;
  flex-wrap: wrap;
}
.progress-row__stale-flag {
  color: #f59e0b;
  font-weight: 500;
}

@media (max-width: 640px) {
  .progress-row__top {
    flex-wrap: wrap;
  }
  .progress-row__actions {
    margin-left: auto;
  }
  .progress-row__numbers {
    min-width: 90px;
  }
}
</style>
