<script setup lang="ts">
import { ref, computed } from "vue";
import { NButton, NCheckbox, NIcon, NModal, NSpace, NTag, useMessage } from "naive-ui";
import { PhTerminalWindow, PhArrowCounterClockwise, PhX, PhFolder, PhGear, PhArrowSquareOut } from "@phosphor-icons/vue";
import type { LostSession } from "@/api/types";
import { recreateSessionBatch, dismissRecovery, dismissRecoverySession } from "@/api/http";
import { useI18n } from "@/i18n";

const props = defineProps<{
  show: boolean;
  sessions: LostSession[];
  token: string | null;
}>();

const emit = defineEmits<{
  (e: "update:show", value: boolean): void;
  (e: "updated", sessions: LostSession[]): void;
}>();

const { t } = useI18n();
const message = useMessage();
const busy = ref(false);
const selected = ref<Set<string>>(new Set());
// id -> "ok" | error string
const results = ref<Map<string, string>>(new Map());

const pendingSessions = computed(() => props.sessions.filter(s => !results.value.has(s.id)));
const hasSelection = computed(() => {
  return [...selected.value].some(id => !results.value.has(id));
});

function toggleSelected(id: string) {
  const s = new Set(selected.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  selected.value = s;
}

function toggleAll() {
  const pending = pendingSessions.value;
  const allChecked = pending.every(s => selected.value.has(s.id));
  if (allChecked) {
    selected.value = new Set();
  } else {
    selected.value = new Set(pending.map(s => s.id));
  }
}

function totalPanes(session: LostSession): number {
  return session.windows.reduce((sum, w) => sum + (w.panes?.length || 1), 0);
}

function timeAgo(timestamp: number): string {
  const seconds = Math.floor(Date.now() / 1000 - timestamp);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function terminalUrl(session: LostSession): string {
  return `/app/terminal?box=${encodeURIComponent(session.box)}&session=${encodeURIComponent(session.session_name)}&dir=${encodeURIComponent(session.working_directory)}`;
}

function isOk(id: string): boolean {
  return results.value.get(id) === "ok";
}

function isFailed(id: string): boolean {
  const r = results.value.get(id);
  return r !== undefined && r !== "ok";
}

async function handleRecreate(ids: string[]) {
  if (ids.length === 0) return;
  busy.value = true;
  try {
    const resp = await recreateSessionBatch(ids, props.token);
    const newResults = new Map(results.value);
    for (const [id, status] of Object.entries(resp.results)) {
      newResults.set(id, status);
    }
    results.value = newResults;
    // Clear selected for any that succeeded
    const newSelected = new Set([...selected.value].filter(id => !isOk(id)));
    selected.value = newSelected;

    const okCount = Object.values(resp.results).filter(s => s === "ok").length;
    const failCount = Object.values(resp.results).filter(s => s !== "ok").length;
    if (okCount > 0) message.success(`${okCount} session(s) recreated`);
    if (failCount > 0) message.error(`${failCount} session(s) failed`);
  } catch {
    message.error("Request failed");
  } finally {
    busy.value = false;
  }
}

function handleRecreateClick() {
  const ids = hasSelection.value
    ? [...selected.value].filter(id => !results.value.has(id))
    : pendingSessions.value.map(s => s.id);
  handleRecreate(ids);
}

async function handleSkip(session: LostSession) {
  try {
    await dismissRecoverySession(session.id, props.token);
  } catch { /* best effort */ }
  const remaining = props.sessions.filter(s => s.id !== session.id);
  emit("updated", remaining);
  if (remaining.length === 0) emit("update:show", false);
}

async function handleDismiss() {
  try {
    await dismissRecovery(props.token);
    message.info(t("recovery.dismissed"));
  } catch { /* best effort */ }
  emit("updated", []);
  emit("update:show", false);
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="t('recovery.title')"
    :bordered="false"
    :closable="true"
    :mask-closable="false"
    style="max-width: 640px; max-height: 80vh"
    @update:show="emit('update:show', $event)"
  >
    <p style="margin: 0 0 16px; opacity: 0.7">{{ t('recovery.description') }}</p>

    <!-- Select all (only when 2+ pending) -->
    <div v-if="pendingSessions.length > 1" style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
      <NCheckbox
        :checked="pendingSessions.every(s => selected.has(s.id))"
        :indeterminate="selected.size > 0 && !pendingSessions.every(s => selected.has(s.id))"
        @update:checked="toggleAll"
      />
      <span style="font-size: 13px; opacity: 0.7">{{ t('recovery.select_all') }}</span>
    </div>

    <!-- Session list -->
    <div style="display: flex; flex-direction: column; gap: 10px; max-height: 50vh; overflow-y: auto">
      <div
        v-for="session in sessions"
        :key="session.id"
        style="border: 1px solid var(--n-border-color, #e0e0e6); border-radius: 8px; padding: 12px"
        :style="isOk(session.id) ? 'border-color: var(--n-color-success, #18a058)' : isFailed(session.id) ? 'border-color: var(--n-color-error, #d03050)' : ''"
      >
        <!-- Header row -->
        <div style="display: flex; align-items: center; justify-content: space-between">
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0">
            <!-- Checkbox (only for pending) -->
            <NCheckbox
              v-if="!results.has(session.id)"
              :checked="selected.has(session.id)"
              @update:checked="toggleSelected(session.id)"
            />

            <NIcon :size="18"><PhTerminalWindow weight="duotone" /></NIcon>

            <!-- Done: clickable link -->
            <a
              v-if="isOk(session.id)"
              :href="terminalUrl(session)"
              target="_blank"
              style="font-weight: 600; color: var(--n-color-success, #18a058); text-decoration: none"
            >
              {{ session.session_name }}
              <NIcon :size="12" style="vertical-align: middle"><PhArrowSquareOut weight="duotone" /></NIcon>
            </a>
            <!-- Failed: plain with error -->
            <strong v-else-if="isFailed(session.id)" style="color: var(--n-color-error, #d03050)">
              {{ session.session_name }}
            </strong>
            <!-- Pending: plain -->
            <strong v-else>{{ session.session_name }}</strong>

            <NTag size="small" :bordered="false">{{ session.windows.length }}w{{ totalPanes(session) > session.windows.length ? ` ${totalPanes(session)}p` : '' }}</NTag>
            <NTag v-if="isOk(session.id)" size="small" :bordered="false" type="success">ready</NTag>
            <NTag v-else-if="isFailed(session.id)" size="small" :bordered="false" type="error">failed</NTag>
            <NTag v-else size="small" :bordered="false" type="warning">{{ timeAgo(session.last_snapshot_at) }} ago</NTag>
          </div>

          <!-- Skip button (only for non-done) -->
          <NButton
            v-if="!isOk(session.id)"
            size="tiny"
            quaternary
            :disabled="busy"
            @click="handleSkip(session)"
          >
            {{ t('recovery.skip') }}
          </NButton>
        </div>

        <!-- Window details -->
        <div style="display: flex; flex-direction: column; gap: 2px; font-size: 12px; opacity: 0.7; margin-top: 6px">
          <template v-for="win in session.windows" :key="win.index">
            <!-- Multi-pane window: show each pane indented -->
            <template v-if="win.panes && win.panes.length > 1">
              <div style="display: flex; align-items: center; gap: 4px; font-weight: 500">
                <NIcon :size="12"><PhGear weight="duotone" /></NIcon>
                <span>{{ win.name }} ({{ win.panes.length }} panes)</span>
              </div>
              <div v-for="pane in win.panes" :key="pane.index" style="display: flex; align-items: center; gap: 4px; padding-left: 16px">
                <code>{{ pane.command }}</code>
                <span style="opacity: 0.5">in</span>
                <NIcon :size="12"><PhFolder weight="duotone" /></NIcon>
                <code style="word-break: break-all">{{ pane.path }}</code>
              </div>
            </template>
            <!-- Single pane: flat display -->
            <div v-else style="display: flex; align-items: center; gap: 4px">
              <NIcon :size="12"><PhGear weight="duotone" /></NIcon>
              <code>{{ win.command }}</code>
              <span style="opacity: 0.5">in</span>
              <NIcon :size="12"><PhFolder weight="duotone" /></NIcon>
              <code style="word-break: break-all">{{ win.path }}</code>
            </div>
          </template>
        </div>
      </div>
    </div>

    <template #footer>
      <NSpace justify="end">
        <NButton quaternary :disabled="busy" @click="handleDismiss">
          <template #icon><NIcon><PhX weight="duotone" /></NIcon></template>
          {{ t('recovery.dismiss_all') }}
        </NButton>
        <NButton
          v-if="pendingSessions.length > 0"
          type="primary"
          :loading="busy"
          @click="handleRecreateClick"
        >
          <template #icon><NIcon><PhArrowCounterClockwise weight="duotone" /></NIcon></template>
          {{ hasSelection ? t('recovery.recreate_selected', { count: [...selected].filter(id => !results.has(id)).length }) : t('recovery.recreate_all') }}
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>
