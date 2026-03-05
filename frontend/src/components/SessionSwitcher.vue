<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { NButton, NIcon, NSpin, NTooltip, NPopconfirm, NEmpty } from 'naive-ui'
import { PhArrowsClockwise, PhTerminalWindow, PhTrash, PhCircle, PhPencilSimple, PhCheck, PhX } from '@phosphor-icons/vue'
import type { ApiSession } from '@/api/types'
import { fetchBoxSessions, syncBoxSessions, deleteSession, renameSession } from '@/api/http'
import { useI18n } from '@/i18n'

interface Props {
  boxName: string
  token: string | null
  currentSession?: string
}

interface Emits {
  (e: 'select', session: ApiSession): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
const { t } = useI18n()

const sessions = ref<ApiSession[]>([])
const loading = ref(false)
const syncing = ref(false)
const editingId = ref<string | null>(null)
const editName = ref('')

const sortedSessions = computed(() =>
  [...sessions.value].sort((a, b) => b.last_accessed_at - a.last_accessed_at)
)

async function load() {
  loading.value = true
  try {
    sessions.value = await fetchBoxSessions(props.boxName, props.token)
  } catch {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

async function sync() {
  syncing.value = true
  try {
    sessions.value = await syncBoxSessions(props.boxName, props.token)
  } catch {
    // keep existing
  } finally {
    syncing.value = false
  }
}

async function kill(session: ApiSession) {
  try {
    await deleteSession(props.boxName, session.id, props.token, true)
    sessions.value = sessions.value.filter(s => s.id !== session.id)
  } catch {
    // ignore
  }
}

function startRename(session: ApiSession) {
  editingId.value = session.id
  editName.value = session.session_name
}

function cancelRename() {
  editingId.value = null
  editName.value = ''
}

async function confirmRename(session: ApiSession) {
  const newName = editName.value.trim()
  if (!newName || newName === session.session_name) {
    cancelRename()
    return
  }
  try {
    const updated = await renameSession(props.boxName, session.id, newName, props.token)
    const idx = sessions.value.findIndex(s => s.id === session.id)
    if (idx !== -1) sessions.value[idx] = updated
    editingId.value = null
  } catch {
    // keep editing state on error
  }
}

function formatTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

onMounted(load)
watch(() => props.boxName, load)
</script>

<template>
  <div class="session-switcher">
    <div class="session-header">
      <span class="session-title">{{ t('sessions.title') }}</span>
      <NTooltip trigger="hover">
        <template #trigger>
          <NButton size="tiny" quaternary :loading="syncing" @click="sync">
            <NIcon size="14"><PhArrowsClockwise weight="duotone" /></NIcon>
          </NButton>
        </template>
        {{ t('sessions.sync_tooltip') }}
      </NTooltip>
    </div>

    <NSpin v-if="loading" size="small" />

    <NEmpty v-else-if="sortedSessions.length === 0" :description="t('sessions.empty')" size="small" />

    <div v-else class="session-list">
      <div
        v-for="session in sortedSessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.session_name === currentSession, inactive: !session.active }"
        @click="emit('select', session)"
      >
        <div class="session-info">
          <div class="session-name">
            <NIcon size="10" :color="session.active ? '#52c41a' : '#666'">
              <PhCircle weight="fill" />
            </NIcon>
            <template v-if="editingId === session.id">
              <input
                v-model="editName"
                class="rename-input"
                @click.stop
                @keyup.enter.stop="confirmRename(session)"
                @keyup.escape.stop="cancelRename"
              />
            </template>
            <span v-else>{{ session.session_name }}</span>
          </div>
          <div class="session-meta">
            {{ session.working_directory.split('/').pop() || '~' }}
            <span class="session-time">{{ formatTime(session.last_accessed_at) }}</span>
          </div>
        </div>
        <div class="session-actions" @click.stop>
          <template v-if="editingId === session.id">
            <NButton size="tiny" quaternary type="primary" @click.stop="confirmRename(session)">
              <NIcon size="12"><PhCheck weight="bold" /></NIcon>
            </NButton>
            <NButton size="tiny" quaternary @click.stop="cancelRename">
              <NIcon size="12"><PhX weight="bold" /></NIcon>
            </NButton>
          </template>
          <template v-else>
            <NButton size="tiny" quaternary @click.stop="startRename(session)" :title="t('sessions.rename')">
              <NIcon size="12"><PhPencilSimple weight="duotone" /></NIcon>
            </NButton>
            <NPopconfirm @positive-click.stop="kill(session)">
              <template #trigger>
                <NButton size="tiny" quaternary type="error" @click.stop>
                  <NIcon size="12"><PhTrash weight="duotone" /></NIcon>
                </NButton>
              </template>
              {{ t('sessions.kill_confirm', { name: session.session_name }) }}
            </NPopconfirm>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.session-switcher {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  min-width: 200px;
  max-height: 300px;
}

.session-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.session-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.session-item:hover {
  background: var(--surface-hover);
}

.session-item.active {
  background: rgba(136, 58, 234, 0.15);
  border: 1px solid rgba(136, 58, 234, 0.3);
}

.session-item.inactive {
  opacity: 0.5;
}

.session-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.session-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.session-meta {
  font-size: 11px;
  color: var(--muted);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-time {
  margin-left: 8px;
  opacity: 0.7;
}

.session-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.rename-input {
  flex: 1;
  min-width: 0;
  padding: 2px 6px;
  font-size: 13px;
  font-weight: 500;
  background: var(--surface-variant);
  border: 1px solid var(--accent);
  border-radius: 4px;
  color: var(--text);
  outline: none;
}
</style>
