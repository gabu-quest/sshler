<script setup lang="ts">
import { useMessage } from "naive-ui";
import { NButton, NIcon, NSpace } from "naive-ui";
import { PhArrowClockwise, PhArrowLeft, PhClipboard, PhHouse, PhTerminalWindow } from "@phosphor-icons/vue";
import type { GitInfo } from "@/api/types";
import GitBadge from "@/components/GitBadge.vue";
import { useI18n } from "@/i18n";

const props = defineProps<{
  currentDir: string;
  gitInfo: GitInfo | null;
  selectedBox: string | null;
  refreshing: boolean;
  wslDistro?: string | null;
}>();

const emit = defineEmits<{
  (e: "navigate-home"): void;
  (e: "navigate-up"): void;
  (e: "reload"): void;
  (e: "open-terminal"): void;
}>();

const { t } = useI18n();
const message = useMessage();

const copyWindowsPath = async () => {
  if (!props.wslDistro) return;
  const winPath = `\\\\wsl$\\${props.wslDistro}${props.currentDir.replace(/\//g, '\\')}`;
  await navigator.clipboard.writeText(winPath);
  message.success(t('files.wsl_path_copied'));
};
</script>

<template>
  <div class="breadcrumb-nav">
    <NSpace size="small" align="center">
      <NButton size="small" quaternary @click="emit('navigate-home')" :title="t('common.home')">
        <NIcon size="16"><PhHouse weight="duotone" /></NIcon>
      </NButton>
      <NButton size="small" quaternary @click="emit('navigate-up')" :disabled="currentDir === '/' || currentDir === '~'" :title="t('common.up')">
        <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
      </NButton>
      <span class="breadcrumb-path">{{ currentDir }}</span>
      <NButton
        v-if="wslDistro && selectedBox === 'local'"
        size="tiny"
        quaternary
        @click="copyWindowsPath"
        :title="t('files.copy_windows_path')"
        class="wsl-copy-btn"
      >
        <NIcon size="12"><PhClipboard weight="duotone" /></NIcon>
        <span class="wsl-label">Win</span>
      </NButton>
      <GitBadge v-if="gitInfo?.is_repo" :info="gitInfo" />
    </NSpace>
    <NSpace size="small">
      <NButton size="small" @click="emit('reload')" :disabled="!selectedBox || refreshing" :loading="refreshing" :title="t('common.refresh')">
        <NIcon size="16"><PhArrowClockwise weight="duotone" /></NIcon>
      </NButton>
      <NButton size="small" @click="emit('open-terminal')" :disabled="!selectedBox" :title="t('terminal.open_terminal')">
        <NIcon size="16"><PhTerminalWindow weight="duotone" /></NIcon>
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped>
.breadcrumb-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-size: 14px;
}

.breadcrumb-path {
  color: var(--text);
  font-weight: 500;
}

.wsl-copy-btn {
  font-size: 11px;
  padding: 0 6px;
  height: 22px;
  opacity: 0.6;
  transition: opacity 0.15s ease;
}

.wsl-copy-btn:hover {
  opacity: 1;
}

.wsl-label {
  margin-left: 3px;
  font-size: 10px;
  font-family: var(--font-mono);
}


@media (max-width: 768px) {
  .breadcrumb-nav {
    flex-direction: column;
    gap: 8px;
  }

  .breadcrumb-path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 200px;
  }
}

@media (max-width: 480px) {
  .breadcrumb-path {
    max-width: 140px;
  }
}
</style>
