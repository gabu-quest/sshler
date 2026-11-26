<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { failureText } from "@/api/apiError";
import type { TransferProgress, TransferResult } from "@/api/http";
import type { PendingPrompt } from "@/composables/useReplacePrompt";
import { useI18n } from "@/i18n";
import { NButton, NCheckbox, NModal, NProgress } from "naive-ui";
import { PhCheck, PhHourglass, PhMinus, PhQuestion, PhSpinner, PhWarning, PhX } from "@phosphor-icons/vue";

const props = withDefaults(defineProps<{
  show: boolean;
  mode: "copy" | "move";
  srcBox: string;
  destBox: string;
  destination: string;
  files: string[];
  progress: TransferProgress | null;
  result: TransferResult | null;
  error: string | null;
  /** Files the user chose to keep as they were when asked "Replace?". */
  skipped?: string[];
  /** Files an earlier call of this transfer already placed. */
  placed?: string[];
  /** Files the server found taken that the user has not answered about yet. */
  waiting?: string[];
  /** Files an earlier call of this transfer failed to place, with the server's text. */
  failedSoFar?: TransferResult["failed"];
  /** Files the call in flight found taken; they are asked about when it ends. */
  held?: string[];
  /** The files the call in flight carries (all of them for the first call). */
  sending?: string[] | null;
  /** The "Replace?" question that is open, drawn under the file list; null when none is. */
  prompt?: PendingPrompt | null;
}>(), { prompt: null, skipped: () => [], placed: () => [], waiting: () => [], failedSoFar: () => [], held: () => [], sending: null });

const emit = defineEmits<{
  cancel: [];
  close: [];
}>();

const { t } = useI18n();

const isRunning = computed(() => props.show && !props.result && !props.error);
const isDone = computed(() => !!props.result);
const hasErrors = computed(() => (props.result?.failed.length ?? 0) > 0);

const activeFile = computed(() => props.progress?.file ?? null);
const activeFileName = computed(() => {
  if (!activeFile.value) return "";
  const parts = activeFile.value.split("/");
  return parts[parts.length - 1] || activeFile.value;
});

const filePercent = computed(() => {
  if (!props.progress || !props.progress.bytes_total) return 0;
  return Math.round((props.progress.bytes_done / props.progress.bytes_total) * 100);
});

// The files that were placed: a kept or failed file is not counted, also at the end.
const overallDone = computed(() => props.files.filter((file) => getFileStatus(file) === "done").length);

const overallPercent = computed(() => {
  if (!props.files.length) return 0;
  if (props.result) return 100;
  return Math.round((overallDone.value / props.files.length) * 100);
});

const title = computed(() => {
  return t(props.mode === "move" ? "commander.transfer_title_move" : "commander.transfer_title_copy", {
    count: props.files.length,
    box: props.destBox,
  });
});

/** An answer for the open question; a click that lands after it closed does nothing. */
function answer(choice: "replace" | "keep_both" | "no") {
  props.prompt?.answer(choice);
}

/**
 * Escape anywhere in the dialog answers an open question "no". The modal's own Escape is
 * off while a question is open, so the press does not also close the transfer; with no
 * question open it is left alone for the modal.
 */
function onEscape(event: KeyboardEvent) {
  if (!props.prompt) return;
  event.stopPropagation();
  event.preventDefault();
  answer("no");
}

const noButton = ref<{ $el?: HTMLElement } | HTMLElement | null>(null);

// A new question puts the focus on "No, keep it" (or "Skip"), so Enter replaces nothing.
watch(
  () => props.prompt,
  async (question) => {
    if (!question) return;
    await nextTick();
    const el = noButton.value && "$el" in noButton.value ? noButton.value.$el : noButton.value;
    (el as HTMLElement | null | undefined)?.focus?.();
  },
);

function getFileName(path: string) {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

type FileStatus = "done" | "active" | "pending" | "failed" | "skipped" | "waiting";

function getFileStatus(filePath: string): FileStatus {
  if (props.result) {
    if (props.result.failed.some((failedFile) => failedFile.path === filePath)) return "failed";
    if (props.skipped.includes(filePath)) return "skipped";
    return "done";
  }

  // Said so far, whatever the call in flight reports: a skipped, waiting, failed or
  // already placed file keeps that state.
  if (props.skipped.includes(filePath)) return "skipped";
  if (props.waiting.includes(filePath)) return "waiting";
  if (props.failedSoFar.some((failedFile) => failedFile.path === filePath)) return "failed";
  if (props.placed.includes(filePath)) return "done";
  // Not placed, though the call has moved on to the files after it.
  if (props.held.includes(filePath)) return "pending";

  if (!props.progress) return "pending";
  if (props.progress.file === filePath) return "active";

  // The progress index counts within the files the call in flight carries.
  const index = (props.sending ?? props.files).indexOf(filePath);
  return index >= 0 && index < (props.progress.index ?? 0) ? "done" : "pending";
}

function getFileError(filePath: string): string | null {
  const failedFile = (props.result?.failed ?? props.failedSoFar).find((item) => item.path === filePath);
  return failedFile ? failureText(failedFile) : null;
}
</script>

<template>
  <NModal :show="show" :mask-closable="!isRunning" :close-on-esc="!prompt" @update:show="(value) => !value && emit('close')">
    <div class="transfer-modal" @keydown.escape="onEscape">
      <div class="transfer-header">
        <div class="transfer-heading">
          <span class="transfer-heading-icon">
            <PhSpinner v-if="isRunning" weight="duotone" class="heading-running" />
            <PhWarning v-else-if="error || hasErrors" weight="duotone" class="heading-warning" />
            <PhCheck v-else-if="isDone" weight="duotone" class="heading-done" />
          </span>
          <div class="transfer-heading-text">
            <span class="transfer-title">{{ title }}</span>
            <span class="transfer-dest">{{ srcBox }} -> {{ destBox }} · {{ destination }}</span>
          </div>
        </div>
      </div>

      <div class="transfer-files">
        <div
          v-for="file in files"
          :key="file"
          class="transfer-file-row"
          :class="getFileStatus(file)"
        >
          <span class="file-status-icon">
            <PhCheck v-if="getFileStatus(file) === 'done'" weight="duotone" class="status-done" />
            <PhSpinner v-else-if="getFileStatus(file) === 'active'" weight="duotone" class="status-active" />
            <PhHourglass v-else-if="getFileStatus(file) === 'pending'" weight="duotone" class="status-pending" />
            <PhQuestion v-else-if="getFileStatus(file) === 'waiting'" weight="duotone" class="status-waiting" />
            <PhMinus v-else-if="getFileStatus(file) === 'skipped'" weight="duotone" class="status-skipped" />
            <PhX v-else weight="duotone" class="status-failed" />
          </span>
          <span class="file-name">{{ getFileName(file) }}</span>
          <span v-if="getFileStatus(file) === 'active' && progress" class="file-progress">
            {{ filePercent }}%
          </span>
          <span v-if="getFileStatus(file) === 'skipped'" class="file-skipped">
            {{ t("commander.transfer_skipped") }}
          </span>
          <span v-if="getFileStatus(file) === 'waiting'" class="file-waiting">
            {{ t("commander.transfer_waiting") }}
          </span>
          <span v-if="getFileStatus(file) === 'failed'" class="file-error" :title="getFileError(file) || ''">
            {{ getFileError(file) }}
          </span>
        </div>
      </div>

      <div
        v-if="prompt"
        class="transfer-prompt"
        role="dialog"
        :aria-label="prompt.title"
      >
        <div class="prompt-title">{{ prompt.title }}</div>
        <div class="prompt-content">{{ prompt.content }}</div>
        <div class="replace-actions">
          <NButton ref="noButton" size="small" @click="answer('no')">
            {{ t(prompt.remaining > 1 ? "files.skip" : "files.replace_no") }}
          </NButton>
          <NButton size="small" @click="answer('keep_both')">{{ t("files.keep_both") }}</NButton>
          <NButton size="small" type="warning" :disabled="!prompt.armed" @click="answer('replace')">
            {{ t("files.replace_yes") }}
          </NButton>
        </div>
        <NCheckbox
          v-if="prompt.remaining > 1"
          :checked="prompt.same"
          @update:checked="(value: boolean) => { if (props.prompt) props.prompt.same = value }"
        >
          {{ t("files.replace_same_for_rest", { n: prompt.remaining - 1 }) }}
        </NCheckbox>
      </div>

      <div v-if="isRunning && progress" class="transfer-current">
        <div class="current-label">{{ activeFileName }}</div>
        <NProgress
          :percentage="filePercent"
          :show-indicator="false"
          :height="4"
          color="var(--accent)"
          rail-color="var(--surface-variant)"
        />
      </div>

      <div class="transfer-overall">
        <div class="overall-label">{{ t("commander.transfer_overall", { done: overallDone, total: files.length }) }}</div>
        <NProgress
          :percentage="overallPercent"
          :show-indicator="false"
          :height="6"
          color="var(--accent)"
          rail-color="var(--surface-variant)"
        />
      </div>

      <div v-if="error" class="transfer-error">
        <PhWarning weight="duotone" class="transfer-error-icon" />
        <span>{{ error }}</span>
      </div>

      <div class="transfer-footer">
        <NButton v-if="isRunning" type="error" size="small" ghost @click="emit('cancel')">
          {{ t("commander.transfer_cancel") }}
        </NButton>
        <NButton v-else size="small" @click="emit('close')">
          {{ t("common.close") }}
        </NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.transfer-modal {
  width: 480px;
  max-width: 90vw;
  max-height: calc(100vh - 32px);
  max-height: calc(100dvh - 32px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 6px;
  padding: 16px;
  font-family: var(--font-mono);
  color: var(--text);
}

.transfer-header {
  margin-bottom: 12px;
}

.transfer-heading {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.transfer-heading-icon {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
}

.heading-running {
  color: var(--accent);
  animation: spin 1s linear infinite;
}

.heading-warning {
  color: var(--warning);
}

.heading-done {
  color: var(--success);
}

.transfer-heading-text {
  min-width: 0;
  flex: 1;
}

.transfer-title {
  font-size: 14px;
  font-weight: 600;
  display: block;
}

.transfer-dest {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
  display: block;
  word-break: break-all;
}

.transfer-files {
  max-height: 200px;
  min-height: 56px;
  flex: 0 1 auto;
  overflow-y: auto;
  margin-bottom: 12px;
  border: 1px solid var(--stroke);
  border-radius: 4px;
}

.transfer-file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  font-size: 12px;
  border-bottom: 1px solid var(--surface-variant);
}

.transfer-file-row:last-child {
  border-bottom: none;
}

.file-status-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-done {
  color: var(--success);
}

.status-active {
  color: var(--accent);
  animation: spin 1s linear infinite;
}

.status-pending {
  color: var(--muted);
}

.status-failed {
  color: var(--error);
}

.status-skipped {
  color: var(--warning);
}

.status-waiting {
  color: var(--warning);
}

.file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-progress {
  color: var(--accent);
  flex-shrink: 0;
  font-size: 11px;
}

/* What happened to a file is read whole: it wraps under a long name, never cut. */
.file-skipped,
.file-waiting,
.file-error {
  font-size: 10px;
  max-width: 65%;
  text-align: right;
  overflow-wrap: anywhere;
}

.file-skipped,
.file-waiting {
  color: var(--warning);
}

.file-error {
  color: var(--error);
}

.transfer-current {
  margin-bottom: 8px;
}

.current-label {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 4px;
}

.transfer-overall {
  margin-bottom: 12px;
}

.overall-label {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}

.transfer-error {
  color: var(--error);
  font-size: 12px;
  padding: 8px;
  background: var(--error-bg);
  border-radius: 4px;
  margin-bottom: 12px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.transfer-error-icon {
  flex-shrink: 0;
  margin-top: 1px;
}

.transfer-prompt {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid var(--warning);
  border-radius: var(--radius-sm);
  background: var(--warning-bg);
  font-family: var(--font-sans);
}

.prompt-title {
  font-size: 13px;
  font-weight: 600;
}

/* Naive's unticked box is a faint outline that vanishes on the amber background. */
.transfer-prompt :deep(.n-checkbox .n-checkbox-box .n-checkbox-box__border) {
  border: 1.5px solid var(--text-secondary);
}

.transfer-prompt :deep(.n-checkbox.n-checkbox--checked .n-checkbox-box) {
  background-color: var(--warning);
}

.transfer-prompt :deep(.n-checkbox.n-checkbox--checked .n-checkbox-box .n-checkbox-box__border) {
  border-color: var(--warning);
}

.transfer-prompt :deep(.n-checkbox .n-checkbox-icon) {
  color: var(--bg);
}

.prompt-content {
  font-size: 12px;
  overflow-wrap: anywhere;
}

.replace-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  flex-wrap: nowrap;
}

.replace-actions > * {
  flex: 1 1 auto;
  min-width: 0;
}

.transfer-footer {
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
}
</style>
