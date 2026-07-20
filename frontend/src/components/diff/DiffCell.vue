<script setup lang="ts">
import { computed } from "vue";
import { NSpin, NAlert, NIcon, NTag, NButton, NTooltip } from "naive-ui";
import {
  PhFileX,
  PhFileCode,
  PhTrash,
  PhArrowUp,
  PhArrowDown,
  PhArrowsLeftRight,
} from "@phosphor-icons/vue";

import DiffViewer from "@/components/DiffViewer.vue";
import DiffSidePicker from "@/components/diff/DiffSidePicker.vue";
import { useAppStore } from "@/stores/app";
import { useI18n } from "@/i18n";
import type { DiffCellState, DiffSide } from "@/stores/diff";

const props = defineProps<{
  state: DiffCellState;
  index: number;
  total: number;
  language: string;
}>();

const emit = defineEmits<{
  (e: "update:side", which: "left" | "right", side: DiffSide): void;
  (e: "remove"): void;
  (e: "move-up"): void;
  (e: "move-down"): void;
  (e: "swap-sides"): void;
}>();

const { t } = useI18n();
const appStore = useAppStore();

const theme = computed<"light" | "dark">(() => (appStore.isDark ? "dark" : "light"));

function summarize(side: DiffCellState["left"]): string {
  const c = side.config;
  if (!c.box && !c.path) return t("diff.cell.empty_side");
  const parts: string[] = [];
  if (c.box) parts.push(c.box);
  if (c.ref) parts.push(`@${c.ref}`);
  if (c.path) parts.push(`:${c.path}`);
  return parts.join("");
}

const cellLabel = computed(() =>
  t("diff.cell.label_with_index", {
    n: String(props.index + 1),
    left: summarize(props.state.left),
    right: summarize(props.state.right),
  }),
);

const canMoveUp = computed(() => props.index > 0);
const canMoveDown = computed(() => props.index < props.total - 1);
</script>

<template>
  <article
    class="diff-cell"
    :data-testid="`diff-cell-${index}`"
    :data-cell-index="index"
  >
    <header class="cell-header">
      <span class="cell-index" :title="cellLabel">#{{ index + 1 }}</span>
      <span class="side-label left" :title="summarize(state.left)">{{ summarize(state.left) }}</span>
      <span class="cell-arrow">↔</span>
      <span class="side-label right" :title="summarize(state.right)">{{ summarize(state.right) }}</span>
      <span class="cell-badges">
        <NTag v-if="state.left.status === 'missing'" size="tiny" type="warning">{{ t("diff.cell.left_missing") }}</NTag>
        <NTag v-if="state.right.status === 'missing'" size="tiny" type="warning">{{ t("diff.cell.right_missing") }}</NTag>
        <NTag v-if="state.left.truncated || state.right.truncated" size="tiny" type="info">{{ t("diff.cell.truncated") }}</NTag>
      </span>
      <span class="cell-controls">
        <NTooltip :delay="300">
          <template #trigger>
            <NButton quaternary circle size="tiny" :disabled="!canMoveUp" @click="emit('move-up')" :aria-label="t('diff.cell.action_move_up')" :data-testid="`diff-cell-up-${index}`">
              <NIcon size="14"><PhArrowUp weight="bold" /></NIcon>
            </NButton>
          </template>
          {{ t("diff.cell.action_move_up") }}
        </NTooltip>
        <NTooltip :delay="300">
          <template #trigger>
            <NButton quaternary circle size="tiny" :disabled="!canMoveDown" @click="emit('move-down')" :aria-label="t('diff.cell.action_move_down')" :data-testid="`diff-cell-down-${index}`">
              <NIcon size="14"><PhArrowDown weight="bold" /></NIcon>
            </NButton>
          </template>
          {{ t("diff.cell.action_move_down") }}
        </NTooltip>
        <NTooltip :delay="300">
          <template #trigger>
            <NButton quaternary circle size="tiny" @click="emit('swap-sides')" :aria-label="t('diff.cell.action_swap_sides')" :data-testid="`diff-cell-swap-${index}`">
              <NIcon size="14"><PhArrowsLeftRight weight="duotone" /></NIcon>
            </NButton>
          </template>
          {{ t("diff.cell.action_swap_sides") }}
        </NTooltip>
        <NTooltip :delay="300">
          <template #trigger>
            <NButton quaternary circle size="tiny" type="error" @click="emit('remove')" :aria-label="t('diff.cell.action_remove')" :data-testid="`diff-cell-remove-${index}`">
              <NIcon size="14"><PhTrash weight="duotone" /></NIcon>
            </NButton>
          </template>
          {{ t("diff.cell.action_remove") }}
        </NTooltip>
      </span>
    </header>

    <section class="picker-row">
      <DiffSidePicker
        :side="state.left.config"
        variant="left"
        @update:side="(s) => emit('update:side', 'left', s)"
      />
      <DiffSidePicker
        :side="state.right.config"
        variant="right"
        @update:side="(s) => emit('update:side', 'right', s)"
      />
    </section>

    <div class="cell-body">
      <div v-if="state.status === 'idle'" class="cell-empty">
        {{ t("diff.cell.idle") }}
      </div>

      <div v-else-if="state.status === 'loading'" class="cell-loading">
        <NSpin size="medium" />
        <span>{{ t("diff.cell.loading") }}</span>
      </div>

      <NAlert v-else-if="state.status === 'error'" type="error" :title="t('diff.cell.error_title')">
        <pre class="error-detail">{{ state.error }}</pre>
      </NAlert>

      <NAlert v-else-if="state.status === 'binary'" type="warning" :title="t('diff.cell.binary_title')">
        <span>
          <NIcon size="14"><PhFileX weight="duotone" /></NIcon>
          {{ t("diff.cell.binary_body") }}
        </span>
      </NAlert>

      <div v-else class="cell-diff">
        <DiffViewer
          :original="state.left.content"
          :modified="state.right.content"
          :language="language"
          :theme="theme"
        />
        <div class="cell-meta">
          <NIcon size="12"><PhFileCode weight="duotone" /></NIcon>
          <span>{{ t("diff.cell.language_label", { language }) }}</span>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.diff-cell {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--stroke);
  border-radius: 10px;
  background: var(--panel-bg);
  overflow: hidden;
}

.cell-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--hover-overlay);
  border-bottom: 1px solid var(--stroke);
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  position: sticky;
  top: 0;
  z-index: 2;
}

.cell-index {
  flex-shrink: 0;
  color: var(--muted);
  font-weight: 700;
}

.side-label {
  flex: 1 1 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.side-label.left { color: #f97316; text-align: right; }
.side-label.right { color: #22c55e; text-align: left; }

.cell-arrow {
  flex-shrink: 0;
  color: var(--muted);
  font-weight: 600;
}

.cell-badges {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.cell-controls {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.picker-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 10px;
}

.cell-body {
  padding: 0 10px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cell-empty,
.cell-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
  color: var(--muted);
  font-size: 13px;
}

.cell-diff {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cell-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--muted);
  font-family: "JetBrains Mono", monospace;
}

.error-detail {
  white-space: pre-wrap;
  font-size: 12px;
  margin: 0;
  font-family: "JetBrains Mono", monospace;
}

@media (max-width: 768px) {
  .picker-row {
    grid-template-columns: 1fr;
  }
}
</style>
