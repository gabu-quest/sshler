<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NIcon, NModal, NSpace, NSpin, NTag, useMessage } from "naive-ui";
import { PhTerminalWindow, PhArrowCounterClockwise, PhX, PhFolder, PhGear } from "@phosphor-icons/vue";
import type { LostSession } from "@/api/types";
import { recreateSession, dismissRecovery, dismissRecoverySession } from "@/api/http";
import { useI18n } from "@/i18n";

const router = useRouter();

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
const recreating = ref<Set<string>>(new Set());
const recreatingAll = ref(false);

function timeAgo(timestamp: number): string {
  const seconds = Math.floor(Date.now() / 1000 - timestamp);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

async function handleRecreate(session: LostSession, navigate = true) {
  recreating.value.add(session.id);
  try {
    await recreateSession(session.id, props.token);
    message.success(t("recovery.recreated", { name: session.session_name }));
    const remaining = props.sessions.filter(s => s.id !== session.id);
    emit("updated", remaining);
    if (remaining.length === 0) {
      emit("update:show", false);
    }
    if (navigate) {
      router.push(`/terminal?box=${encodeURIComponent(session.box)}&session=${encodeURIComponent(session.session_name)}&dir=${encodeURIComponent(session.working_directory)}`);
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "Failed to recreate session");
  } finally {
    recreating.value.delete(session.id);
  }
}

async function handleRecreateAll() {
  recreatingAll.value = true;
  try {
    for (const session of props.sessions) {
      await recreateSession(session.id, props.token);
    }
    message.success(t("recovery.all_recreated"));
    emit("updated", []);
    emit("update:show", false);
    // Navigate to the first session's terminal
    const first = props.sessions[0];
    if (first) {
      router.push(`/terminal?box=${encodeURIComponent(first.box)}&session=${encodeURIComponent(first.session_name)}&dir=${encodeURIComponent(first.working_directory)}`);
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "Failed to recreate sessions");
  } finally {
    recreatingAll.value = false;
  }
}

async function handleSkip(session: LostSession) {
  try {
    await dismissRecoverySession(session.id, props.token);
  } catch { /* best effort */ }
  const remaining = props.sessions.filter(s => s.id !== session.id);
  emit("updated", remaining);
  if (remaining.length === 0) {
    emit("update:show", false);
  }
}

async function handleDismiss() {
  try {
    await dismissRecovery(props.token);
    message.info(t("recovery.dismissed"));
  } catch {
    // Best effort
  }
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
    style="max-width: 640px; max-height: 80vh"
    @update:show="emit('update:show', $event)"
  >
    <p style="margin: 0 0 16px; opacity: 0.7">{{ t('recovery.description') }}</p>

    <div style="display: flex; flex-direction: column; gap: 12px; max-height: 50vh; overflow-y: auto">
      <div
        v-for="session in sessions"
        :key="session.id"
        style="border: 1px solid var(--n-border-color, #e0e0e6); border-radius: 8px; padding: 12px"
      >
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px">
          <div style="display: flex; align-items: center; gap: 8px">
            <NIcon :size="20"><PhTerminalWindow weight="duotone" /></NIcon>
            <strong>{{ session.session_name }}</strong>
            <NTag size="small" :bordered="false">
              {{ t('recovery.windows_count', { count: session.windows.length }) }}
            </NTag>
            <NTag size="small" :bordered="false" type="warning">
              {{ t('recovery.last_seen', { time: timeAgo(session.last_snapshot_at) }) }}
            </NTag>
          </div>
          <NSpace :size="4">
            <NButton
              size="small"
              type="primary"
              :loading="recreating.has(session.id)"
              :disabled="recreatingAll"
              @click="handleRecreate(session)"
            >
              <template #icon><NIcon><PhArrowCounterClockwise weight="duotone" /></NIcon></template>
              {{ t('recovery.recreate') }}
            </NButton>
            <NButton
              size="small"
              quaternary
              :disabled="recreatingAll"
              @click="handleSkip(session)"
            >
              {{ t('recovery.skip') }}
            </NButton>
          </NSpace>
        </div>

        <div style="display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: 0.8">
          <div v-for="win in session.windows" :key="win.index" style="display: flex; align-items: center; gap: 6px; padding: 2px 0">
            <NIcon :size="14"><PhGear weight="duotone" /></NIcon>
            <span style="font-family: monospace">{{ win.command }}</span>
            <span style="opacity: 0.5">in</span>
            <NIcon :size="14"><PhFolder weight="duotone" /></NIcon>
            <span style="font-family: monospace; word-break: break-all">{{ win.path }}</span>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <NSpace justify="end">
        <NButton
          quaternary
          @click="handleDismiss"
          :disabled="recreatingAll"
        >
          <template #icon><NIcon><PhX weight="duotone" /></NIcon></template>
          {{ t('recovery.dismiss_all') }}
        </NButton>
        <NButton
          type="primary"
          :loading="recreatingAll"
          @click="handleRecreateAll"
        >
          <template #icon><NIcon><PhArrowCounterClockwise weight="duotone" /></NIcon></template>
          {{ t('recovery.recreate_all') }}
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>
