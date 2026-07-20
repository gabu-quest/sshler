<script setup lang="ts">
import { computed } from "vue";

import type { TransferProgress, TransferResult } from "@/api/http";
import { useI18n } from "@/i18n";
import { NButton, NModal, NProgress } from "naive-ui";
import { PhCheck, PhHourglass, PhSpinner, PhWarning, PhX } from "@phosphor-icons/vue";

const props = defineProps<{
  show: boolean;
  mode: "copy" | "move";
  srcBox: string;
  destBox: string;
  destination: string;
  files: string[];
  progress: TransferProgress | null;
  result: TransferResult | null;
  error: string | null;
}>();

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

const overallDone = computed(() => {
  if (props.result) return props.files.length;
  return props.progress?.index ?? 0;
});

const overallPercent = computed(() => {
  if (!props.files.length) return 0;
  if (props.result) return 100;
  return Math.round((overallDone.value / props.files.length) * 100);
});

const title = computed(() => {
  const verb = props.mode === "move" ? t("commander.move") : t("commander.copy");
  return `${verb} ${props.files.length} file${props.files.length !== 1 ? "s" : ""} -> ${props.destBox}`;
});

function getFileName(path: string) {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

type FileStatus = "done" | "active" | "pending" | "failed";

function getFileStatus(filePath: string): FileStatus {
  if (props.result) {
    if (props.result.failed.some((failedFile) => failedFile.path === filePath)) return "failed";
    return "done";
  }

  if (!props.progress) return "pending";
  if (props.progress.file === filePath) return "active";

  const index = props.files.indexOf(filePath);
  return index < (props.progress.index ?? 0) ? "done" : "pending";
}

function getFileError(filePath: string): string | null {
  const failedFile = props.result?.failed.find((item) => item.path === filePath);
  return failedFile?.error ?? null;
}
</script>

<template>
  <NModal :show="show" :mask-closable="!isRunning" @update:show="(value) => !value && emit('close')">
    <div class="transfer-modal">
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
            <PhX v-else weight="duotone" class="status-failed" />
          </span>
          <span class="file-name">{{ getFileName(file) }}</span>
          <span v-if="getFileStatus(file) === 'active' && progress" class="file-progress">
            {{ filePercent }}%
          </span>
          <span v-if="getFileStatus(file) === 'failed'" class="file-error" :title="getFileError(file) || ''">
            {{ getFileError(file) }}
          </span>
        </div>
      </div>

      <div v-if="isRunning && progress" class="transfer-current">
        <div class="current-label">{{ activeFileName }}</div>
        <NProgress
          :percentage="filePercent"
          :show-indicator="false"
          :height="4"
          color="#a78bfa"
          rail-color="#1a1a24"
        />
      </div>

      <div class="transfer-overall">
        <div class="overall-label">{{ overallDone }} / {{ files.length }} files</div>
        <NProgress
          :percentage="overallPercent"
          :show-indicator="false"
          :height="6"
          color="#a78bfa"
          rail-color="#1a1a24"
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
  background: #0d0d14;
  border: 1px solid #1a1a24;
  border-radius: 6px;
  padding: 16px;
  font-family: var(--font-mono);
  color: #e0e0e6;
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
  color: #a78bfa;
  animation: spin 1s linear infinite;
}

.heading-warning {
  color: #fbbf24;
}

.heading-done {
  color: #4ade80;
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
  color: #888;
  margin-top: 2px;
  display: block;
  word-break: break-all;
}

.transfer-files {
  max-height: 200px;
  overflow-y: auto;
  margin-bottom: 12px;
  border: 1px solid #1a1a24;
  border-radius: 4px;
}

.transfer-file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  font-size: 12px;
  border-bottom: 1px solid #111118;
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
  color: #4ade80;
}

.status-active {
  color: #a78bfa;
  animation: spin 1s linear infinite;
}

.status-pending {
  color: #555;
}

.status-failed {
  color: #f87171;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-progress {
  color: #a78bfa;
  flex-shrink: 0;
  font-size: 11px;
}

.file-error {
  color: #f87171;
  font-size: 10px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.transfer-current {
  margin-bottom: 8px;
}

.current-label {
  font-size: 11px;
  color: #888;
  margin-bottom: 4px;
}

.transfer-overall {
  margin-bottom: 12px;
}

.overall-label {
  font-size: 12px;
  color: #aaa;
  margin-bottom: 4px;
}

.transfer-error {
  color: #f87171;
  font-size: 12px;
  padding: 8px;
  background: rgba(248, 113, 113, 0.1);
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

.transfer-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
