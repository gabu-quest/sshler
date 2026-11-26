<script setup lang="ts">
import { errorText } from '@/api/apiError'
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { NButton, NIcon, NSpin, NTooltip, NPopconfirm, NEmpty, useMessage } from 'naive-ui'
import { PhArrowsClockwise, PhTrash, PhCircle, PhPencilSimple, PhCheck, PhX, PhHouse, PhSkull } from '@phosphor-icons/vue'
import type { ApiSession } from '@/api/types'
import { fetchBoxSessions, syncBoxSessions, deleteSession, renameSession, recreateSession } from '@/api/http'
import { useI18n } from '@/i18n'
import { getColorForSession } from '@/utils/sessionName'

interface Props {
  boxName: string
  token: string | null
  currentSession?: string
  homeSession?: string
}

interface Emits {
  (e: 'select', session: ApiSession): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()
const { t } = useI18n()
const message = useMessage()

const sessions = ref<ApiSession[]>([])
const loading = ref(false)
const syncing = ref(false)
const editingId = ref<string | null>(null)
const editName = ref('')
// The message of a failed load, shown where "no sessions" would be.
const loadError = ref<string | null>(null)

const effectiveHome = computed(() => props.homeSession ?? 'main')

const homeSessionData = computed(() =>
  sessions.value.find(s => s.session_name === effectiveHome.value) ?? null
)

// Sessions list excluding the home session (pinned separately at top)
const sortedSessions = computed(() =>
  [...sessions.value]
    .filter(s => s.session_name !== effectiveHome.value)
    .sort((a, b) => {
      if (a.active !== b.active) return a.active ? -1 : 1
      return b.last_accessed_at - a.last_accessed_at
    })
)

function goHome() {
  if (homeSessionData.value) {
    emit('select', homeSessionData.value)
  } else {
    emit('select', {
      id: '',
      box: props.boxName,
      session_name: effectiveHome.value,
      working_directory: '~',
      created_at: 0,
      last_accessed_at: 0,
      active: false,
      window_count: 0,
      metadata: {},
      restorable: false,
    })
  }
}

// Loaded by a sync, as the overview's card is, so a session tmux no longer has shows
// as not running here too. When the sync fails (a box that cannot be reached, a box
// without tmux) the stored sessions are listed as they were last known, with the
// failure's line; when they cannot be read either, the line alone.
async function load() {
  loading.value = true
  loadError.value = null
  try {
    sessions.value = await syncBoxSessions(props.boxName, props.token)
  } catch (err) {
    loadError.value = errorText(err)
    try {
      sessions.value = await fetchBoxSessions(props.boxName, props.token)
    } catch {
      sessions.value = []
    }
  } finally {
    loading.value = false
  }
}

async function sync() {
  syncing.value = true
  try {
    sessions.value = await syncBoxSessions(props.boxName, props.token)
    loadError.value = null
  } catch (err) {
    // The list keeps the sessions it had; the message says the sync failed.
    message.error(errorText(err))
  } finally {
    syncing.value = false
  }
}

async function kill(session: ApiSession, force = false) {
  try {
    await deleteSession(props.boxName, session.id, props.token, true, force)
    sessions.value = sessions.value.filter(s => s.id !== session.id)
  } catch (err) {
    message.error(errorText(err))
  }
}

// "Restore layout": the session is recreated from its last layout, then the list is
// synced, so the row shows running.
async function restore(session: ApiSession) {
  try {
    await recreateSession(session.id, props.token)
  } catch (err) {
    message.error(errorText(err))
    return
  }
  await sync()
}

// The one rename field on screen (only the edited row draws it).
let renameInput: HTMLInputElement | null = null
const setRenameInput = (el: unknown) => { renameInput = el as HTMLInputElement | null }

function startRename(session: ApiSession) {
  editingId.value = session.id
  editName.value = session.session_name
  nextTick(() => {
    renameInput?.focus()
    renameInput?.select()
  })
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
  } catch (err) {
    // The name stays in its field, so it can be corrected and sent again.
    message.error(errorText(err))
  }
}

function formatTime(ts: number): string {
  const now = Date.now() / 1000
  const diff = now - ts
  if (diff < 60) return t('sessions.just_now')
  if (diff < 3600) return t('sessions.minutes_ago', { n: String(Math.floor(diff / 60)) })
  if (diff < 86400) return t('sessions.hours_ago', { n: String(Math.floor(diff / 3600)) })
  return t('sessions.days_ago', { n: String(Math.floor(diff / 86400)) })
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

    <!-- Pinned home row — always visible -->
    <div
      class="session-item home-item"
      :class="{ active: currentSession === effectiveHome }"
      @click="goHome"
    >
      <NIcon size="13" style="flex-shrink:0"><PhHouse weight="duotone" /></NIcon>
      <span class="home-label">{{ effectiveHome }}</span>
      <span v-if="homeSessionData?.active" class="home-dot" />
    </div>

    <NSpin v-if="loading" size="small" />

    <NEmpty v-else-if="sortedSessions.length === 0" :description="loadError ?? t('sessions.empty')" size="small" />

    <div v-else class="session-list">
      <div v-if="loadError" class="load-error" data-testid="load-error">{{ loadError }}</div>
      <div
        v-for="session in sortedSessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.session_name === currentSession, inactive: !session.active }"
        :style="{ '--session-color': getColorForSession(session.session_name) }"
        @click="emit('select', session)"
      >
        <div class="session-info">
          <div class="session-name">
            <NIcon size="10" :color="session.active ? 'var(--success)' : 'var(--muted)'">
              <PhCircle weight="fill" />
            </NIcon>
            <span v-if="session.metadata?.recovered_at" class="recovered-badge" :title="t('sessions.recovered_tooltip')">↺</span>
            <template v-if="editingId === session.id">
              <input
                v-model="editName"
                :ref="setRenameInput"
                class="rename-input"
                @click.stop
                @keyup.enter.stop="confirmRename(session)"
                @keyup.escape.stop="cancelRename"
              />
            </template>
            <span v-else>{{ session.session_name }}</span>
          </div>
          <div class="session-meta">
            {{ session.working_directory.split(/[/\\]/).filter(Boolean).pop() || '~' }}
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
            <NButton
              v-if="session.restorable"
              size="tiny"
              quaternary
              type="primary"
              class="restore-layout"
              :title="t('sessions.restore_layout')"
              @click.stop="restore(session)"
            >
              {{ t('sessions.restore_layout') }}
            </NButton>
            <NButton size="tiny" quaternary @click.stop="startRename(session)" :title="t('sessions.rename')">
              <NIcon size="12"><PhPencilSimple weight="duotone" /></NIcon>
            </NButton>
            <NPopconfirm @positive-click.stop="kill(session)">
              <template #trigger>
                <NButton size="tiny" quaternary type="error" @click.stop :title="t('sessions.kill')">
                  <NIcon size="12"><PhTrash weight="duotone" /></NIcon>
                </NButton>
              </template>
              {{ t('sessions.kill_confirm', { name: session.session_name }) }}
            </NPopconfirm>
            <NPopconfirm @positive-click.stop="kill(session, true)">
              <template #trigger>
                <NButton size="tiny" quaternary type="error" @click.stop :title="t('sessions.force_kill')">
                  <NIcon size="12"><PhSkull weight="duotone" /></NIcon>
                </NButton>
              </template>
              {{ t('sessions.force_kill_confirm', { name: session.session_name }) }}
            </NPopconfirm>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.load-error {
  padding: 4px 8px;
  font-size: 12px;
  color: var(--muted);
}

.home-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  border-bottom: 1px solid var(--stroke);
  margin-bottom: 4px;
  padding-bottom: 8px;
  transition: background var(--dur-fast) var(--ease-out);
}

.home-item:hover {
  background: var(--surface-hover);
}

.home-item.active {
  color: var(--accent);
}

.home-label {
  flex: 1;
}

.home-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  flex-shrink: 0;
}

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
  padding: 6px 8px 6px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out);
  border-left: 3px solid var(--session-color, transparent);
}

.session-item:hover {
  background: var(--surface-hover);
}

.session-item.active {
  background: var(--accent-bg);
  border: 1px solid var(--accent);
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

.recovered-badge {
  font-size: 10px;
  color: var(--warning);
  margin-left: 2px;
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
