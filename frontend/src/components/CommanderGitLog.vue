<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";

import { gitBranches, gitLog } from "@/api/http";
import type { GitBranch, GitCommit } from "@/api/types";
import { useI18n } from "@/i18n";
import { NSpin, NTabPane, NTabs } from "naive-ui";
import { PhCheck, PhGitBranch } from "@phosphor-icons/vue";

const props = defineProps<{
  show: boolean;
  box: string;
  directory: string;
  token: string | null;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
  "select-commit": [commit: GitCommit];
  "select-branch": [branch: GitBranch];
}>();

const { t } = useI18n();

const activeTab = ref<"commits" | "branches">("commits");
const commits = ref<GitCommit[]>([]);
const branches = ref<GitBranch[]>([]);
const loadingCommits = ref(false);
const loadingBranches = ref(false);

let loadRequestId = 0;

function truncate(value: string, maxLength: number) {
  if (value.length <= maxLength) return value;
  if (maxLength <= 3) return value.slice(0, maxLength);
  return `${value.slice(0, maxLength - 3)}...`;
}

function translateOrFallback(key: string, fallback: string) {
  const value = t(key);
  return value === key ? fallback : value;
}

function relativeDate(isoDate: string): string {
  const date = new Date(isoDate);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays}d ago`;
  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) return `${diffMonths}mo ago`;
  return `${Math.floor(diffMonths / 12)}y ago`;
}

function close() {
  emit("update:show", false);
}

function selectCommit(commit: GitCommit) {
  emit("select-commit", commit);
  close();
}

function selectBranch(branch: GitBranch) {
  emit("select-branch", branch);
  close();
}

function resetLoadingState() {
  loadingCommits.value = false;
  loadingBranches.value = false;
}

async function loadData() {
  const requestId = ++loadRequestId;
  loadingCommits.value = true;
  loadingBranches.value = true;
  commits.value = [];
  branches.value = [];

  const commitsPromise = gitLog(props.box, props.directory, props.token, 50)
    .then((response) => {
      if (requestId !== loadRequestId) return;
      commits.value = response.is_repo ? response.commits : [];
    })
    .catch(() => {
      if (requestId !== loadRequestId) return;
      commits.value = [];
    })
    .finally(() => {
      if (requestId === loadRequestId) {
        loadingCommits.value = false;
      }
    });

  const branchesPromise = gitBranches(props.box, props.directory, props.token)
    .then((response) => {
      if (requestId !== loadRequestId) return;
      branches.value = response.is_repo ? response.branches : [];
    })
    .catch(() => {
      if (requestId !== loadRequestId) return;
      branches.value = [];
    })
    .finally(() => {
      if (requestId === loadRequestId) {
        loadingBranches.value = false;
      }
    });

  await Promise.allSettled([commitsPromise, branchesPromise]);
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.show) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    close();
  }
}

watch(
  () => props.show,
  (showing) => {
    if (showing) {
      document.addEventListener("keydown", handleKeydown, true);
      return;
    }
    document.removeEventListener("keydown", handleKeydown, true);
  },
  { immediate: true },
);

watch(
  () => [props.show, props.box, props.directory, props.token] as const,
  ([showing, box, directory, token], previous) => {
    if (!showing) {
      loadRequestId += 1;
      resetLoadingState();
      return;
    }

    const [wasShowing, prevBox, prevDirectory, prevToken] = previous ?? [false, "", "", null];
    const shouldLoad = !wasShowing || box !== prevBox || directory !== prevDirectory || token !== prevToken;

    if (!wasShowing) {
      activeTab.value = "commits";
    }

    if (shouldLoad) {
      void loadData();
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  document.removeEventListener("keydown", handleKeydown, true);
});
</script>

<template>
  <div v-if="show" class="git-log-backdrop" @click.self="close">
    <div class="git-log-panel" role="dialog" aria-modal="true">
      <div class="git-log-header">
        <span class="git-log-title">{{ translateOrFallback("commander.git_history", "Git history") }}</span>
        <button type="button" class="git-log-close" @click="close">&times;</button>
      </div>

      <NTabs v-model:value="activeTab" type="segment" size="small" animated class="git-log-tabs">
        <NTabPane name="commits" :tab="translateOrFallback('commander.git_commits', 'Commits')">
          <div v-if="loadingCommits" class="git-log-loading">
            <NSpin size="small" />
          </div>

          <div v-else class="git-log-list">
            <div
              v-for="commit in commits"
              :key="commit.hash"
              class="git-log-row"
              @click="selectCommit(commit)"
            >
              <span class="commit-hash">{{ commit.short_hash }}</span>
              <span class="commit-msg">{{ truncate(commit.message, 50) }}</span>
              <span class="commit-author">{{ commit.author }}</span>
              <span class="commit-date">{{ relativeDate(commit.date) }}</span>
            </div>

            <div v-if="commits.length === 0" class="git-log-empty">
              {{ translateOrFallback("commander.no_commits", "No commits") }}
            </div>
          </div>
        </NTabPane>

        <NTabPane name="branches" :tab="translateOrFallback('commander.git_branches', 'Branches')">
          <div v-if="loadingBranches" class="git-log-loading">
            <NSpin size="small" />
          </div>

          <div v-else class="git-log-list">
            <div
              v-for="branch in branches"
              :key="branch.name"
              class="git-log-row branch-row"
              @click="selectBranch(branch)"
            >
              <PhCheck v-if="branch.is_current" weight="duotone" class="branch-check" />
              <PhGitBranch v-else weight="duotone" class="branch-icon" />
              <span class="branch-name" :class="{ current: branch.is_current }">{{ branch.name }}</span>
              <span v-if="branch.last_commit" class="branch-commit">{{ truncate(branch.last_commit, 24) }}</span>
            </div>

            <div v-if="branches.length === 0" class="git-log-empty">
              {{ translateOrFallback("commander.no_branches", "No branches") }}
            </div>
          </div>
        </NTabPane>
      </NTabs>
    </div>
  </div>
</template>

<style scoped>
.git-log-backdrop {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  justify-content: center;
  padding-top: 80px;
}

.git-log-panel {
  width: 480px;
  max-width: calc(100vw - 24px);
  max-height: 400px;
  background: #0d0d14;
  border: 1px solid #1a1a24;
  border-radius: 6px;
  font-family: var(--font-mono);
  color: #e0e0e6;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
}

.git-log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #1a1a24;
}

.git-log-title {
  font-size: 13px;
  font-weight: 600;
}

.git-log-close {
  background: none;
  border: none;
  color: #f87171;
  cursor: pointer;
  font-size: 16px;
  font-family: var(--font-mono);
  padding: 2px 6px;
  line-height: 1;
}

.git-log-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.git-log-list {
  overflow-y: auto;
  max-height: 300px;
}

.git-log-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  cursor: pointer;
  font-size: 12px;
  border-bottom: 1px solid #111118;
}

.git-log-row:hover {
  background: #111118;
}

.commit-hash {
  color: #a78bfa;
  flex-shrink: 0;
  width: 58px;
}

.commit-msg {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.commit-author {
  color: #666;
  flex-shrink: 0;
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.commit-date {
  color: #555;
  flex-shrink: 0;
  width: 60px;
  text-align: right;
  font-size: 11px;
}

.branch-row {
  gap: 6px;
}

.branch-check {
  color: #4ade80;
  flex-shrink: 0;
}

.branch-icon {
  color: #555;
  flex-shrink: 0;
}

.branch-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.branch-name.current {
  color: #4ade80;
  font-weight: 600;
}

.branch-commit {
  color: #555;
  font-size: 11px;
  flex-shrink: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.git-log-empty {
  padding: 24px;
  text-align: center;
  color: #555;
  font-size: 12px;
}

.git-log-tabs {
  min-height: 0;
  padding: 8px;
}

.git-log-tabs :deep(.n-tabs-nav) {
  margin-bottom: 8px;
}

.git-log-tabs :deep(.n-tabs-nav-scroll-content) {
  background: #111118;
  border: 1px solid #1a1a24;
  border-radius: 6px;
  padding: 2px;
}

.git-log-tabs :deep(.n-tabs-tab) {
  color: #8b8b95;
  font-family: var(--font-mono);
  font-size: 12px;
}

.git-log-tabs :deep(.n-tabs-tab.n-tabs-tab--active) {
  color: #e0e0e6;
  background: #181824;
}

.git-log-tabs :deep(.n-tab-pane) {
  min-height: 0;
}

@media (max-width: 640px) {
  .git-log-backdrop {
    padding: 12px;
    align-items: flex-start;
  }

  .git-log-panel {
    width: 100%;
    max-height: min(400px, calc(100vh - 24px));
  }

  .commit-author,
  .branch-commit {
    display: none;
  }
}
</style>
