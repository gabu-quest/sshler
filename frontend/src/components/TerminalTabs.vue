<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, watch } from 'vue'

import Terminal from '@/components/Terminal.vue'
import TerminalTabBar from '@/components/TerminalTabBar.vue'
import { useTerminalTabsStore, scopeKey } from '@/stores/terminalTabs'
import { killTerminalSession } from '@/api/http'
import type { ApiSession } from '@/api/types'

/**
 * Multi-tab terminal host (Windows-local boxes). Tabs are scoped per
 * (box, directory): each directory has its own strip, persisted independently.
 * Owns the tab store interaction, keeps the current scope's <Terminal>s mounted
 * (v-show) so background tabs stay live, and re-fits the active terminal on
 * switch. When `directory` changes, the strip swaps to that directory's scope —
 * the previous scope's shells keep running server-side (ConPTY registry) and
 * re-attach when the user navigates back. The parent (TerminalView) treats this
 * like a single Terminal: it calls send/sendRaw/focus and reads the active tab's
 * directory/session via update events for the header.
 */
interface Props {
  boxName: string
  /** Current directory — the scope driver and the seed dir for a fresh scope. */
  directory?: string
  theme?: 'cyberpunk' | 'default' | 'solarized' | 'dracula' | 'nord' | 'monokai' | 'light'
  fontSize?: number
  fontFamily?: string
  showTitleBar?: boolean
  externalInput?: boolean
  /** Shell used to seed the first tab when a scope has nothing persisted yet. */
  seedShell?: string
  /** Auth token — required to kill a server-side shell ("kill terminal"). */
  token?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  directory: '~',
  theme: 'cyberpunk',
  fontSize: 14,
  showTitleBar: true,
  externalInput: false,
  seedShell: '',
})

const emit = defineEmits<{
  (e: 'connected'): void
  (e: 'disconnected'): void
  (e: 'update:activeDirectory', dir: string): void
  (e: 'update:activeSession', session: string): void
  (e: 'killed', sessionName: string): void
  (e: 'killError', sessionName: string): void
}>()

const store = useTerminalTabsStore()

// A scope is one (box, directory) pair — its own independent tab strip.
const scope = computed(() => scopeKey(props.boxName, props.directory))

const tabList = computed(() => store.tabs(scope.value))
const activeId = computed(() => store.activeTabId(scope.value))

// Connection state per tab id (transient — not persisted).
const connectedByTab = reactive<Record<string, boolean>>({})

// Exposed-method surface of a <Terminal> instance.
type TermInstance = {
  send: (d: string) => void
  sendRaw: (d: string) => void
  focus: () => void
  fit: () => void
}
const termRefs = new Map<string, TermInstance>()
function setTermRef(id: string, el: unknown): void {
  if (el) termRefs.set(id, el as TermInstance)
  else termRefs.delete(id)
}

function activeTerm(): TermInstance | null {
  const id = activeId.value
  return id ? termRefs.get(id) ?? null : null
}

function emitActiveContext(): void {
  const id = activeId.value
  if (!id) return
  const tab = tabList.value.find((t) => t.id === id)
  if (tab) {
    emit('update:activeDirectory', tab.directory)
    emit('update:activeSession', tab.sessionName)
  }
  if (connectedByTab[id]) emit('connected')
  else emit('disconnected')
}

// --- connection events from individual terminals -------------------------
function onTabConnected(id: string): void {
  connectedByTab[id] = true
  if (id === activeId.value) emit('connected')
}
function onTabDisconnected(id: string): void {
  connectedByTab[id] = false
  if (id === activeId.value) emit('disconnected')
}

// --- tab bar actions ------------------------------------------------------
function onSelect(id: string): void {
  store.activateTab(scope.value, id)
}
function onClose(id: string): void {
  store.closeTab(scope.value, id)
  delete connectedByTab[id]
  termRefs.delete(id)
}

/**
 * Kill a tab's shell FOR GOOD: terminate the server-side ConPTY, then remove the
 * tab locally — so reopening the same directory spawns a fresh shell instead of
 * re-attaching to the old (e.g. laggy) one. Distinct from {@link onClose}, which
 * only detaches and leaves the shell running.
 *
 * On a kill failure the tab is KEPT (the shell may still be alive — silently
 * dropping it would re-create the "reopen lands on the same dead-feeling shell"
 * bug); the parent surfaces an error toast.
 */
async function onKill(id: string): Promise<void> {
  const tab = tabList.value.find((t) => t.id === id)
  if (!tab) return
  if (props.boxName && props.token) {
    try {
      await killTerminalSession(props.boxName, tab.sessionName, props.token)
    } catch {
      emit('killError', tab.sessionName)
      return
    }
  }
  // The ConPTY is gone (killed, or never tracked) — drop the tab locally.
  store.closeTab(scope.value, id)
  delete connectedByTab[id]
  termRefs.delete(id)
  emit('killed', tab.sessionName)
}
function onAdd(): void {
  // New tabs stay in the current directory (all tabs in a scope share its dir);
  // inherit the active tab's shell so "+" clones the shell you're using.
  const active = store.activeTab(scope.value)
  store.addTab(scope.value, {
    directory: props.directory,
    shell: active?.shell ?? props.seedShell,
  })
}
function onRename(id: string, label: string): void {
  store.renameTab(scope.value, id, label)
}

// --- exposed API (parent drives the active tab) --------------------------
function send(data: string): void {
  activeTerm()?.send(data)
}
function sendRaw(data: string): void {
  activeTerm()?.sendRaw(data)
}
function focus(): void {
  activeTerm()?.focus()
}
function addTab(opts: { directory: string; shell: string }): void {
  // Adds within the current scope (the parent switches `directory` to change
  // scope); `opts.directory` is recorded on the tab for display/metadata.
  store.addTab(scope.value, { directory: opts.directory, shell: opts.shell })
}
function openSession(session: ApiSession): void {
  // The tab lives in the CURRENT scope, so its directory must be the scope's
  // directory — emitActiveContext emits it back to the header, and a mismatch
  // would bounce initialDirectory and swap the scope out from under the tab.
  // (The live ConPTY keeps its own real cwd regardless of this metadata.)
  store.openOrActivateSession(scope.value, {
    sessionName: session.session_name,
    directory: props.directory,
    shell: props.seedShell,
  })
}
defineExpose({ send, sendRaw, focus, addTab, openSession })

// --- keyboard shortcuts (Alt+T/W, Alt+1-9, Alt+Arrows) -------------------
function cycle(dir: number): void {
  const list = tabList.value
  if (list.length < 2) return
  const idx = list.findIndex((t) => t.id === activeId.value)
  const next = (idx + dir + list.length) % list.length
  const target = list[next]
  if (target) store.activateTab(scope.value, target.id)
}

function onKeydown(e: KeyboardEvent): void {
  // Alt-based shortcuts only — leave Ctrl/Meta combos alone.
  if (!e.altKey || e.ctrlKey || e.metaKey) return
  const code = e.code
  if (e.shiftKey) {
    // Alt+Shift+W kills the active terminal for good (vs Alt+W which detaches).
    if (code === 'KeyW') {
      e.preventDefault()
      const id = activeId.value
      if (id) void onKill(id)
    }
    return
  }
  if (code === 'KeyT') {
    e.preventDefault()
    onAdd()
  } else if (code === 'KeyW') {
    e.preventDefault()
    const id = activeId.value
    if (id) onClose(id)
  } else if (code === 'ArrowRight') {
    e.preventDefault()
    cycle(1)
  } else if (code === 'ArrowLeft') {
    e.preventDefault()
    cycle(-1)
  } else if (/^Digit[1-9]$/.test(code)) {
    e.preventDefault()
    const idx = Number(code.slice(5)) - 1
    const tab = tabList.value[idx]
    if (tab) store.activateTab(scope.value, tab.id)
  }
}

// Re-fit + focus the active terminal AFTER a paint frame. nextTick is a
// microtask that runs before the browser paints, but xterm only re-measures its
// character cell during a render frame — so fitting a just-revealed pane on
// nextTick measures a stale cell and leaves it squished. Two rAFs guarantee the
// pane has been painted (and its cell measured) before we fit. A short timeout
// backstops environments that throttle rAF for the freshly-shown pane.
function refitActive(): void {
  const run = () => {
    const term = activeTerm()
    term?.fit()
    term?.focus()
  }
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => requestAnimationFrame(run))
  } else {
    nextTick(run)
  }
  setTimeout(run, 120)
}

// Re-fit + focus the active terminal whenever the active tab changes, and keep
// the parent's header in sync with the active tab's directory/session.
watch(activeId, () => {
  emitActiveContext()
  refitActive()
})

// Load the scope's tabs on mount and whenever the directory (scope) changes.
// loadScope restores a persisted scope or seeds a fresh single tab, so changing
// directory swaps the strip instead of stranding the user in the old dir's tabs.
watch(
  scope,
  () => {
    store.loadScope(scope.value, { directory: props.directory, shell: props.seedShell })
    emitActiveContext()
    refitActive()
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown, true)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div class="terminal-tabs">
    <TerminalTabBar
      :tabs="tabList"
      :active-tab-id="activeId"
      :connected-by-tab="connectedByTab"
      @select="onSelect"
      @close="onClose"
      @kill="onKill"
      @add="onAdd"
      @rename="onRename"
    />
    <div class="terminal-tabs-body">
      <!--
        Background tabs are hidden with `visibility`, NOT `v-show`/display:none.
        A terminal first laid out at display:none measures its character cell as
        0, and FitAddon can't derive columns from a zero cell — so on reveal it
        stays squished (~20 cols). Keeping every pane laid out (stacked, filling
        the body) means xterm always measures a real cell and fits correctly;
        only the active pane is visible + interactive.
      -->
      <Terminal
        v-for="tab in tabList"
        :key="tab.id"
        :class="['tab-pane', { 'tab-pane--active': tab.id === activeId }]"
        :ref="(el) => setTermRef(tab.id, el)"
        :box-name="boxName"
        :session-name="tab.sessionName"
        :directory="tab.directory"
        :shell="tab.shell"
        :native-shell="true"
        :theme="theme"
        :font-size="fontSize"
        :font-family="fontFamily"
        :show-title-bar="showTitleBar"
        :external-input="externalInput"
        @connected="onTabConnected(tab.id)"
        @disconnected="onTabDisconnected(tab.id)"
      />
    </div>
  </div>
</template>

<style scoped>
.terminal-tabs {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  width: 100%;
}

.terminal-tabs-body {
  position: relative;
  flex: 1;
  min-height: 0;
  width: 100%;
}

/*
 * Every pane is stacked and fully sized (so xterm measures a real cell even
 * while in the background); only the active pane is visible + interactive.
 * Using visibility (not display:none) keeps the cell measurable, which is what
 * lets FitAddon size a tab correctly the first time it's revealed.
 */
.terminal-tabs-body :deep(.terminal-wrapper.tab-pane) {
  position: absolute;
  inset: 0;
  height: 100%;
  visibility: hidden;
  z-index: 0;
}

.terminal-tabs-body :deep(.terminal-wrapper.tab-pane--active) {
  visibility: visible;
  z-index: 1;
}
</style>
