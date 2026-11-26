<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useMessage } from "naive-ui";
import { NButton, NIcon, NInput, NSelect } from "naive-ui";
import {
  PhArrowClockwise, PhArrowLeft, PhClipboard, PhHouse, PhPencilSimple, PhStar, PhTerminalWindow,
} from "@phosphor-icons/vue";
import type { GitInfo } from "@/api/types";
import GitBadge from "@/components/GitBadge.vue";
import { useI18n } from "@/i18n";
import { isPathRoot, pathSegments } from "@/utils/pathParts";

const props = defineProps<{
  currentDir: string;
  gitInfo: GitInfo | null;
  selectedBox: string | null;
  refreshing: boolean;
  wslDistro?: string | null;
  boxOptions?: Array<{ label: string; value: string }>;
  boxesLoading?: boolean;
  /** "online", "offline" or "unknown" (not asked yet). */
  status?: string;
  statusText?: string | null;
  isFavorite?: boolean;
}>();

const emit = defineEmits<{
  (e: "navigate-home"): void;
  (e: "navigate-up"): void;
  (e: "navigate", path: string): void;
  (e: "update:box", name: string): void;
  (e: "toggle-favorite"): void;
  (e: "reload"): void;
  (e: "open-terminal"): void;
}>();

const { t } = useI18n();
const message = useMessage();

const segments = computed(() => pathSegments(props.currentDir));

const statusClass = computed(() =>
  props.status === "online" ? "online" : props.status === "offline" ? "offline" : "unknown",
);
const statusTitle = computed(() => {
  if (props.status === "online") return t("files.status_online");
  if (props.status === "offline") {
    return props.statusText ? `${t("files.status_offline")}: ${props.statusText}` : t("files.status_offline");
  }
  return "";
});

// The path edit: the crumbs turn into one text field; Enter goes there, Escape or leaving drops it.
const editing = ref(false);
const draft = ref("");
const inputRef = ref<{ focus?: () => void } | null>(null);

const startEdit = async () => {
  draft.value = props.currentDir;
  editing.value = true;
  await nextTick();
  inputRef.value?.focus?.();
};
const commitEdit = () => {
  const path = draft.value.trim();
  editing.value = false;
  if (path) emit("navigate", path);
};

const copyWindowsPath = async () => {
  if (!props.wslDistro) return;
  const winPath = `\\\\wsl$\\${props.wslDistro}${props.currentDir.replace(/\//g, '\\')}`;
  await navigator.clipboard.writeText(winPath);
  message.success(t('files.wsl_path_copied'));
};
</script>

<template>
  <div class="workspace-bar" data-testid="files-workspace-bar">
    <div class="bar-group bar-box">
      <span
        class="status-dot"
        :class="statusClass"
        role="img"
        :title="statusTitle"
        :aria-label="statusTitle || undefined"
        data-testid="box-status-dot"
      />
      <NSelect
        class="box-select"
        size="small"
        :value="selectedBox"
        :options="boxOptions ?? []"
        :placeholder="t('files.choose_box')"
        :disabled="boxesLoading"
        @update:value="(name: string) => emit('update:box', name)"
      />
      <NButton size="small" quaternary @click="emit('navigate-home')" :title="t('common.home')">
        <NIcon size="16"><PhHouse weight="duotone" /></NIcon>
      </NButton>
      <NButton size="small" quaternary @click="emit('navigate-up')" :disabled="isPathRoot(currentDir)" :title="t('common.up')">
        <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
      </NButton>
    </div>

    <div class="bar-group bar-path">
      <NInput
        v-if="editing"
        ref="inputRef"
        v-model:value="draft"
        class="path-input"
        size="small"
        :placeholder="t('files.dir_placeholder')"
        @keyup.enter="commitEdit"
        @keyup.esc="editing = false"
        @blur="editing = false"
      />
      <nav v-else class="crumbs" data-testid="path-crumbs">
        <template v-for="(segment, index) in segments" :key="segment.path">
          <span v-if="index > 0 && !segments[index - 1]?.label.endsWith('/')" class="crumb-sep" aria-hidden="true">/</span>
          <button
            type="button"
            class="crumb"
            :class="{ current: index === segments.length - 1 }"
            :aria-current="index === segments.length - 1 ? 'page' : undefined"
            @click="emit('navigate', segment.path)"
          >{{ segment.label }}</button>
        </template>
      </nav>
      <NButton v-if="!editing" size="tiny" quaternary :disabled="!selectedBox" :title="t('files.dir_placeholder')" class="path-edit-btn" @click="startEdit">
        <NIcon size="14"><PhPencilSimple weight="duotone" /></NIcon>
      </NButton>
    </div>

    <div class="bar-group bar-actions">
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
      <NButton
        size="small"
        quaternary
        :disabled="!selectedBox"
        :title="isFavorite ? t('files.unfavorite') : t('files.favorite')"
        :class="{ 'is-favorite': isFavorite }"
        class="favorite-btn"
        @click="emit('toggle-favorite')"
      >
        <NIcon size="16"><PhStar :weight="isFavorite ? 'fill' : 'duotone'" /></NIcon>
      </NButton>
      <NButton size="small" quaternary @click="emit('reload')" :disabled="!selectedBox || refreshing" :loading="refreshing" :title="t('common.refresh')">
        <NIcon size="16"><PhArrowClockwise weight="duotone" /></NIcon>
      </NButton>
      <NButton size="small" quaternary @click="emit('open-terminal')" :disabled="!selectedBox" :title="t('terminal.open_terminal')">
        <NIcon size="16"><PhTerminalWindow weight="duotone" /></NIcon>
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.workspace-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  min-width: 0;
}

.bar-group {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.bar-path {
  flex: 1 1 auto;
}

.bar-actions {
  flex: 0 0 auto;
}

.box-select {
  width: 190px;
}

.status-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
  transition: background-color var(--dur-base) var(--ease-out);
}

.status-dot.online {
  background: var(--success);
}

.status-dot.offline {
  background: var(--error);
}

.crumbs {
  display: flex;
  align-items: center;
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  scrollbar-width: none;
  font-family: var(--font-mono);
  font-size: 13px;
}

.crumbs::-webkit-scrollbar {
  display: none;
}

.crumb {
  background: none;
  border: none;
  padding: 2px 4px;
  border-radius: 4px;
  color: var(--text-secondary);
  font: inherit;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}

.crumb:hover {
  background: var(--surface-hover);
  color: var(--text);
}

.crumb.current {
  color: var(--text);
  font-weight: 600;
}

.crumb:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.crumb-sep {
  color: var(--muted);
}

.path-input {
  flex: 1 1 auto;
  font-family: var(--font-mono);
}

.favorite-btn.is-favorite {
  color: var(--warning);
}

.wsl-copy-btn {
  font-size: 11px;
  padding: 0 6px;
  height: 22px;
  opacity: 0.6;
  transition: opacity var(--dur-fast) var(--ease-out);
}

.wsl-copy-btn:hover {
  opacity: 1;
}

.wsl-label {
  margin-left: 3px;
  font-size: 10px;
  font-family: var(--font-mono);
}

/* The phone: the box row, then the path, then the actions; each scrolls rather than wraps. */
@media (max-width: 767px) {
  .workspace-bar {
    flex-wrap: wrap;
    gap: 4px 8px;
    padding: 4px 8px;
  }

  .bar-box {
    flex: 1 1 100%;
  }

  .box-select {
    flex: 1 1 auto;
    width: auto;
    min-width: 0;
  }

  .bar-path {
    flex: 1 1 0;
  }
}
</style>
