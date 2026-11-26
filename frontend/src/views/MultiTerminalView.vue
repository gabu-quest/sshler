<script setup lang="ts">
import { errorText } from '@/api/apiError'
import type { ApiSession } from '@/api/types'
import { ref, computed, onMounted, watch, TransitionGroup } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NIcon, NSpace, NModal, NPopover, NSelect, NInput, useMessage } from 'naive-ui'
import { PhArrowLeft, PhFloppyDisk, PhLayout, PhPlus, PhTerminalWindow, PhTrash } from '@phosphor-icons/vue'

import { createLayout, deleteLayout, fetchLayouts } from '@/api/http'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import { useAppStore } from '@/stores/app'
import { useI18n } from '@/i18n'
import { WINDOWS_SHELL_KEY, normalizePosixShell, useShellOptions, type PosixShell } from '@/composables/useShellOptions'
import { generateSessionName, lastPathSegment } from '@/utils/sessionName'
import { terminalThemeOf } from '@/utils/terminalThemes'
import { useDirectoryOptions, BROWSE_DIRECTORY, CUSTOM_DIRECTORY } from '@/composables/useDirectoryOptions'
import Terminal from '@/components/Terminal.vue'
import DirectoryPickerModal from '@/components/DirectoryPickerModal.vue'
import { getEmojiForBox } from '@/utils/emoji-favicon'
import { useResponsive } from '@/composables/useResponsive'

interface TerminalInstance {
  id: string
  boxName: string
  sessionName: string
  directory: string
  /** The kind this pane was added with; a fallback to plain is not recorded here. */
  shell: PosixShell
}

interface SavedLayoutTerminal {
  boxName: string
  sessionName: string
  directory: string
  /** Absent in a layout saved before the kind existed: that is tmux. */
  shell?: unknown
}

interface SavedLayout {
  id: string
  name: string
  terminals: SavedLayoutTerminal[]
  created_at: number
}

const route = useRoute()
const message = useMessage()
const bootstrapStore = useBootstrapStore()
const boxesStore = useBoxesStore()
const favoritesStore = useFavoritesStore()
const appStore = useAppStore()
const { t } = useI18n()
const { width: viewportWidth } = useResponsive()

const terminals = ref<TerminalInstance[]>([])
const savedLayouts = ref<SavedLayout[]>([])
const showAddModal = ref(false)
const showSaveLayoutModal = ref(false)
const showDirPicker = ref(false)
const showLayoutMenu = ref(false)
// "Custom path..." swaps the directory list for a field to type the path in.
const showCustomDir = ref(false)
// The list's choice before Custom path was picked; Back puts it back.
let directoryBeforeCustom = ''
const newLayoutName = ref('')
const newTerminal = ref<{ boxName: string; sessionName: string; directory: string; shell: PosixShell }>({
  boxName: '',
  sessionName: '',
  directory: '~',
  shell: 'tmux',
})

const { isWindowsLocalBox, optionsOf } = useShellOptions()
const shellOptions = optionsOf(() => newTerminal.value.boxName)
// A Windows local box opens its default shell: no choice is offered for it.
const showShellSelect = computed(() => !!newTerminal.value.boxName && !isWindowsLocalBox(newTerminal.value.boxName))

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({
    label: `${getEmojiForBox(box.name)} ${box.name} (${box.host})`,
    value: box.name
  }))
)

const tokenValue = computed(() =>
  bootstrapStore.token || bootstrapStore.payload?.token || null
)

const sortedSavedLayouts = computed(() =>
  [...savedLayouts.value].sort((a, b) => b.created_at - a.created_at)
)

const gridCols = computed(() => {
  const count = terminals.value.length
  const w = viewportWidth.value
  if (count === 0 || count === 1) return 1
  if (w < 768) return 1
  // Four panes make a 2 x 2: wider panes and taller rows than one row of four.
  if (count === 4) return 2
  if (w <= 1024) return Math.min(count, 2)
  if (w <= 1400) return Math.min(count, 3)
  return Math.min(count, 4)
})

const terminalFontSize = computed(() => {
  const count = terminals.value.length
  const w = viewportWidth.value
  const base = appStore.terminalFontSize
  // Start from the user's configured size, then shrink as panes multiply
  let size = count <= 4 ? base : count <= 8 ? base - 2 : base - 3
  // Single column on narrow screens — use the full configured size
  if (w < 768) return Math.max(base, 8)
  // Tablet: shrink a bit more with multiple panes to avoid overflow
  if (w <= 1024 && count > 1) size = Math.min(size, base - 2)
  return Math.max(size, 8)
})

// Terminal height adapts to viewport and count
const terminalMinHeight = computed(() => {
  const count = terminals.value.length
  const w = viewportWidth.value
  // A floor only: the rows share the window's height, so four panes at 1366 x 768 all fit.
  if (w < 768) return '250px'
  if (w <= 1024) return '240px'
  return count <= 1 ? '300px' : '220px'
})

// The box's saved terminal theme (calm for a box with none, or one this page no longer knows).
const themeOf = (boxName: string) =>
  terminalThemeOf(boxesStore.items.find((b) => b.name === boxName)?.terminal_theme)

// The one directory list: home, the box's favourites, Browse, custom path.
const directoryOptions = useDirectoryOptions(() => newTerminal.value.boxName)

const handleDirectoryChange = (value: string) => {
  if (value === BROWSE_DIRECTORY) {
    showDirPicker.value = true
    return
  }
  if (value === CUSTOM_DIRECTORY) {
    directoryBeforeCustom = newTerminal.value.directory
    showCustomDir.value = true
    newTerminal.value.directory = ''
    return
  }
  showCustomDir.value = false
  newTerminal.value.directory = value
}

const backToDirectoryList = () => {
  showCustomDir.value = false
  newTerminal.value.directory = directoryBeforeCustom
}

const handleDirPickerSelect = (path: string) => {
  showCustomDir.value = false
  newTerminal.value.directory = path
}

const ensureData = async () => {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap()
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null)
  }
}

const addTerminal = () => {
  if (!newTerminal.value.boxName) {
    message.error(t('multi.select_box'))
    return
  }

  // Generate session name based on directory for tmux window sharing.
  // Hash-suffixed so two different paths with the same final dir name don't
  // collapse onto the same tmux session. See utils/sessionName.ts.
  const dirBasedSession = generateSessionName(
    newTerminal.value.directory || '~',
    newTerminal.value.boxName,
  )

  const id = `term-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  terminals.value.push({
    id,
    boxName: newTerminal.value.boxName,
    sessionName: newTerminal.value.sessionName || dirBasedSession,
    directory: newTerminal.value.directory || '~',
    shell: showShellSelect.value ? newTerminal.value.shell : 'tmux',
  })
  saveLayout()

  showAddModal.value = false
  newTerminal.value = {
    boxName: newTerminal.value.boxName, // Keep same box selected
    sessionName: '', // Reset to auto-generate
    directory: '~',
    shell: 'tmux', // each new terminal starts as tmux
  }
  showCustomDir.value = false
}

// A pick in a pane's session list (its title bar) puts that pane on the session, as the
// single view does; the pane remounts on it (its key holds the session name). A plain shell
// has no sessions: the pick replaces it with tmux.
const handleSessionSelect = (id: string, session: ApiSession) => {
  const target = terminals.value.find((item) => item.id === id)
  if (!target) return
  target.sessionName = session.session_name
  target.directory = session.working_directory || target.directory
  target.shell = 'tmux'
  saveLayout()
}

// A Windows local box opens a native shell, as the single view's tabs do: the stored shell
// id (empty is the default) and native-shell. Any other box passes "plain" or nothing (tmux).
const shellOf = (terminal: TerminalInstance): string =>
  isWindowsLocalBox(terminal.boxName)
    ? localStorage.getItem(WINDOWS_SHELL_KEY) || ''
    : terminal.shell === 'plain' ? 'plain' : ''
const nativeShellOf = (terminal: TerminalInstance): boolean | undefined =>
  isWindowsLocalBox(terminal.boxName) ? true : undefined

// A key in the custom path field: Enter adds the pane, unless it confirms an IME conversion.
const onCustomDirKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return
  addTerminal()
}

const removeTerminal = (id: string) => {
  terminals.value = terminals.value.filter(t => t.id !== id)
  saveLayout()
}

const openAddModal = () => {
  const firstBox = boxOptions.value[0]
  if (firstBox && !newTerminal.value.boxName) {
    newTerminal.value.boxName = firstBox.value
  }
  // Load favorites for selected box
  if (newTerminal.value.boxName) {
    favoritesStore.loadBox(newTerminal.value.boxName, tokenValue.value || null)
  }
  newTerminal.value.shell = 'tmux'
  showCustomDir.value = false
  showAddModal.value = true
}

// Watch for box selection changes to load favorites; the folder belonged to the old box,
// so it goes back to home and the custom field closes.
watch(() => newTerminal.value.boxName, async (boxName) => {
  newTerminal.value.directory = '~'
  showCustomDir.value = false
  if (boxName) {
    await favoritesStore.loadBox(boxName, tokenValue.value || null)
  }
})

const goBack = () => {
  window.history.back()
}

// Layout persistence
const LAYOUT_KEY = 'sshler:multi-terminal:layout'

async function loadSavedLayouts() {
  try {
    savedLayouts.value = await fetchLayouts(tokenValue.value || null)
  } catch (error) {
    // The local layout still works; the message says the saved ones could not be listed.
    message.error(errorText(error))
  }
}

function saveLayout() {
  if (terminals.value.length === 0) {
    localStorage.removeItem(LAYOUT_KEY)
    return
  }
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(terminals.value.map(t => ({
    boxName: t.boxName,
    sessionName: t.sessionName,
    directory: t.directory,
    shell: t.shell,
  }))))
}

function loadLayout(): TerminalInstance[] | null {
  const raw = localStorage.getItem(LAYOUT_KEY)
  if (!raw) return null
  try {
    const items = JSON.parse(raw)
    if (!Array.isArray(items) || items.length === 0) return null
    return items.map((item: any) => ({
      id: `term-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      boxName: item.boxName,
      sessionName: item.sessionName,
      directory: item.directory,
      shell: normalizePosixShell(item.shell),
    }))
  } catch { return null }
}

async function saveCurrentLayout() {
  const name = newLayoutName.value.trim()
  if (!name || terminals.value.length === 0) return

  try {
    await createLayout(
      name,
      terminals.value.map((terminal) => ({
        boxName: terminal.boxName,
        sessionName: terminal.sessionName,
        directory: terminal.directory,
        shell: terminal.shell,
      })),
      tokenValue.value || null
    )
    newLayoutName.value = ''
    showSaveLayoutModal.value = false
    await loadSavedLayouts()
  } catch (error) {
    message.error(errorText(error))
  }
}

async function applyLayout(layout: { terminals: SavedLayoutTerminal[] }) {
  terminals.value = layout.terminals.map((item) => ({
    id: `term-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    boxName: item.boxName,
    sessionName: item.sessionName,
    directory: item.directory,
    shell: normalizePosixShell(item.shell),
  }))
  saveLayout()
  showLayoutMenu.value = false
}

async function removeSavedLayout(id: string) {
  try {
    await deleteLayout(id, tokenValue.value || null)
    await loadSavedLayouts()
  } catch (error) {
    message.error(errorText(error))
  }
}

onMounted(async () => {
  await ensureData()
  await loadSavedLayouts()

  // Restore saved layout
  const saved = loadLayout()
  if (saved && saved.length > 0) {
    terminals.value = saved
  }

  // Initialize from route
  const boxFromRoute = route.query.box as string
  if (boxFromRoute && boxOptions.value.some(opt => opt.value === boxFromRoute)) {
    newTerminal.value.boxName = boxFromRoute
    appStore.activeBox = boxFromRoute
  }
})
</script>

<template>
  <div class="multi-terminal-page">
    <!-- One toolbar row: title, count, Add, Save, Load -->
    <div class="header">
      <NButton size="small" quaternary :title="t('terminal.go_back')" :aria-label="t('terminal.go_back')" @click="goBack">
        <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
      </NButton>
      <h1>{{ t('multi.title') }}</h1>
      <span class="count-pill text-muted">{{ terminals.length }} {{ terminals.length !== 1 ? t('multi.terminals') : t('multi.terminal') }} {{ t('multi.active') }}</span>

      <div class="header-actions">
        <NButton type="primary" size="small" @click="openAddModal">
          <template #icon><NIcon size="16"><PhPlus weight="duotone" /></NIcon></template>
          {{ t('multi.add_terminal') }}
        </NButton>

        <NButton
          size="small"
          quaternary
          :disabled="terminals.length === 0"
          :title="t('multi_terminal.save_layout')"
          :aria-label="t('multi_terminal.save_layout')"
          @click="showSaveLayoutModal = true"
        >
          <NIcon size="16"><PhFloppyDisk weight="duotone" /></NIcon>
          <span class="btn-label">{{ t('multi_terminal.save_layout') }}</span>
        </NButton>

        <NPopover
          trigger="click"
          placement="bottom-end"
          v-model:show="showLayoutMenu"
        >
          <template #trigger>
            <NButton size="small" quaternary :title="t('multi_terminal.load_layout')" :aria-label="t('multi_terminal.load_layout')">
              <NIcon size="16"><PhLayout weight="duotone" /></NIcon>
              <span class="btn-label">{{ t('multi_terminal.load_layout') }}</span>
            </NButton>
          </template>

          <div class="layout-menu">
            <div v-if="sortedSavedLayouts.length === 0" class="layout-empty">
              {{ t('multi_terminal.no_saved_layouts') }}
            </div>
            <div v-else class="layout-list">
              <div
                v-for="layout in sortedSavedLayouts"
                :key="layout.id"
                class="layout-row"
                @click="applyLayout(layout)"
              >
                <span class="layout-name">{{ layout.name }}</span>
                <NButton
                  size="tiny"
                  quaternary
                  :title="t('common.delete')"
                  @click.stop="removeSavedLayout(layout.id)"
                >
                  <NIcon size="14"><PhTrash weight="duotone" /></NIcon>
                </NButton>
              </div>
            </div>
          </div>
        </NPopover>
      </div>
    </div>

    <!-- Terminal Grid: each pane is a Terminal with its own title bar (closable) -->
    <TransitionGroup
      v-if="terminals.length > 0"
      name="list-row"
      tag="div"
      class="terminal-grid"
      :style="{
        gridTemplateColumns: `repeat(${gridCols}, minmax(0, 1fr))`,
        '--terminal-min-height': terminalMinHeight
      }"
    >
      <div
        v-for="(terminal, index) in terminals"
        :key="terminal.id"
        class="multi-pane"
        :style="{ '--row-index': index }"
      >
        <Terminal
          :key="terminal.sessionName"
          :box-name="terminal.boxName"
          :session-name="terminal.sessionName"
          :directory="terminal.directory"
          :shell="shellOf(terminal)"
          :native-shell="nativeShellOf(terminal)"
          :theme="themeOf(terminal.boxName)"
          :font-size="terminalFontSize"
          closable
          @close="removeTerminal(terminal.id)"
          @session-select="(session) => handleSessionSelect(terminal.id, session)"
        >
          <template #titlebar-meta>
            <span class="pane-meta" :title="terminal.directory">
              {{ getEmojiForBox(terminal.boxName) }}
              <span class="directory-name">{{ lastPathSegment(terminal.directory) || terminal.directory }}</span>
            </span>
          </template>
        </Terminal>
      </div>
    </TransitionGroup>

    <div v-else class="terminal-grid">
      <div class="empty-state">
        <NIcon size="48" class="empty-icon"><PhTerminalWindow weight="duotone" /></NIcon>
        <h3>{{ t('multi.no_terminals') }}</h3>
        <p class="text-muted">{{ t('multi.no_terminals_hint') }}</p>
      </div>
    </div>

    <!-- Add Terminal Modal -->
    <NModal v-model:show="showAddModal" preset="card" :title="t('multi.add_terminal')" style="max-width: 400px">
      <NSpace vertical size="medium">
        <div>
          <label class="form-label">{{ t('multi.box') }}</label>
          <NSelect
            v-model:value="newTerminal.boxName"
            :options="boxOptions"
            :placeholder="t('multi.choose_box')"
          />
        </div>

        <div v-if="showShellSelect">
          <label class="form-label">{{ t('terminal.shell_label') }}</label>
          <NSelect
            class="shell-select"
            v-model:value="newTerminal.shell"
            :options="shellOptions"
            :placeholder="t('terminal.shell_label')"
          />
        </div>

        <div>
          <label class="form-label">{{ t('multi.session_name') }}</label>
          <NInput
            v-model:value="newTerminal.sessionName"
            :placeholder="t('multi.session_placeholder')"
          />
          <p class="form-help">{{ t('multi.session_help') }}</p>
        </div>

        <div>
          <label class="form-label">{{ t('multi.directory') }}</label>
          <NSelect
            v-if="!showCustomDir"
            :value="newTerminal.directory"
            :options="directoryOptions"
            :placeholder="t('multi.dir_placeholder')"
            @update:value="handleDirectoryChange"
          />
          <div v-else class="custom-dir-row">
            <NInput
              v-model:value="newTerminal.directory"
              :input-props="{ class: 'custom-dir' }"
              :placeholder="t('terminal.dir_placeholder')"
              @keydown="onCustomDirKeydown"
              @keydown.esc.stop="backToDirectoryList"
            />
            <NButton @click="backToDirectoryList">{{ t('common.back') }}</NButton>
          </div>
          <p class="form-help">{{ t('multi.dir_help') }}</p>
        </div>
      </NSpace>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showAddModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" @click="addTerminal">{{ t('multi.add_terminal') }}</NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal
      v-model:show="showSaveLayoutModal"
      preset="card"
      :title="t('multi_terminal.save_layout')"
      style="max-width: 400px"
    >
      <NSpace vertical size="medium">
        <div>
          <label class="form-label">{{ t('multi_terminal.layout_name') }}</label>
          <NInput
            v-model:value="newLayoutName"
            :placeholder="t('multi_terminal.layout_name')"
            @keyup.enter="saveCurrentLayout"
          />
        </div>
      </NSpace>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showSaveLayoutModal = false">{{ t('common.cancel') }}</NButton>
          <NButton
            type="primary"
            :disabled="!newLayoutName.trim() || terminals.length === 0"
            @click="saveCurrentLayout"
          >
            {{ t('common.save') }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Directory Picker Modal -->
    <DirectoryPickerModal
      v-if="newTerminal.boxName"
      v-model:show="showDirPicker"
      :box-name="newTerminal.boxName"
      :initial-path="newTerminal.directory"
      :token="tokenValue"
      @select="handleDirPickerSelect"
    />
  </div>
</template>

<style scoped>
.custom-dir-row {
  display: flex;
  gap: 8px;
}

.multi-terminal-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0;
}

/* One row, at most 36px: back, title, count, then Add / Save / Load. */
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 8px;
  border-bottom: 1px solid var(--stroke);
  flex-shrink: 0;
  min-width: 0;
}

.header h1 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.count-pill {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-variant);
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
}

.btn-label {
  margin-left: 4px;
}

.terminal-grid {
  flex: 1;
  display: grid;
  gap: 4px;
  padding: 4px;
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  grid-auto-rows: minmax(var(--terminal-min-height, 220px), 1fr);
}

/* A neutral 1px frame and a 3px accent on top, from the tokens. */
.multi-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid var(--stroke);
  border-top: 3px solid var(--accent);
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface);
  min-height: var(--terminal-min-height, 220px);
  container-type: inline-size;
}

/* A narrow pane keeps its title bar to one line: the title shortens before it wraps. */
.multi-pane :deep(.titlebar-center) {
  min-width: 0;
  overflow: hidden;
  flex-wrap: nowrap;
}

.multi-pane :deep(.titlebar-text) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.multi-pane > :deep(*) {
  flex: 1;
  min-height: 0;
}

.pane-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.directory-name {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--muted);
}

.empty-icon {
  margin-bottom: 16px;
  opacity: 0.5;
}

.empty-state h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
}

.empty-state p {
  margin: 0;
}

.layout-menu {
  min-width: 260px;
}

.layout-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.layout-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.layout-row:hover {
  background: var(--surface-variant);
}

.layout-name {
  font-size: 13px;
  color: var(--text);
}

.layout-empty {
  color: var(--muted);
  font-size: 13px;
  padding: 4px;
}

.form-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 4px;
  color: var(--text);
}

.form-help {
  margin: 4px 0 0 0;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.3;
}

.text-muted {
  color: var(--muted);
}

/* Grid columns, font and floor heights are driven by JS (useResponsive). On the phone the
   toolbar stays one row: Save and Load keep their icons, their words go. */
@media (max-width: 767px) {
  .btn-label {
    display: none;
  }

  .count-pill {
    display: none;
  }

  .directory-name {
    display: none;
  }
}
</style>
