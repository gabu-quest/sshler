<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSelect, NButton, NIcon, NInput, NPopover, useMessage } from 'naive-ui'
import {
  PhTerminalWindow, PhArrowLeft, PhStar, PhFolderOpen,
  PhCaretDown, PhList, PhBookmarkSimple, PhArrowsLeftRight,
  PhClipboard,
} from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import { useAppStore } from '@/stores/app'
import { useResponsive } from '@/composables/useResponsive'
import { useI18n } from '@/i18n'
import { generateSessionName, getColorForSession, lastPathSegment } from '@/utils/sessionName'
import Terminal from '@/components/Terminal.vue'
import TerminalTabs from '@/components/TerminalTabs.vue'
import MobileInputBar from '@/components/MobileInputBar.vue'
import SessionSwitcher from '@/components/SessionSwitcher.vue'
import DirectoryPickerModal from '@/components/DirectoryPickerModal.vue'
import SnippetsPanel from '@/components/SnippetsPanel.vue'
import TunnelsPanel from '@/components/TunnelsPanel.vue'
import GitBadge from '@/components/GitBadge.vue'
import { setEmojiFavicon, resetFavicon, getEmojiForBox } from '@/utils/emoji-favicon'
import { gitInfo, setBoxTerminalTheme } from '@/api/http'
import type { GitInfo } from '@/api/types'

const route = useRoute()
const router = useRouter()

const bootstrapStore = useBootstrapStore()
const boxesStore = useBoxesStore()
const favoritesStore = useFavoritesStore()
const appStore = useAppStore()
const { t } = useI18n()
const message = useMessage()

const selectedBox = ref<string | null>(null)
const initialDirectory = ref<string>('~')
const sessionName = ref<string>('main')
const showManualDir = ref(false)
const showDirPicker = ref(false)
const terminalRef = ref<InstanceType<typeof Terminal> | null>(null)
const tabsRef = ref<InstanceType<typeof TerminalTabs> | null>(null)
const currentGitInfo = ref<GitInfo | null>(null)
const { isMobile } = useResponsive()
const mobileControlsExpanded = ref(false)
const rawMode = ref(false)
const terminalConnected = ref(false)
const showSessionPanel = ref(false)
const showSnippetsPanel = ref(false)
const showTunnelsPanel = ref(false)

// Periodic git info refresh — only when tab is visible
let gitPollTimer: ReturnType<typeof setInterval> | null = null

const startGitPolling = () => {
  if (gitPollTimer || !terminalConnected.value) return
  loadGitInfo()
  gitPollTimer = setInterval(loadGitInfo, 15_000)
}

const stopGitPolling = () => {
  if (gitPollTimer) {
    clearInterval(gitPollTimer)
    gitPollTimer = null
  }
}

const handleGitVisibility = () => {
  if (document.hidden) stopGitPolling()
  else startGitPolling()
}

// Track terminal connection state
const onTerminalConnected = () => {
  terminalConnected.value = true
  if (!document.hidden) startGitPolling()
}
const onTerminalDisconnected = () => {
  terminalConnected.value = false
  stopGitPolling()
}

// MobileInputBar handlers
const handleSmartSend = (data: string) => {
  activeTerminalApi()?.send(data)
}

const handleRawSend = (data: string) => {
  activeTerminalApi()?.sendRaw(data)
}

const handleToggleRawMode = () => {
  rawMode.value = !rawMode.value
  if (rawMode.value) {
    // Switching to raw: re-enable xterm stdin and focus it
    nextTick(() => activeTerminalApi()?.focus())
  }
}

// Toggle mobile controls dropdown
const toggleMobileControls = () => {
  mobileControlsExpanded.value = !mobileControlsExpanded.value
}

// Collapse mobile controls when terminal gets focus
const onTerminalFocus = () => {
  if (isMobile.value) {
    mobileControlsExpanded.value = false
  }
}

// Load git info for current directory
const loadGitInfo = async () => {
  if (!selectedBox.value || !initialDirectory.value) {
    currentGitInfo.value = null
    return
  }
  try {
    currentGitInfo.value = await gitInfo(selectedBox.value, initialDirectory.value, tokenValue.value)
  } catch {
    currentGitInfo.value = null
  }
}

const handleSessionSelect = (session: import('@/api/types').ApiSession) => {
  const dir = session.working_directory || '~'
  if (tabbedMode.value) {
    // Open (or re-activate) the session as a tab; header syncs via child events.
    tabsRef.value?.openSession(session)
    showSessionPanel.value = false
    return
  }
  if (terminalRef.value) {
    // Reconnect in-place: keeps the DOM element alive so fullscreen is preserved
    terminalRef.value.switchSession(session.session_name, dir)
    sessionName.value = session.session_name
  } else {
    sessionName.value = session.session_name
    initialDirectory.value = dir
  }
  showSessionPanel.value = false
}

const handleSnippetInsert = (command: string, execute: boolean) => {
  if (terminalRef.value) {
    terminalRef.value.send(execute ? command + '\n' : command)
  }
}

const copyDirToClipboard = async () => {
  if (!initialDirectory.value) return
  await navigator.clipboard.writeText(initialDirectory.value)
  message.success(t('terminal.copied'))
}

const filesUrl = computed(() => {
  if (!selectedBox.value) return '#'
  let path = initialDirectory.value
  // Resolve ~ to the box's default_dir — the backend can't expand tilde
  if (!path || path === '~') {
    const box = boxesStore.items.find(b => b.name === selectedBox.value)
    path = box?.default_dir || `/home/${box?.user || 'root'}`
  }
  return `/app/files?box=${encodeURIComponent(selectedBox.value)}&path=${encodeURIComponent(path)}`
})

const goToFiles = () => {
  if (selectedBox.value) {
    // Open in new tab at the terminal's current directory
    window.open(filesUrl.value, '_blank')
  }
}

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({
    label: `${getEmojiForBox(box.name)} ${box.name} (${box.host})`,
    value: box.name
  }))
)

const tokenValue = computed(() =>
  bootstrapStore.token || bootstrapStore.payload?.token || null
)

// --- Windows native-shell chooser ---------------------------------------
const SHELL_STORAGE_KEY = 'sshler.windows_shell'

const isLocalBox = computed(() => {
  const box = boxesStore.items.find(b => b.name === selectedBox.value)
  return box?.transport === 'local'
})

// Only Windows local boxes get the chooser (remote boxes use SSH+tmux).
const showShellPicker = computed(() => bootstrapStore.isWindows && isLocalBox.value)

// Tabs are a Windows-local-box feature. Remote/tmux boxes already multiplex via
// tmux windows (Ctrl+B), but a native ConPTY shell has no multiplexer — so we
// give those boxes browser-side tabs instead. Same gate as the shell picker.
const tabbedMode = computed(() => bootstrapStore.isWindows && isLocalBox.value)

// The currently-active terminal, whichever rendering path is live. Both the
// single <Terminal> and the <TerminalTabs> host expose send/sendRaw/focus.
type TerminalApi = { send: (d: string) => void; sendRaw: (d: string) => void; focus: () => void }
const activeTerminalApi = (): TerminalApi | null =>
  (tabbedMode.value ? tabsRef.value : terminalRef.value) as unknown as TerminalApi | null

// Keep the header (title, git, favicon, favorites) in sync with the active tab.
const onActiveTabDirectory = (dir: string) => { initialDirectory.value = dir }
const onActiveTabSession = (s: string) => { sessionName.value = s }

// Feedback for the "kill terminal for good" action (right-click a tab → Kill).
const onTabKilled = (session: string) =>
  message.success(t('terminal.tabs.killed', { session }))
const onTabKillError = (session: string) =>
  message.error(t('terminal.tabs.kill_failed', { session }))

const shellOptions = computed(() =>
  bootstrapStore.windowsShells.map((shell) => ({
    label: shell.available ? shell.label : `${shell.label} (not installed)`,
    value: shell.id,
  }))
)

const selectedShell = ref<string>(
  (typeof localStorage !== 'undefined' && localStorage.getItem(SHELL_STORAGE_KEY)) || ''
)

// The shell only matters for Windows local boxes; elsewhere it's the empty
// string so Terminal omits the query param and the SSH/tmux path is used.
const activeShell = computed(() => (showShellPicker.value ? selectedShell.value : ''))

function handleShellChange(value: string) {
  selectedShell.value = value
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(SHELL_STORAGE_KEY, value)
  }
}

const isCurrentDirFavorite = computed(() =>
  selectedBox.value ? favoritesStore.isFavorite(selectedBox.value, initialDirectory.value) : false
)

const VALID_THEMES = ['cyberpunk', 'default', 'solarized', 'dracula', 'nord', 'monokai', 'light'] as const
type TerminalTheme = typeof VALID_THEMES[number]

const currentBoxTheme = computed<TerminalTheme>(() => {
  if (!selectedBox.value) return 'cyberpunk'
  const box = boxesStore.items.find(b => b.name === selectedBox.value)
  const theme = box?.terminal_theme
  if (theme && VALID_THEMES.includes(theme as TerminalTheme)) return theme as TerminalTheme
  return 'cyberpunk'
})

const themeOptions = VALID_THEMES.map(t => ({ label: t.charAt(0).toUpperCase() + t.slice(1), value: t }))

const handleThemeChange = async (theme: string) => {
  if (!selectedBox.value) return
  try {
    await setBoxTerminalTheme(selectedBox.value, theme, tokenValue.value)
    // Update local box data
    const box = boxesStore.items.find(b => b.name === selectedBox.value)
    if (box) box.terminal_theme = theme
  } catch {
    // ignore
  }
}

const toggleCurrentDirFavorite = async () => {
  if (selectedBox.value) {
    await favoritesStore.toggle(selectedBox.value, initialDirectory.value, tokenValue.value)
  }
}

const directoryOptions = computed(() => {
  const options = [
    { label: '~ (Home)', value: '~' }
  ]

  if (selectedBox.value) {
    const favorites = Array.from(favoritesStore.favoritesForBox(selectedBox.value).values())
    favorites.forEach(fav => {
      const label = lastPathSegment(fav) || fav
      options.push({ label: `★ ${label}`, value: fav })
    })
  }

  options.push({ label: '📁 Browse...', value: '__browse__' })
  options.push({ label: '+ Custom path...', value: '__custom__' })
  return options
})

const handleDirectoryChange = (value: string) => {
  if (value === '__browse__') {
    showDirPicker.value = true
    return
  }
  if (value === '__custom__') {
    showManualDir.value = true
    return
  }
  showManualDir.value = false
  initialDirectory.value = value
  sessionName.value = generateSessionName(value, selectedBox.value ?? undefined)
}

const handleDirPickerSelect = (path: string) => {
  initialDirectory.value = path
  sessionName.value = generateSessionName(path, selectedBox.value ?? undefined)
}

// Display name for the directory (last component)
const displayDirName = computed(() => {
  if (!initialDirectory.value || initialDirectory.value === '~') {
    return 'Home'
  }
  return lastPathSegment(initialDirectory.value) || 'Root'
})

// Update browser tab title, favicon, and git info
watch([selectedBox, initialDirectory], () => {
  appStore.activeBox = selectedBox.value
  appStore.activeDir = initialDirectory.value || null
  if (selectedBox.value) {
    document.title = `${displayDirName.value} — ${selectedBox.value}`
    // Set deterministic emoji favicon based on box + directory
    setEmojiFavicon(`${selectedBox.value}:${initialDirectory.value}`)
    // Load git info for this directory
    loadGitInfo()
  } else {
    document.title = 'Terminal'
    resetFavicon()
    currentGitInfo.value = null
  }
}, { immediate: true })

const ensureData = async () => {
  if (!bootstrapStore.payload && !bootstrapStore.loading) {
    await bootstrapStore.bootstrap()
  }
  if (!boxesStore.items.length && !boxesStore.loading) {
    await boxesStore.load(tokenValue.value || null)
  }
}

const initializeFromRoute = () => {
  const boxFromRoute = route.query.box as string
  const dirFromRoute = route.query.dir as string
  
  if (boxFromRoute && boxesStore.items.some(box => box.name === boxFromRoute)) {
    selectedBox.value = boxFromRoute
  } else if (!selectedBox.value && boxesStore.items.length > 0) {
    selectedBox.value = boxesStore.items[0]?.name || null
  }
  
  if (dirFromRoute) {
    initialDirectory.value = dirFromRoute
  }

  // An explicit ?session= (e.g. a resumed Claude session) attaches to that exact
  // tmux session; otherwise derive a per-directory name.
  const sessionFromRoute = route.query.session as string | undefined
  sessionName.value = sessionFromRoute
    ? sessionFromRoute
    : generateSessionName(initialDirectory.value, selectedBox.value ?? undefined)
}

const handleBoxChange = async (boxName: string) => {
  selectedBox.value = boxName

  // Load favorites for this box
  await favoritesStore.loadBox(boxName, tokenValue.value || null)

  const newQuery = { ...route.query, box: boxName }
  window.history.replaceState(
    null,
    '',
    `${route.path}?${new URLSearchParams(newQuery).toString()}`
  )
}

const goBack = () => {
  window.history.back()
}

// Pick a sane default shell once bootstrap data is available: keep a stored
// choice if it's still a known shell id, otherwise fall back to the server default.
const initializeShell = () => {
  const known = bootstrapStore.windowsShells.map((s) => s.id)
  if (!selectedShell.value || !known.includes(selectedShell.value)) {
    selectedShell.value = bootstrapStore.defaultShell || known[0] || ''
  }
}

onMounted(async () => {
  await ensureData()
  initializeFromRoute()
  initializeShell()
  document.addEventListener('visibilitychange', handleGitVisibility)
})

onUnmounted(() => {
  stopGitPolling()
  document.removeEventListener('visibilitychange', handleGitVisibility)
})

watch(() => boxesStore.items, () => {
  if (boxesStore.items.length > 0 && !selectedBox.value) {
    initializeFromRoute()
  }
}, { immediate: true })
</script>

<template>
  <div class="page" :class="{ 'mobile': isMobile }">
    <!-- Desktop Header -->
    <header v-if="!isMobile" class="page-header">
      <div class="header-content">
        <div class="header-left">
          <NButton size="small" quaternary @click="goBack" :title="t('terminal.go_back')">
            <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
          </NButton>
          <h1 class="dir-title">{{ displayDirName }}</h1>
          <NButton v-if="selectedBox" size="tiny" quaternary @click="copyDirToClipboard" :title="t('files.copy_path')" class="copy-dir-btn">
            <NIcon size="14"><PhClipboard weight="duotone" /></NIcon>
          </NButton>
          <span class="box-badge">{{ selectedBox ? getEmojiForBox(selectedBox) + ' ' + selectedBox : t('terminal.no_box') }}</span>
          <GitBadge v-if="currentGitInfo?.is_repo" :info="currentGitInfo" />
        </div>

        <div class="header-controls">
          <NSelect
            v-model:value="selectedBox"
            :options="boxOptions"
            :placeholder="t('terminal.box')"
            :disabled="boxesStore.loading"
            @update:value="handleBoxChange"
            size="small"
            style="min-width: 140px"
          />
          <NSelect
            v-if="selectedBox && !showManualDir"
            :value="initialDirectory"
            :options="directoryOptions"
            :placeholder="t('terminal.dir')"
            @update:value="handleDirectoryChange"
            size="small"
            style="min-width: 140px"
          />
          <NSelect
            v-if="showShellPicker"
            :value="selectedShell"
            :options="shellOptions"
            :placeholder="t('terminal.shell_label')"
            @update:value="handleShellChange"
            size="small"
            style="min-width: 150px"
          />
          <NInput
            v-if="showManualDir"
            v-model:value="initialDirectory"
            :placeholder="t('terminal.dir_placeholder')"
            size="small"
            @blur="sessionName = generateSessionName(initialDirectory, selectedBox ?? undefined)"
            @keyup.enter="sessionName = generateSessionName(initialDirectory, selectedBox ?? undefined)"
            style="min-width: 160px"
          />
          <NButton v-if="showManualDir" size="small" @click="showManualDir = false">
            ✕
          </NButton>

          <NButton
            v-if="selectedBox"
            size="small"
            :type="isCurrentDirFavorite ? 'warning' : 'default'"
            @click="toggleCurrentDirFavorite"
            :title="isCurrentDirFavorite ? t('terminal.remove_favorite') : t('terminal.add_favorite')"
            class="favorite-btn"
          >
            <NIcon size="14" :color="isCurrentDirFavorite ? '#faad14' : undefined">
              <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'duotone'" />
            </NIcon>
          </NButton>

          <NSelect
            v-if="selectedBox"
            :value="currentBoxTheme"
            :options="themeOptions"
            @update:value="handleThemeChange"
            size="small"
            style="min-width: 110px"
          />

          <NPopover v-if="selectedBox" trigger="click" placement="bottom-end" v-model:show="showSessionPanel">
            <template #trigger>
              <NButton size="small" quaternary :title="t('terminal.sessions') || 'Sessions'">
                <NIcon size="14"><PhList weight="duotone" /></NIcon>
              </NButton>
            </template>
            <SessionSwitcher
              :box-name="selectedBox"
              :token="tokenValue"
              :current-session="sessionName"
              @select="handleSessionSelect"
            />
          </NPopover>

          <NButton v-if="selectedBox" size="small" quaternary :title="t('snippets.title')" @click="showSnippetsPanel = true">
            <NIcon size="14"><PhBookmarkSimple weight="duotone" /></NIcon>
          </NButton>

          <NButton v-if="selectedBox" size="small" quaternary :title="t('tunnels.title')" @click="showTunnelsPanel = true">
            <NIcon size="14"><PhArrowsLeftRight weight="duotone" /></NIcon>
          </NButton>

          <a
            v-if="selectedBox"
            :href="filesUrl"
            class="header-link-btn"
            :title="t('terminal.browse_files')"
            @click.prevent="goToFiles"
          >
            <NIcon size="14"><PhFolderOpen weight="duotone" /></NIcon>
          </a>
        </div>
      </div>
    </header>

    <!-- Mobile Header: ultra-compact single row -->
    <header v-if="isMobile" class="mobile-header">
      <button class="mobile-back-btn" @click="goBack" :title="t('common.back')">
        <NIcon size="14"><PhArrowLeft weight="duotone" /></NIcon>
      </button>
      <button class="mobile-title-btn" @click="toggleMobileControls">
        <span class="mobile-title-text">{{ displayDirName }}</span>
        <span class="mobile-box-text">{{ selectedBox ? getEmojiForBox(selectedBox) + ' ' + selectedBox : '' }}</span>
        <NIcon size="10" class="mobile-caret" :class="{ expanded: mobileControlsExpanded }"><PhCaretDown weight="duotone" /></NIcon>
      </button>
      <div class="mobile-header-actions">
        <button v-if="selectedBox" class="mobile-action-btn" @click="copyDirToClipboard" aria-label="Copy path">
          <NIcon size="14"><PhClipboard weight="duotone" /></NIcon>
        </button>
        <button
          v-if="selectedBox"
          class="mobile-action-btn"
          :class="{ active: isCurrentDirFavorite }"
          @click="toggleCurrentDirFavorite"
        >
          <NIcon size="14" :color="isCurrentDirFavorite ? '#faad14' : undefined">
            <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'duotone'" />
          </NIcon>
        </button>
        <button v-if="selectedBox" class="mobile-action-btn" @click="showSnippetsPanel = true" aria-label="Snippets">
          <NIcon size="14"><PhBookmarkSimple weight="duotone" /></NIcon>
        </button>
        <button v-if="selectedBox" class="mobile-action-btn" @click="showTunnelsPanel = true" aria-label="Port Forwarding">
          <NIcon size="14"><PhArrowsLeftRight weight="duotone" /></NIcon>
        </button>
        <a
          v-if="selectedBox"
          :href="filesUrl"
          class="mobile-action-btn"
          @click.prevent="goToFiles"
        >
          <NIcon size="14"><PhFolderOpen /></NIcon>
        </a>
      </div>
    </header>

    <!-- Mobile Controls Dropdown -->
    <div v-if="isMobile && mobileControlsExpanded" class="mobile-controls-dropdown">
      <NSelect
        v-model:value="selectedBox"
        :options="boxOptions"
        :placeholder="t('terminal.box')"
        :disabled="boxesStore.loading"
        @update:value="handleBoxChange"
        size="small"
      />
      <NSelect
        v-if="selectedBox && !showManualDir"
        :value="initialDirectory"
        :options="directoryOptions"
        :placeholder="t('terminal.dir')"
        @update:value="handleDirectoryChange"
        size="small"
      />
      <NInput
        v-if="showManualDir"
        v-model:value="initialDirectory"
        :placeholder="t('terminal.dir_placeholder')"
        size="small"
        @blur="sessionName = generateSessionName(initialDirectory, selectedBox ?? undefined)"
        @keyup.enter="sessionName = generateSessionName(initialDirectory, selectedBox ?? undefined)"
      />
      <GitBadge v-if="currentGitInfo?.is_repo" :info="currentGitInfo" compact />
    </div>

    <!-- Terminal Container -->
    <div class="terminal-container" @focusin="onTerminalFocus">
      <!-- Windows-local boxes: tabbed native shells (no tmux multiplexer) -->
      <TerminalTabs
        v-if="selectedBox && tabbedMode"
        ref="tabsRef"
        :key="selectedBox"
        :box-name="selectedBox"
        :theme="currentBoxTheme"
        :font-size="appStore.terminalFontSize"
        :font-family="appStore.terminalFontFamily"
        :show-title-bar="!isMobile"
        :external-input="isMobile && !rawMode"
        :directory="initialDirectory"
        :seed-shell="activeShell"
        :token="tokenValue"
        @connected="onTerminalConnected"
        @disconnected="onTerminalDisconnected"
        @update:active-directory="onActiveTabDirectory"
        @update:active-session="onActiveTabSession"
        @killed="onTabKilled"
        @kill-error="onTabKillError"
      />

      <!-- Remote / local-tmux boxes: single terminal (tmux handles windows) -->
      <Terminal
        v-else-if="selectedBox"
        ref="terminalRef"
        :key="selectedBox + '-' + initialDirectory + '-' + activeShell"
        :box-name="selectedBox"
        :session-name="sessionName"
        :directory="initialDirectory"
        :shell="activeShell"
        :theme="currentBoxTheme"
        :font-size="appStore.terminalFontSize"
        :font-family="appStore.terminalFontFamily"
        :show-title-bar="!isMobile"
        :external-input="isMobile && !rawMode"
        @connected="onTerminalConnected"
        @disconnected="onTerminalDisconnected"
        @session-select="handleSessionSelect"
      />

      <div v-else class="no-box-selected">
        <div class="empty-state">
          <NIcon size="48" class="empty-icon"><PhTerminalWindow weight="duotone" /></NIcon>
          <h3>{{ t('terminal.no_box') }}</h3>
          <p class="text-muted">{{ t('terminal.no_box_hint') }}</p>
          <NButton
            v-if="boxOptions.length > 0"
            type="primary"
            @click="selectedBox = boxOptions[0]?.value || null"
          >
            Connect to {{ boxOptions[0]?.label }}
          </NButton>
        </div>
      </div>
    </div>

    <!-- Mobile Smart Input Bar -->
    <MobileInputBar
      v-if="isMobile && selectedBox"
      :raw-mode="rawMode"
      :connected="terminalConnected"
      @send="handleSmartSend"
      @send-raw="handleRawSend"
      @toggle-raw-mode="handleToggleRawMode"
    />

    <!-- Directory Picker Modal -->
    <DirectoryPickerModal
      v-if="selectedBox"
      v-model:show="showDirPicker"
      :box-name="selectedBox"
      :initial-path="initialDirectory"
      :token="tokenValue"
      @select="handleDirPickerSelect"
    />

    <!-- Snippets Panel -->
    <SnippetsPanel
      v-if="selectedBox"
      v-model:show="showSnippetsPanel"
      :box-name="selectedBox"
      @insert="handleSnippetInsert"
    />

    <!-- Tunnels Panel -->
    <TunnelsPanel
      v-if="selectedBox"
      v-model:show="showTunnelsPanel"
      :box-name="selectedBox"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(var(--vh-full, 100vh) - 96px);
  overflow: hidden;
  width: 100%;
  max-width: none;
  min-width: 0;
  padding: 0 16px;
  box-sizing: border-box;
}

.page.mobile {
  gap: 0;
  height: 100%;
  padding: 0;
}

.page-header {
  flex-shrink: 0;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dir-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: -0.02em;
}

.copy-dir-btn {
  opacity: 0.5;
  transition: opacity 0.15s;
}
.copy-dir-btn:hover {
  opacity: 1;
}

.box-badge {
  font-size: 12px;
  padding: 2px 8px;
  background: var(--surface-variant);
  border: 1px solid var(--stroke);
  border-radius: 12px;
  color: var(--muted);
}


.header-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.text-muted {
  color: var(--muted);
  font-size: 14px;
  margin: 0;
}

.terminal-container {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
}

.page.mobile .terminal-container {
  border-radius: 0;
}

.no-box-selected {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
}

.empty-state {
  text-align: center;
  max-width: 400px;
  padding: 32px;
}

.empty-icon {
  color: var(--muted);
  margin-bottom: 16px;
}

.empty-state h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
}

.empty-state p {
  margin: 0 0 24px 0;
}

/* ==============================
   MOBILE HEADER - ultra compact
   ============================== */
.mobile-header {
  display: flex;
  align-items: center;
  gap: 0;
  height: 32px;
  padding: 0 4px;
  background: var(--panel-bg-translucent);
  border-bottom: 1px solid var(--stroke);
  flex-shrink: 0;
  z-index: 10;
}

.mobile-back-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  flex-shrink: 0;
}

.mobile-back-btn:active {
  color: var(--text);
}

.mobile-title-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  height: 32px;
  padding: 0 4px;
  background: none;
  border: none;
  color: var(--text);
  cursor: pointer;
  overflow: hidden;
}

.mobile-title-text {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mobile-box-text {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.mobile-caret {
  color: var(--muted);
  transition: transform 0.15s ease;
  flex-shrink: 0;
}

.mobile-caret.expanded {
  transform: rotate(180deg);
}

.mobile-header-actions {
  display: flex;
  align-items: center;
  gap: 0;
  flex-shrink: 0;
}

.mobile-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  text-decoration: none;
}

.mobile-action-btn:active,
.mobile-action-btn.active {
  color: var(--text);
}

/* Mobile controls dropdown */
.mobile-controls-dropdown {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  background: var(--panel-bg-solid);
  border-bottom: 1px solid var(--stroke);
  flex-shrink: 0;
  z-index: 9;
}


.terminal-container :deep(.terminal-wrapper) {
  height: 100%;
}

.favorite-btn {
  transition: transform 0.15s ease;
}

.favorite-btn:hover {
  transform: scale(1.1);
}

.header-link-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 8px;
  border-radius: 4px;
  color: var(--text);
  text-decoration: none;
  background: transparent;
  border: 1px solid var(--stroke);
  transition: background 0.15s ease, border-color 0.15s ease;
}

.header-link-btn:hover {
  background: var(--surface-hover);
  border-color: var(--accent);
}

/* Tablet breakpoint — keep the beauty, tighten the controls */
@media (max-width: 1024px) and (min-width: 769px) {
  .page {
    padding: 0 12px;
    gap: 8px;
  }

  .header-content {
    flex-wrap: wrap;
    gap: 8px;
  }

  .header-controls {
    flex-wrap: wrap;
    gap: 6px;
  }

  .dir-title {
    font-size: 18px;
  }

  .terminal-container {
    border-radius: 6px;
  }
}

@media (prefers-contrast: high) {
  .no-box-selected {
    border: 2px solid var(--stroke);
  }
}

</style>
