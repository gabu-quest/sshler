<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "@/i18n";

import {
  NButton,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NIcon,
  NInput,
  NModal,
  NPopover,
  NSpin,
  NTag,
  NTimeline,
  NTimelineItem,
  useMessage,
} from "naive-ui";
import {
  PhArrowCounterClockwise,
  PhArrowLineUpRight,
  PhArrowsClockwise,
  PhArrowsIn,
  PhArrowsOut,
  PhCopy,
  PhFileText,
  PhFolder,
  PhGitBranch,
  PhPlay,
  PhRobot,
  PhSlidersHorizontal,
  PhTag,
} from "@phosphor-icons/vue";

import { openClaudeSession } from "@/api/http";
import type { ClaudeSession } from "@/api/types";
import { useBootstrapStore } from "@/stores/bootstrap";
import {
  DEFAULT_RESUME_TEMPLATE,
  useClaudeSessionsStore,
} from "@/stores/claudeSessions";
import { getEmojiForString } from "@/utils/emoji-favicon";
import { getColorForSession } from "@/utils/sessionName";

const { t } = useI18n();
const router = useRouter();
const message = useMessage();
const store = useClaudeSessionsStore();
const bootstrapStore = useBootstrapStore();

const token = computed(
  () => bootstrapStore.token || bootstrapStore.payload?.token || null,
);

const filter = ref("");
const resumingId = ref<string | null>(null);

// Global resume-command template (draft edited in the header popover).
const globalDraft = ref(store.resumeTemplate);
function saveGlobalTemplate() {
  store.setGlobalTemplate(globalDraft.value);
  globalDraft.value = store.resumeTemplate;
  message.success(t("claudeSessions.command.saved"));
}
function resetGlobalTemplate() {
  store.setGlobalTemplate(DEFAULT_RESUME_TEMPLATE);
  globalDraft.value = store.resumeTemplate;
}

// Per-session override editor (single modal, reused per row).
const showOverrideEditor = ref(false);
const editingSession = ref<ClaudeSession | null>(null);
const overrideDraft = ref("");
function openOverrideEditor(session: ClaudeSession) {
  editingSession.value = session;
  overrideDraft.value = store.resumeOverrides[session.id] ?? "";
  showOverrideEditor.value = true;
}
function saveOverride() {
  if (editingSession.value) store.setOverride(editingSession.value.id, overrideDraft.value);
  showOverrideEditor.value = false;
}
function clearOverride() {
  if (editingSession.value) store.setOverride(editingSession.value.id, null);
  overrideDraft.value = "";
  showOverrideEditor.value = false;
}

const filtered = computed<ClaudeSession[]>(() => {
  const query = filter.value.trim().toLowerCase();
  if (!query) return store.sessions;
  return store.sessions.filter((session) => {
    const haystack = [
      session.title,
      session.cwd,
      session.project_dir,
      session.git_branch ?? "",
      session.last_prompt ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
});

interface SessionGroup {
  key: string; // full grouping path (repo root, or cwd fallback) — stable + unique
  label: string; // display name (repo basename)
  emoji: string; // same per-directory emoji the rest of the app uses (local:<path>)
  color: string; // deterministic accent per repo
  totalBytes: number; // summed transcript size across the repo's sessions
  lastActive: number; // most-recent activity in the repo
  sessions: ClaudeSession[];
}

/** Last path segment of a directory (the human-readable repo/dir name). */
function baseName(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const idx = trimmed.lastIndexOf("/");
  return (idx >= 0 ? trimmed.slice(idx + 1) : trimmed) || path;
}

/** Subdirectory of a session relative to its repo root, or "" if at the root. */
function subdirLabel(session: ClaudeSession): string {
  const root = session.repo_root;
  if (!root || !session.cwd || session.cwd === root) return "";
  return session.cwd.startsWith(`${root}/`) ? session.cwd.slice(root.length + 1) : "";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

type TimelineType = "default" | "success" | "info" | "warning" | "error";
/** Recency → timeline dot color: green <1h, blue <1d, amber <1wk, grey older. */
function recencyType(ts: number): TimelineType {
  const delta = Date.now() / 1000 - ts;
  if (delta < 3600) return "success";
  if (delta < 86400) return "info";
  if (delta < 7 * 86400) return "warning";
  return "default";
}

/**
 * Group by git repo root (matching Claude Code's `/resume`, which collapses
 * subdirectories under their repo). Sessions with no repo fall back to their
 * exact cwd. Store ordering (most-recently-active first) is preserved.
 */
const groups = computed<SessionGroup[]>(() => {
  const order: string[] = [];
  const map = new Map<string, ClaudeSession[]>();
  for (const session of filtered.value) {
    const key = session.repo_root || session.cwd || session.project_dir;
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(session);
  }
  return order.map((key) => {
    const sessions = map.get(key)!;
    return {
      key,
      label: baseName(key),
      // Match the directory emoji used across the app (favicon, favorites,
      // command palette): keyed by `local:<path>`.
      emoji: getEmojiForString(`local:${key}`),
      color: getColorForSession(key),
      totalBytes: sessions.reduce((sum, s) => sum + s.size_bytes, 0),
      lastActive: sessions.reduce((max, s) => Math.max(max, s.last_active), 0),
      sessions,
    };
  });
});

// Repos start collapsed. A live filter expands every matching repo so search
// hits are never hidden inside a collapsed section; clearing the filter
// collapses them again. Expand-all / collapse-all buttons give manual control.
const expandedNames = ref<string[]>([]);
watch(filter, (query) => {
  expandedNames.value = query.trim() ? groups.value.map((g) => g.key) : [];
});

function expandAll(): void {
  expandedNames.value = groups.value.map((g) => g.key);
}
function collapseAll(): void {
  expandedNames.value = [];
}

const isEmpty = computed(() => store.sessions.length === 0);
const hasNoMatch = computed(
  () => !isEmpty.value && filtered.value.length === 0,
);

function formatRelative(ts: number): string {
  const delta = Math.max(0, Date.now() / 1000 - ts);
  if (delta < 60) return `${Math.round(delta)}s ago`;
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}

async function handleRefresh() {
  await store.refresh(token.value);
  if (store.error) {
    message.error(t("claudeSessions.load_error", { reason: store.error }));
  }
}

/**
 * Resume a session into its tmux window. Default (`navigate: false`) opens it in
 * the background and stays on this list; `navigate: true` also switches to the
 * terminal. Either way the window is created + the resume command is typed.
 */
async function handleResume(session: ClaudeSession, navigate = false) {
  resumingId.value = session.id;
  try {
    const result = await openClaudeSession(
      session.id,
      token.value,
      store.templateFor(session.id),
    );
    if (result.already_open) {
      message.info(t("claudeSessions.already_open", { dir: result.session_name }));
    } else {
      message.success(t("claudeSessions.opened", { name: result.session_name }));
    }
    if (navigate) {
      router.push({
        name: "terminal",
        query: {
          box: result.box,
          dir: result.working_directory,
          session: result.session_name,
        },
      });
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err));
  } finally {
    resumingId.value = null;
  }
}

async function handleCopy(session: ClaudeSession) {
  const command = `claude --resume ${session.id}`;
  try {
    await navigator.clipboard.writeText(command);
    message.success(t("claudeSessions.copied"));
  } catch {
    message.error(t("claudeSessions.copy_failed", { command }));
  }
}

const onFocus = () => handleRefresh();

onMounted(() => {
  handleRefresh();
  window.addEventListener("focus", onFocus);
});

onUnmounted(() => {
  window.removeEventListener("focus", onFocus);
});
</script>

<template>
  <div class="claude-view">
    <header class="claude-view__header">
      <div class="claude-view__title">
        <NIcon size="22" :component="PhRobot" />
        <h1>{{ t("claudeSessions.title") }}</h1>
      </div>
      <div class="claude-view__meta">
        <span class="claude-view__count">
          {{ t(
            store.sessions.length === 1
              ? "claudeSessions.session_count_one"
              : "claudeSessions.session_count",
            { n: store.sessions.length },
          ) }}
        </span>
        <NPopover trigger="click" placement="bottom-end" :width="340">
          <template #trigger>
            <NButton
              size="small"
              quaternary
              :title="t('claudeSessions.command.global_label')"
              data-testid="claude-global-cmd"
            >
              <template #icon>
                <NIcon :component="PhSlidersHorizontal" />
              </template>
            </NButton>
          </template>
          <div class="claude-cmd-pop">
            <div class="claude-cmd-pop__label">{{ t("claudeSessions.command.global_label") }}</div>
            <NInput
              v-model:value="globalDraft"
              :placeholder="DEFAULT_RESUME_TEMPLATE"
              data-testid="claude-global-cmd-input"
            />
            <div class="claude-cmd-pop__hint">{{ t("claudeSessions.command.hint") }}</div>
            <div class="claude-cmd-pop__actions">
              <NButton size="tiny" quaternary @click="resetGlobalTemplate">
                <template #icon>
                  <NIcon :component="PhArrowCounterClockwise" />
                </template>
                {{ t("claudeSessions.command.reset") }}
              </NButton>
              <NButton size="tiny" type="primary" @click="saveGlobalTemplate">
                {{ t("claudeSessions.command.save") }}
              </NButton>
            </div>
          </div>
        </NPopover>
        <NButton size="small" :loading="store.loading" @click="handleRefresh">
          <template #icon>
            <NIcon :component="PhArrowsClockwise" />
          </template>
          {{ t("common.refresh") }}
        </NButton>
      </div>
    </header>

    <div class="claude-view__toolbar">
      <NInput
        v-model:value="filter"
        clearable
        data-testid="claude-filter"
        :placeholder="t('claudeSessions.filter_placeholder')"
        class="claude-view__filter"
      />
      <NButton
        size="small"
        quaternary
        :disabled="isEmpty"
        :title="t('claudeSessions.expand_all')"
        data-testid="claude-expand-all"
        @click="expandAll"
      >
        <template #icon>
          <NIcon :component="PhArrowsOut" />
        </template>
      </NButton>
      <NButton
        size="small"
        quaternary
        :disabled="isEmpty"
        :title="t('claudeSessions.collapse_all')"
        data-testid="claude-collapse-all"
        @click="collapseAll"
      >
        <template #icon>
          <NIcon :component="PhArrowsIn" />
        </template>
      </NButton>
    </div>

    <div v-if="store.loading && isEmpty" class="claude-view__center">
      <NSpin />
    </div>

    <div v-else-if="isEmpty" class="claude-view__center">
      <NEmpty :description="t('claudeSessions.empty')">
        <template #extra>
          <code class="claude-view__hint">{{ t("claudeSessions.empty_hint") }}</code>
        </template>
      </NEmpty>
    </div>

    <div v-else-if="hasNoMatch" class="claude-view__center">
      <NEmpty :description="t('claudeSessions.no_match', { query: filter })" />
    </div>

    <div v-else class="claude-view__groups">
      <NCollapse v-model:expanded-names="expandedNames" arrow-placement="right">
        <NCollapseItem
          v-for="group in groups"
          :key="group.key"
          :name="group.key"
        >
          <template #header>
            <div class="claude-group__header">
              <span
                class="claude-group__emoji"
                :style="{ backgroundColor: `${group.color}22` }"
                aria-hidden="true"
              >{{ group.emoji }}</span>
              <span class="claude-group__name" :title="group.key" data-testid="claude-group">{{ group.label }}</span>
            </div>
          </template>
          <template #header-extra>
            <span class="claude-group__summary">
              {{ t(
                group.sessions.length === 1
                  ? "claudeSessions.group.summary_one"
                  : "claudeSessions.group.summary",
                { n: group.sessions.length, size: formatSize(group.totalBytes) },
              ) }}
            </span>
          </template>

          <NTimeline :icon-size="16">
            <NTimelineItem
              v-for="session in group.sessions"
              :key="session.id"
              :type="recencyType(session.last_active)"
              :line-type="recencyType(session.last_active) === 'default' ? 'dashed' : 'default'"
            >
              <div class="claude-row" data-testid="claude-session">
                <div class="claude-row__text">
                  <div class="claude-row__title" data-testid="claude-title">
                    {{ session.title }}
                  </div>
                  <div class="claude-row__sub">
                    <span class="claude-row__time">{{
                      t("claudeSessions.last_active", { time: formatRelative(session.last_active) })
                    }}</span>
                    <NTag
                      v-if="subdirLabel(session)"
                      size="small"
                      round
                      type="info"
                      :bordered="false"
                      :title="`${t('claudeSessions.meta.subdir')}: ${session.cwd}`"
                    >
                      <template #icon>
                        <NIcon :component="PhFolder" />
                      </template>
                      {{ subdirLabel(session) }}
                    </NTag>
                    <NTag
                      v-if="session.git_branch"
                      size="small"
                      round
                      :bordered="false"
                    >
                      <template #icon>
                        <NIcon :component="PhGitBranch" />
                      </template>
                      {{ session.git_branch }}
                    </NTag>
                    <NTag
                      size="small"
                      round
                      :bordered="false"
                      :title="t('claudeSessions.meta.size')"
                    >
                      <template #icon>
                        <NIcon :component="PhFileText" />
                      </template>
                      {{ formatSize(session.size_bytes) }}
                    </NTag>
                    <NTag
                      v-if="session.version"
                      size="small"
                      round
                      :bordered="false"
                      :title="t('claudeSessions.meta.version')"
                    >
                      <template #icon>
                        <NIcon :component="PhTag" />
                      </template>
                      v{{ session.version }}
                    </NTag>
                    <NTag
                      v-if="store.hasOverride(session.id)"
                      size="small"
                      type="warning"
                      round
                      :bordered="false"
                      :title="t('claudeSessions.command.custom_badge')"
                    >
                      {{ t("claudeSessions.command.custom_badge") }}
                    </NTag>
                  </div>
                  <div
                    v-if="session.last_prompt && session.last_prompt !== session.title"
                    class="claude-row__preview"
                    :title="session.last_prompt"
                  >
                    {{ session.last_prompt }}
                  </div>
                </div>
                <div class="claude-row__actions">
                  <NButton
                    size="small"
                    type="primary"
                    :loading="resumingId === session.id"
                    :title="t('claudeSessions.resume_here_hint')"
                    :data-testid="`resume-${session.id}`"
                    @click="handleResume(session, false)"
                  >
                    <template #icon>
                      <NIcon :component="PhPlay" />
                    </template>
                    {{ resumingId === session.id ? t("claudeSessions.resuming") : t("claudeSessions.resume") }}
                  </NButton>
                  <NButton
                    size="small"
                    secondary
                    type="primary"
                    :disabled="resumingId === session.id"
                    :title="t('claudeSessions.resume_open')"
                    :data-testid="`resume-open-${session.id}`"
                    @click="handleResume(session, true)"
                  >
                    <template #icon>
                      <NIcon :component="PhArrowLineUpRight" />
                    </template>
                  </NButton>
                  <NButton
                    size="small"
                    quaternary
                    :title="t('claudeSessions.copy_command')"
                    @click="handleCopy(session)"
                  >
                    <template #icon>
                      <NIcon :component="PhCopy" />
                    </template>
                  </NButton>
                  <NButton
                    size="small"
                    quaternary
                    :type="store.hasOverride(session.id) ? 'warning' : 'default'"
                    :title="t('claudeSessions.command.edit')"
                    :data-testid="`cmd-${session.id}`"
                    @click="openOverrideEditor(session)"
                  >
                    <template #icon>
                      <NIcon :component="PhSlidersHorizontal" />
                    </template>
                  </NButton>
                </div>
              </div>
            </NTimelineItem>
          </NTimeline>
        </NCollapseItem>
      </NCollapse>
    </div>

    <NModal
      v-model:show="showOverrideEditor"
      preset="card"
      :title="t('claudeSessions.command.override_title')"
      style="max-width: 460px"
    >
      <div class="claude-cmd-pop">
        <div v-if="editingSession" class="claude-cmd-pop__sub">{{ editingSession.title }}</div>
        <NInput
          v-model:value="overrideDraft"
          :placeholder="store.resumeTemplate"
          data-testid="claude-override-input"
        />
        <div class="claude-cmd-pop__hint">{{ t("claudeSessions.command.hint") }}</div>
        <div class="claude-cmd-pop__hint">{{ t("claudeSessions.command.override_help") }}</div>
        <div class="claude-cmd-pop__actions">
          <NButton size="small" quaternary @click="clearOverride">
            {{ t("claudeSessions.command.use_global") }}
          </NButton>
          <NButton size="small" type="primary" data-testid="claude-override-save" @click="saveOverride">
            {{ t("claudeSessions.command.save") }}
          </NButton>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.claude-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.claude-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.claude-view__title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.claude-view__title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.claude-view__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
.claude-view__count {
  opacity: 0.7;
}

.claude-view__center {
  padding: 48px 0;
  display: flex;
  justify-content: center;
}

.claude-view__hint {
  display: inline-block;
  background: var(--n-action-color, color-mix(in srgb, currentColor 8%, transparent));
  padding: 4px 8px;
  border-radius: 4px;
  font-family: var(--font-family-mono, monospace);
}

.claude-view__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
}
.claude-view__filter {
  flex: 1;
}

.claude-view__groups {
  display: flex;
  flex-direction: column;
}

/* Repo accordion header: emoji chip + repo name, with a summary on the right. */
.claude-group__header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.claude-group__emoji {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  line-height: 1;
}
.claude-group__name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.claude-group__summary {
  font-size: 12px;
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.claude-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 2px 0;
}
.claude-row__text {
  flex: 1;
  min-width: 0;
}
.claude-row__title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.claude-row__sub {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
}
.claude-row__time {
  opacity: 0.65;
  white-space: nowrap;
}
.claude-row__preview {
  margin-top: 4px;
  font-size: 12px;
  opacity: 0.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.claude-row__actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.claude-cmd-pop {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.claude-cmd-pop__label {
  font-weight: 500;
  font-size: 13px;
}
.claude-cmd-pop__sub {
  font-size: 12px;
  opacity: 0.7;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.claude-cmd-pop__hint {
  font-size: 11px;
  opacity: 0.6;
}
.claude-cmd-pop__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}
</style>
