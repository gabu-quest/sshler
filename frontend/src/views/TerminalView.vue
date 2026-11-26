<script setup lang="ts">
import { errorText } from '@/api/apiError'
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { NSelect, NButton, NIcon, NInput, NPopover, useMessage } from 'naive-ui'
import {
  PhTerminalWindow, PhArrowLeft, PhStar, PhFolderOpen,
  PhCaretDown, PhList, PhBookmarkSimple, PhArrowsLeftRight,
  PhClipboard, PhX,
} from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import { useAppStore } from '@/stores/app'
import { useResponsive } from '@/composables/useResponsive'
import { useI18n } from '@/i18n'
import { WINDOWS_SHELL_KEY, normalizePosixShell, useShellOptions, type PosixShell } from '@/composables/useShellOptions'
import { generateSessionName, lastPathSegment } from '@/utils/sessionName'
import { useDirectoryOptions, BROWSE_DIRECTORY, CUSTOM_DIRECTORY } from '@/composables/useDirectoryOptions'
import { answerBox, useBoxAnswer } from '@/composables/useBoxAnswer'
import { TERMINAL_THEME_NAMES, terminalThemeOf, type TerminalThemeName } from '@/utils/terminalThemes'
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
// The custom folder field edits this draft; the folder (part of the terminal's key) is
// applied on Enter or blur, so typing does not reconnect the terminal on each key.
const directoryDraft = ref('')
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
    // Silent on purpose: the git badge is a decoration, and its absence is the answer.
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
  if (posixShell.value === 'plain') {
    // A session is tmux: the plain shell's terminal is replaced by a tmux one on it.
    posixShell.value = 'tmux'
    sessionName.value = session.session_name
  } else if (terminalRef.value) {
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

// --- The Shell choice ------------------------------------------------------
// A Windows local box picks one of its native shells (kept across visits). Every other
// box picks "tmux" or "Plain shell" for this terminal only: each new terminal starts as tmux.

const { isWindowsLocalBox, optionsOf } = useShellOptions()
const windowsLocal = computed(() => isWindowsLocalBox(selectedBox.value))

// Tabs are a Windows-local-box feature. Remote/tmux boxes already multiplex via
// tmux windows (Ctrl+B), but a native ConPTY shell has no multiplexer — so we
// give those boxes browser-side tabs instead.
const tabbedMode = windowsLocal

// The picker shows on every box that is open.
const showShellPicker = computed(() => !!selectedBox.value)

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

const shellOptions = optionsOf(() => selectedBox.value)

const selectedShell = ref<string>(
  (typeof localStorage !== 'undefined' && localStorage.getItem(WINDOWS_SHELL_KEY)) || ''
)
// The kind of the terminal on a tmux-or-plain box; nothing is stored or put in the address.
const posixShell = ref<PosixShell>('tmux')

// Set when the server opened a plain shell where tmux was wanted (the box has none):
// the picker then shows what is running. `shellAttempt` makes picking tmux again a new terminal.
const fellBackToPlain = ref(false)
const shellAttempt = ref(0)
// `openedKind` is what the terminal reported it opened; it is null again for each new terminal.
const openedKind = ref<string | null>(null)
const onTerminalShell = (opened: { kind: string; fallback: boolean }) => {
  fellBackToPlain.value = opened.fallback
  openedKind.value = opened.kind
}

// What the picker shows, and what <Terminal> is asked for: a Windows shell id, or
// "plain" (tmux is the empty string, so Terminal omits the query param).
const pickerValue = computed(() =>
  windowsLocal.value ? selectedShell.value : fellBackToPlain.value ? 'plain' : posixShell.value,
)
const activeShell = computed(() =>
  windowsLocal.value ? selectedShell.value : posixShell.value === 'plain' ? 'plain' : '',
)

// Is the terminal on screen a plain shell (no tmux)? What it said it opened, or before
// that what the page asked for; a Windows local box's shells are never tmux.
const plainTerminal = computed(
  () => windowsLocal.value || (openedKind.value ?? activeShell.value) === 'plain',
)

// Another box or directory is a new terminal, and a new terminal starts as tmux.
watch([selectedBox, initialDirectory], () => {
  posixShell.value = 'tmux'
  fellBackToPlain.value = false
})

// The terminal is re-created when its key changes: what it reported belongs to the old one.
watch([selectedBox, initialDirectory, activeShell, shellAttempt], () => {
  openedKind.value = null
})

function handleShellChange(value: string) {
  if (!windowsLocal.value) {
    const picked = normalizePosixShell(value)
    if (picked === posixShell.value && fellBackToPlain.value) shellAttempt.value++ // tmux again
    posixShell.value = picked
    fellBackToPlain.value = false
    return
  }
  selectedShell.value = value
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(WINDOWS_SHELL_KEY, value)
  }
}

const isCurrentDirFavorite = computed(() =>
  selectedBox.value ? favoritesStore.isFavorite(selectedBox.value, initialDirectory.value) : false
)

// A box with no saved theme (or one this page no longer knows) gets the calm theme; a
// saved theme is kept.
const currentBoxTheme = computed<TerminalThemeName>(() =>
  terminalThemeOf(boxesStore.items.find(b => b.name === selectedBox.value)?.terminal_theme),
)

const themeOptions = TERMINAL_THEME_NAMES.map(name => ({ label: name.charAt(0).toUpperCase() + name.slice(1), value: name }))

const handleThemeChange = async (theme: string) => {
  if (!selectedBox.value) return
  try {
    await setBoxTerminalTheme(selectedBox.value, theme, tokenValue.value)
    // Update local box data
    const box = boxesStore.items.find(b => b.name === selectedBox.value)
    if (box) box.terminal_theme = theme
  } catch (err) {
    message.error(errorText(err))
  }
}

const toggleCurrentDirFavorite = async () => {
  if (selectedBox.value) {
    await favoritesStore.toggle(selectedBox.value, initialDirectory.value, tokenValue.value)
  }
}

// The one directory list (home, favourites, Browse, custom path), its own entries named
// in the page's language by the composable.
const directoryOptions = useDirectoryOptions(() => selectedBox.value)

const handleDirectoryChange = (value: string) => {
  if (value === BROWSE_DIRECTORY) {
    showDirPicker.value = true
    return
  }
  if (value === CUSTOM_DIRECTORY) {
    directoryDraft.value = initialDirectory.value
    showManualDir.value = true
    return
  }
  showManualDir.value = false
  initialDirectory.value = value
  sessionName.value = generateSessionName(value, selectedBox.value ?? undefined)
}

const applyDirectoryDraft = () => {
  initialDirectory.value = directoryDraft.value
  sessionName.value = generateSessionName(directoryDraft.value, selectedBox.value ?? undefined)
}

const handleDirPickerSelect = (path: string) => {
  initialDirectory.value = path
  sessionName.value = generateSessionName(path, selectedBox.value ?? undefined)
}

// Display name for the directory (last component)
const displayDirName = computed(() => {
  if (!initialDirectory.value || initialDirectory.value === '~') {
    return t('common.home')
  }
  return lastPathSegment(initialDirectory.value) || t('terminal.root_label')
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

// A ?box= nobody has opens no terminal: the page says so and waits for a pick.
const unknownBox = ref<string | null>(null)
watch(selectedBox, (box) => { if (box) unknownBox.value = null })
const { unknownText } = useBoxAnswer(() => unknownBox.value, () => boxesStore.items)

const initializeFromRoute = () => {
  const dirFromRoute = route.query.dir as string

  const answer = answerBox(route.query.box as string | null | undefined, boxesStore.items)
  if (answer.kind === 'known') {
    selectedBox.value = answer.box.name
  } else if (answer.kind === 'unknown') {
    unknownBox.value = answer.name
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
  if (boxesStore.items.length > 0 && !selectedBox.value && !unknownBox.value) {
    initializeFromRoute()
  }
}, { immediate: true })
</script>

<template>
  <div class="page" :class="{ 'mobile': isMobile }">
    <!-- Desktop: one slim row -->
    <header v-if="!isMobile" class="page-header">
      <div class="header-left">
        <NButton size="tiny" quaternary @click="goBack" :title="t('terminal.go_back')" :aria-label="t('terminal.go_back')">
          <NIcon size="14"><PhArrowLeft weight="duotone" /></NIcon>
        </NButton>
        <h1 class="dir-title" :title="initialDirectory">{{ displayDirName }}</h1>
        <NButton v-if="selectedBox" size="tiny" quaternary @click="copyDirToClipboard" :title="t('files.copy_path')" :aria-label="t('files.copy_path')" class="copy-dir-btn">
          <NIcon size="14"><PhClipboard weight="duotone" /></NIcon>
        </NButton>
        <span class="box-badge">{{ selectedBox ? getEmojiForBox(selectedBox) + ' ' + selectedBox : t('terminal.no_box') }}</span>
        <GitBadge v-if="currentGitInfo?.is_repo" class="header-git" :info="currentGitInfo" compact />
      </div>

      <div class="header-controls">
        <NSelect
          v-model:value="selectedBox"
          class="header-select header-select--box"
          :options="boxOptions"
          :placeholder="t('terminal.box')"
          :disabled="boxesStore.loading"
          @update:value="handleBoxChange"
          size="small"
        />
        <NSelect
          v-if="selectedBox && !showManualDir"
          class="header-select header-select--dir"
          :value="initialDirectory"
          :options="directoryOptions"
          :placeholder="t('terminal.dir')"
          @update:value="handleDirectoryChange"
          size="small"
        />
        <NInput
          v-if="showManualDir"
          v-model:value="directoryDraft"
          class="header-select header-select--dir"
          :placeholder="t('terminal.dir_placeholder')"
          size="small"
          @blur="applyDirectoryDraft"
          @keyup.enter="applyDirectoryDraft"
        />
        <NButton v-if="showManualDir" size="small" quaternary :title="t('common.close')" :aria-label="t('common.close')" @click="showManualDir = false">
          <NIcon size="14"><PhX weight="bold" /></NIcon>
        </NButton>
        <NSelect
          v-if="showShellPicker"
          class="shell-select header-select header-select--shell"
          :value="pickerValue"
          :options="shellOptions"
          :placeholder="t('terminal.shell_label')"
          @update:value="handleShellChange"
          size="small"
        />
        <NSelect
          v-if="selectedBox"
          class="header-select header-select--theme"
          :value="currentBoxTheme"
          :options="themeOptions"
          @update:value="handleThemeChange"
          size="small"
        />

        <NButton
          v-if="selectedBox"
          size="small"
          quaternary
          @click="toggleCurrentDirFavorite"
          :title="isCurrentDirFavorite ? t('terminal.remove_favorite') : t('terminal.add_favorite')"
          :aria-label="isCurrentDirFavorite ? t('terminal.remove_favorite') : t('terminal.add_favorite')"
          :aria-pressed="isCurrentDirFavorite"
          class="favorite-btn"
          :class="{ 'is-favorite': isCurrentDirFavorite }"
        >
          <NIcon size="14">
            <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'duotone'" />
          </NIcon>
        </NButton>

        <!-- Windows local tabs: their title bars do not pass a pick on, so the view keeps
             the switcher that opens a session as a tab. A single terminal has its own. -->
        <NPopover v-if="selectedBox && tabbedMode" trigger="click" placement="bottom-end" v-model:show="showSessionPanel">
          <template #trigger>
            <NButton size="small" quaternary :title="t('sessions.title')" :aria-label="t('sessions.title')">
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

        <NButton v-if="selectedBox" size="small" quaternary :title="t('snippets.title')" :aria-label="t('snippets.title')" @click="showSnippetsPanel = true">
          <NIcon size="14"><PhBookmarkSimple weight="duotone" /></NIcon>
        </NButton>

        <NButton v-if="selectedBox" size="small" quaternary :title="t('tunnels.title')" :aria-label="t('tunnels.title')" @click="showTunnelsPanel = true">
          <NIcon size="14"><PhArrowsLeftRight weight="duotone" /></NIcon>
        </NButton>

        <a
          v-if="selectedBox"
          :href="filesUrl"
          class="header-link-btn"
          :title="t('terminal.browse_files')"
          :aria-label="t('terminal.browse_files')"
          @click.prevent="goToFiles"
        >
          <NIcon size="14"><PhFolderOpen weight="duotone" /></NIcon>
        </a>
      </div>
    </header>

    <!-- Phone: one 32 px row under the 14 px bar; the menu button opens the drawer -->
    <header v-if="isMobile" class="mobile-header">
      <button class="mobile-action-btn" :aria-label="t('header.menu')" :title="t('header.menu')" :aria-expanded="appStore.mobileNavOpen" @click="appStore.mobileNavOpen = true">
        <NIcon size="14"><PhList weight="bold" /></NIcon>
      </button>
      <button class="mobile-back-btn" @click="goBack" :title="t('common.back')" :aria-label="t('common.back')">
        <NIcon size="14"><PhArrowLeft weight="duotone" /></NIcon>
      </button>
      <button class="mobile-title-btn" :aria-expanded="mobileControlsExpanded" @click="toggleMobileControls">
        <span class="mobile-title-text">{{ displayDirName }}</span>
        <span class="mobile-box-text">{{ selectedBox ? getEmojiForBox(selectedBox) + ' ' + selectedBox : '' }}</span>
        <NIcon size="10" class="mobile-caret" :class="{ expanded: mobileControlsExpanded }"><PhCaretDown weight="duotone" /></NIcon>
      </button>
      <div class="mobile-header-actions">
        <button v-if="selectedBox" class="mobile-action-btn" @click="copyDirToClipboard" :aria-label="t('files.copy_path')">
          <NIcon size="14"><PhClipboard weight="duotone" /></NIcon>
        </button>
        <button
          v-if="selectedBox"
          class="mobile-action-btn"
          :class="{ active: isCurrentDirFavorite, 'is-favorite': isCurrentDirFavorite }"
          :aria-label="isCurrentDirFavorite ? t('terminal.remove_favorite') : t('terminal.add_favorite')"
          :aria-pressed="isCurrentDirFavorite"
          @click="toggleCurrentDirFavorite"
        >
          <NIcon size="14">
            <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'duotone'" />
          </NIcon>
        </button>
        <button v-if="selectedBox" class="mobile-action-btn" @click="showSnippetsPanel = true" :aria-label="t('snippets.title')">
          <NIcon size="14"><PhBookmarkSimple weight="duotone" /></NIcon>
        </button>
        <button v-if="selectedBox" class="mobile-action-btn" @click="showTunnelsPanel = true" :aria-label="t('tunnels.title')">
          <NIcon size="14"><PhArrowsLeftRight weight="duotone" /></NIcon>
        </button>
        <a
          v-if="selectedBox"
          :href="filesUrl"
          class="mobile-action-btn"
          :aria-label="t('terminal.browse_files')"
          @click.prevent="goToFiles"
        >
          <NIcon size="14"><PhFolderOpen /></NIcon>
        </a>
      </div>
    </header>

    <!-- Mobile Controls Dropdown -->
    <Transition name="slide-down-sm">
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
        v-model:value="directoryDraft"
        :placeholder="t('terminal.dir_placeholder')"
        size="small"
        @blur="applyDirectoryDraft"
        @keyup.enter="applyDirectoryDraft"
      />
      <NSelect
        v-if="showShellPicker"
        class="shell-select"
        :value="pickerValue"
        :options="shellOptions"
        :placeholder="t('terminal.shell_label')"
        @update:value="handleShellChange"
        size="small"
      />
      <GitBadge v-if="currentGitInfo?.is_repo" :info="currentGitInfo" compact />
    </div>
    </Transition>

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
        @session-select="handleSessionSelect"
      />

      <!-- Remote / local-tmux boxes: single terminal (tmux handles windows) -->
      <Terminal
        v-else-if="selectedBox"
        ref="terminalRef"
        :key="selectedBox + '-' + initialDirectory + '-' + activeShell + '-' + shellAttempt"
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
        @shell="onTerminalShell"
        @session-select="handleSessionSelect"
      />

      <div v-else class="no-box-selected">
        <div class="empty-state">
          <NIcon size="48" class="empty-icon"><PhTerminalWindow weight="duotone" /></NIcon>
          <template v-if="unknownBox">
            <h3>{{ t('terminal.unknown_box_named', { box: unknownBox }) }}</h3>
            <p class="text-muted">{{ unknownText }}</p>
          </template>
          <template v-else>
            <h3>{{ t('terminal.no_box') }}</h3>
            <p class="text-muted">{{ t('terminal.no_box_hint') }}</p>
          </template>
          <NButton
            v-if="boxOptions.length > 0"
            type="primary"
            @click="selectedBox = boxOptions[0]?.value || null"
          >
            {{ t('terminal.connect_to') }} {{ boxOptions[0]?.label }}
          </NButton>
        </div>
      </div>
    </div>

    <!-- Mobile Smart Input Bar -->
    <MobileInputBar
      v-if="isMobile && selectedBox"
      :raw-mode="rawMode"
      :connected="terminalConnected"
      :tmux="!plainTerminal"
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
/* The view fills the frame the app gives it: the header row, then the terminal, which
   takes the rest of the height. */
.page {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 100%;
  overflow: hidden;
  width: 100%;
  max-width: none;
  min-width: 0;
  padding: 4px 8px 6px;
  box-sizing: border-box;
}

.page.mobile {
  gap: 0;
  padding: 0;
}

/* One row, at most 32 px: the title shrinks (with an ellipsis) before anything wraps. */
.page-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 32px;
  min-width: 0;
  overflow: hidden;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.dir-title {
  margin: 0;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 260px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.copy-dir-btn {
  flex-shrink: 0;
  opacity: 0.5;
  transition: opacity var(--dur-fast) var(--ease-out);
}
.copy-dir-btn:hover {
  opacity: 1;
}

.box-badge {
  flex-shrink: 0;
  font-size: 12px;
  line-height: 18px;
  padding: 0 8px;
  white-space: nowrap;
  background: var(--surface-variant);
  border: 1px solid var(--stroke);
  border-radius: 10px;
  color: var(--muted);
}

.header-git {
  flex-shrink: 0;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 1 auto;
  min-width: 0;
}

.header-select {
  flex: 0 1 auto;
  min-width: 72px;
}
.header-select--box { width: 168px; }
.header-select--dir { width: 150px; }
.header-select--shell { width: 116px; }
.header-select--theme { width: 112px; }

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
  border-radius: var(--radius-lg);
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
  border-radius: var(--radius-lg);
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
  transition: transform var(--dur-fast) var(--ease-out);
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
  flex-shrink: 0;
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
  transition: transform var(--dur-instant) var(--ease-out);
}

.favorite-btn:hover {
  transform: scale(1.1);
}

.favorite-btn:active {
  transform: scale(0.92);
}

.is-favorite,
.mobile-action-btn.is-favorite {
  color: var(--warning);
}

.header-link-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  height: 28px;
  padding: 0 8px;
  box-sizing: border-box;
  border-radius: var(--radius-sm);
  color: var(--text);
  text-decoration: none;
  background: transparent;
  border: 1px solid var(--stroke);
  transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
}

.header-link-btn:hover {
  background: var(--surface-hover);
  border-color: var(--accent);
}

/* The box select names the box, so the badge goes first when the row is short; then the
   git mark and the copy button, and the selects narrow. */
@media (max-width: 1279px) {
  .box-badge {
    display: none;
  }
}

@media (max-width: 1023px) and (min-width: 768px) {
  .header-git,
  .copy-dir-btn {
    display: none;
  }
  .header-select--box { width: 124px; }
  .header-select--dir { width: 116px; }
  .header-select--shell { width: 96px; }
  .header-select--theme { width: 96px; }
}

@media (prefers-contrast: high) {
  .no-box-selected {
    border: 2px solid var(--stroke);
  }
}

</style>
