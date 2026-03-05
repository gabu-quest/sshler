<script setup lang="ts">
import { NButton, NIcon, NSpace } from "naive-ui";
import { PhArrowClockwise, PhArrowLeft, PhGitBranch, PhHouse, PhTerminalWindow } from "@phosphor-icons/vue";
import type { GitInfo } from "@/api/types";
import { useI18n } from "@/i18n";

const props = defineProps<{
  currentDir: string;
  gitInfo: GitInfo | null;
  selectedBox: string | null;
  refreshing: boolean;
}>();

const emit = defineEmits<{
  (e: "navigate-home"): void;
  (e: "navigate-up"): void;
  (e: "reload"): void;
  (e: "open-terminal"): void;
}>();

const { t } = useI18n();
</script>

<template>
  <div class="breadcrumb-nav">
    <NSpace size="small" align="center">
      <NButton size="small" quaternary @click="emit('navigate-home')" :title="t('common.home')">
        <NIcon size="16"><PhHouse weight="duotone" /></NIcon>
      </NButton>
      <NButton size="small" quaternary @click="emit('navigate-up')" :disabled="currentDir === '/'" :title="t('common.up')">
        <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
      </NButton>
      <span class="breadcrumb-path">{{ currentDir }}</span>
      <span v-if="gitInfo?.is_repo" class="git-badge" :class="{ dirty: gitInfo.dirty }">
        <NIcon size="12"><PhGitBranch weight="duotone" /></NIcon>
        {{ gitInfo.branch }}
        <span v-if="gitInfo.dirty" class="dirty-indicator">*</span>
      </span>
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

.git-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 2px 8px;
  background: rgba(136, 58, 234, 0.15);
  border: 1px solid rgba(136, 58, 234, 0.3);
  border-radius: 12px;
  color: #a78bfa;
  font-family: var(--font-mono);
}

.git-badge.dirty {
  background: rgba(234, 179, 8, 0.15);
  border-color: rgba(234, 179, 8, 0.3);
  color: #fbbf24;
}

.dirty-indicator {
  color: #fbbf24;
  font-weight: bold;
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
