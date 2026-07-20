<script setup lang="ts">
import { NIcon } from 'naive-ui'
import { PhGitBranch } from '@phosphor-icons/vue'
import type { GitInfo } from '@/api/types'

defineProps<{
  info: GitInfo
  compact?: boolean
}>()
</script>

<template>
  <span
    v-if="info.is_repo"
    class="git-badge"
    :class="{ dirty: info.dirty, compact }"
  >
    <NIcon size="12"><PhGitBranch weight="duotone" /></NIcon>
    {{ info.branch }}
    <span v-if="info.commit" class="git-commit">{{ info.commit }}</span>
    <span v-if="info.dirty" class="dirty-indicator">*</span>
  </span>
</template>

<style scoped>
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

.git-badge.compact {
  font-size: 11px;
  padding: 2px 0;
  background: none;
  border: none;
  border-radius: 0;
}

.git-commit {
  opacity: 0.6;
  font-size: 11px;
}

.compact .git-commit {
  font-size: 10px;
}

.dirty-indicator {
  color: #fbbf24;
  font-weight: bold;
}
</style>
