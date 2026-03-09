<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { NButton, NIcon, NSpace, NTag, useMessage } from 'naive-ui'
import { useResponsive } from '@/composables/useResponsive'
import {
  PhPlus,
  PhSplitHorizontal,
  PhSplitVertical,
  PhTerminalWindow
} from '@phosphor-icons/vue'
import Terminal from './Terminal.vue'

interface TerminalPane {
  id: string
  boxName: string
  sessionName: string
  directory: string
  active: boolean
  connected: boolean
}

interface SavedLayout {
  boxName: string
  layout: 'single' | 'horizontal' | 'vertical' | 'grid'
  panes: { sessionName: string; directory: string }[]
  activeIndex: number
  savedAt: number
}

const STORAGE_KEY = 'sshler:terminal:layout'
const LAYOUT_MAX_AGE_MS = 24 * 60 * 60 * 1000

interface Props {
  boxName: string
  initialDirectory?: string
  persistLayout?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  initialDirectory: '~',
  persistLayout: true,
})

const emit = defineEmits<{
  (e: 'restore-available', layout: SavedLayout): void
}>()

const message = useMessage()
const { isMobile } = useResponsive()

const panes = ref<TerminalPane[]>([])
const activePane = ref<string | null>(null)
const layout = ref<'single' | 'horizontal' | 'vertical' | 'grid'>('single')
const terminalRefs = ref<Record<string, InstanceType<typeof Terminal>>>({})

const activePaneData = computed(() => 
  panes.value.find(p => p.id === activePane.value)
)

const connectedPanes = computed(() => 
  panes.value.filter(p => p.connected).length
)

const createPane = (sessionName?: string, directory?: string) => {
  const id = `pane-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  const pane: TerminalPane = {
    id,
    boxName: props.boxName,
    sessionName: sessionName || `session-${panes.value.length + 1}`,
    directory: directory || props.initialDirectory,
    active: false,
    connected: false
  }
  
  panes.value.push(pane)
  setActivePane(id)
  
  return pane
}

const removePane = (paneId: string) => {
  const index = panes.value.findIndex(p => p.id === paneId)
  if (index === -1) return
  
  // Disconnect terminal
  const terminalRef = terminalRefs.value[paneId]
  if (terminalRef) {
    terminalRef.disconnect()
    delete terminalRefs.value[paneId]
  }
  
  panes.value.splice(index, 1)

  // Set new active pane
  if (activePane.value === paneId) {
    if (panes.value.length > 0) {
      setActivePane(panes.value[Math.max(0, index - 1)].id)
    } else {
      activePane.value = null
      clearSavedLayout()
    }
  }
}

const setActivePane = (paneId: string) => {
  panes.value.forEach(p => p.active = p.id === paneId)
  activePane.value = paneId
  
  // Focus the terminal
  setTimeout(() => {
    const terminalRef = terminalRefs.value[paneId]
    if (terminalRef && terminalRef.focus) {
      terminalRef.focus()
    }
  }, 100)
}

const splitHorizontal = () => {
  if (!activePaneData.value) return
  
  createPane(
    `${activePaneData.value.sessionName}-h`,
    activePaneData.value.directory
  )
  
  if (layout.value === 'single') {
    layout.value = 'horizontal'
  } else if (layout.value === 'vertical') {
    layout.value = 'grid'
  }
  
  message.success('Split horizontally')
}

const splitVertical = () => {
  if (!activePaneData.value) return
  
  createPane(
    `${activePaneData.value.sessionName}-v`,
    activePaneData.value.directory
  )
  
  if (layout.value === 'single') {
    layout.value = 'vertical'
  } else if (layout.value === 'horizontal') {
    layout.value = 'grid'
  }
  
  message.success('Split vertically')
}

const handlePaneConnected = (paneId: string) => {
  const pane = panes.value.find(p => p.id === paneId)
  if (pane) {
    pane.connected = true
  }
}

const handlePaneDisconnected = (paneId: string) => {
  const pane = panes.value.find(p => p.id === paneId)
  if (pane) {
    pane.connected = false
  }
}

const handleBell = (paneId: string) => {
  const pane = panes.value.find(p => p.id === paneId)
  if (pane && !pane.active) {
    // Flash the pane tab
    const tabElement = document.querySelector(`[data-pane-id="${paneId}"]`)
    if (tabElement) {
      tabElement.classList.add('bell-flash')
      setTimeout(() => {
        tabElement.classList.remove('bell-flash')
      }, 1000)
    }
  }
}

const handleNotification = (paneId: string, data: { title: string; message: string }) => {
  const pane = panes.value.find(p => p.id === paneId)
  if (pane) {
    message.info(`[${pane.sessionName}] ${data.title}: ${data.message}`)
  }
}

const connectAll = () => {
  Object.values(terminalRefs.value).forEach(ref => {
    if (ref && !ref.connected()) {
      ref.connect()
    }
  })
}

const disconnectAll = () => {
  Object.values(terminalRefs.value).forEach(ref => {
    if (ref && ref.connected()) {
      ref.disconnect()
    }
  })
}

const fitAll = () => {
  Object.values(terminalRefs.value).forEach(ref => {
    if (ref) {
      ref.fit()
    }
  })
}

// Layout persistence
const saveLayout = () => {
  if (!props.persistLayout || panes.value.length === 0) return
  const saved: SavedLayout = {
    boxName: props.boxName,
    layout: layout.value,
    panes: panes.value.map(p => ({
      sessionName: p.sessionName,
      directory: p.directory,
    })),
    activeIndex: panes.value.findIndex(p => p.id === activePane.value),
    savedAt: Date.now(),
  }
  try {
    const all = loadAllLayouts()
    all[props.boxName] = saved
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all))
  } catch { /* quota exceeded etc */ }
}

const loadAllLayouts = (): Record<string, SavedLayout> => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

const getSavedLayout = (): SavedLayout | null => {
  const all = loadAllLayouts()
  const saved = all[props.boxName]
  if (!saved) return null
  if (Date.now() - saved.savedAt > LAYOUT_MAX_AGE_MS) {
    clearSavedLayout()
    return null
  }
  if (saved.panes.length <= 1) return null
  return saved
}

const clearSavedLayout = () => {
  try {
    const all = loadAllLayouts()
    delete all[props.boxName]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all))
  } catch { /* ignore */ }
}

const restoreLayout = (saved: SavedLayout) => {
  // Remove all current panes
  while (panes.value.length > 0) {
    const pane = panes.value[0]
    const terminalRef = terminalRefs.value[pane.id]
    if (terminalRef) {
      terminalRef.disconnect()
      delete terminalRefs.value[pane.id]
    }
    panes.value.splice(0, 1)
  }
  activePane.value = null

  // Recreate panes from saved state
  layout.value = saved.layout
  for (const p of saved.panes) {
    createPane(p.sessionName, p.directory)
  }

  // Restore active pane
  const idx = Math.max(0, Math.min(saved.activeIndex, panes.value.length - 1))
  if (panes.value[idx]) {
    setActivePane(panes.value[idx].id)
  }

  // Connect all panes
  setTimeout(() => connectAll(), 100)
}

// Watch for layout changes to persist
watch(
  () => [panes.value.length, layout.value, activePane.value],
  () => saveLayout(),
)

// Keyboard shortcuts
const handleKeyDown = (event: KeyboardEvent) => {
  const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0
  const ctrlOrCmd = isMac ? event.metaKey : event.ctrlKey
  
  if (ctrlOrCmd && event.shiftKey) {
    switch (event.key) {
      case 'T':
        event.preventDefault()
        createPane()
        break
      case 'W':
        event.preventDefault()
        if (activePane.value) {
          removePane(activePane.value)
        }
        break
      case 'H':
        event.preventDefault()
        splitHorizontal()
        break
      case 'V':
        event.preventDefault()
        splitVertical()
        break
    }
  }
  
  // Tab switching
  if (ctrlOrCmd && !event.shiftKey && event.key >= '1' && event.key <= '9') {
    event.preventDefault()
    const index = parseInt(event.key) - 1
    if (panes.value[index]) {
      setActivePane(panes.value[index].id)
    }
  }
}

// Handle window resize
const handleResize = () => {
  setTimeout(() => {
    fitAll()
  }, 100)
}

onMounted(() => {
  // Check for saved layout
  const saved = props.persistLayout ? getSavedLayout() : null
  if (saved) {
    emit('restore-available', saved)
  }

  // Create initial pane
  createPane('main', props.initialDirectory)

  // Connect to terminal
  setTimeout(() => {
    connectAll()
  }, 100)

  // Add keyboard shortcuts
  document.addEventListener('keydown', handleKeyDown)

  // Handle window resize
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('resize', handleResize)
  disconnectAll()
})

defineExpose({
  createPane,
  removePane,
  setActivePane,
  splitHorizontal,
  splitVertical,
  connectAll,
  disconnectAll,
  fitAll,
  restoreLayout,
  clearSavedLayout,
  panes: () => panes.value,
  activePane: () => activePane.value,
})
</script>

<template>
  <div class="multi-pane-terminal">
    <!-- Terminal tabs -->
    <div class="terminal-tabs" v-if="panes.length > 1 || isMobile">
      <div class="tabs-container">
        <NTag
          v-for="pane in panes"
          :key="pane.id"
          :data-pane-id="pane.id"
          :type="pane.active ? 'primary' : 'default'"
          :class="{ 'connected': pane.connected }"
          @click="setActivePane(pane.id)"
          closable
          @close="removePane(pane.id)"
        >
          <NIcon size="14" style="margin-right: 4px">
            <PhTerminalWindow weight="duotone" />
          </NIcon>
          {{ pane.sessionName }}
          <span v-if="pane.connected" class="connection-dot" />
        </NTag>
      </div>
      
      <NSpace size="small">
        <NButton size="tiny" @click="createPane()" title="New terminal (Ctrl+Shift+T)">
          <NIcon size="14">
            <PhPlus weight="duotone" />
          </NIcon>
        </NButton>
        <NButton size="tiny" @click="splitHorizontal" title="Split horizontal (Ctrl+Shift+H)">
          <NIcon size="14">
            <PhSplitHorizontal weight="duotone" />
          </NIcon>
        </NButton>
        <NButton size="tiny" @click="splitVertical" title="Split vertical (Ctrl+Shift+V)">
          <NIcon size="14">
            <PhSplitVertical weight="duotone" />
          </NIcon>
        </NButton>
      </NSpace>
    </div>

    <!-- Terminal panes -->
    <div
      class="terminal-panes"
      :class="[`layout-${layout}`, { 'layout-mobile': isMobile }]"
    >
      <div
        v-for="pane in panes"
        :key="pane.id"
        v-show="!isMobile || pane.active"
        class="terminal-pane"
        :class="{
          active: pane.active,
          connected: pane.connected
        }"
        @click="setActivePane(pane.id)"
      >
        <Terminal
          :ref="(el: any) => { if (el) terminalRefs[pane.id] = el }"
          :box-name="pane.boxName"
          :session-name="pane.sessionName"
          :directory="pane.directory"
          @connected="handlePaneConnected(pane.id)"
          @disconnected="handlePaneDisconnected(pane.id)"
          @bell="handleBell(pane.id)"
          @notification="(data: any) => handleNotification(pane.id, data)"
        />
        
        <!-- Pane header for single pane mode -->
        <div v-if="panes.length === 1" class="pane-header">
          <div class="pane-info">
            <NIcon size="16">
              <PhTerminalWindow weight="duotone" />
            </NIcon>
            <span>{{ pane.sessionName }}</span>
            <span v-if="pane.connected" class="connection-status connected">●</span>
            <span v-else class="connection-status disconnected">●</span>
          </div>
          
          <NSpace size="small">
            <NButton size="tiny" @click="createPane()">
              <NIcon size="14">
                <PhPlus />
              </NIcon>
            </NButton>
            <NButton size="tiny" @click="splitHorizontal">
              <NIcon size="14">
                <PhSplitHorizontal />
              </NIcon>
            </NButton>
            <NButton size="tiny" @click="splitVertical">
              <NIcon size="14">
                <PhSplitVertical />
              </NIcon>
            </NButton>
          </NSpace>
        </div>
      </div>
    </div>

    <!-- Status bar -->
    <div class="status-bar">
      <div class="status-info">
        <span>{{ panes.length }} pane{{ panes.length !== 1 ? 's' : '' }}</span>
        <span>{{ connectedPanes }} connected</span>
        <span>{{ props.boxName }}</span>
      </div>
      
      <div class="shortcuts-hint">
        <span class="shortcut">Ctrl+Shift+T</span> new
        <span class="shortcut">Ctrl+Shift+W</span> close
        <span class="shortcut">Ctrl+1-9</span> switch
      </div>
    </div>
  </div>
</template>

<style scoped>
.multi-pane-terminal {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
}

.terminal-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--surface-variant);
  border-bottom: 1px solid var(--stroke);
  gap: 12px;
}

.tabs-container {
  display: flex;
  gap: 4px;
  flex: 1;
  overflow-x: auto;
}

.tabs-container :deep(.n-tag) {
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}

.tabs-container :deep(.n-tag.bell-flash) {
  animation: bell-flash 1s ease-in-out;
}

.connection-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  margin-left: 4px;
}

.terminal-panes {
  flex: 1;
  display: grid;
  gap: 1px;
  background: var(--stroke);
}

.layout-single {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.layout-horizontal {
  grid-template-columns: 1fr;
  grid-template-rows: repeat(auto-fit, 1fr);
}

.layout-vertical {
  grid-template-columns: repeat(auto-fit, 1fr);
  grid-template-rows: 1fr;
}

.layout-grid {
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(2, 1fr);
}

.terminal-pane {
  position: relative;
  background: var(--surface);
  min-height: 200px;
}

.terminal-pane:not(.active) {
  opacity: 0.8;
}

.terminal-pane.active {
  box-shadow: inset 0 0 0 2px var(--accent);
}

.pane-header {
  position: absolute;
  top: 8px;
  left: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(0, 0, 0, 0.8);
  padding: 4px 8px;
  border-radius: 4px;
  color: white;
  font-size: 12px;
  z-index: 10;
  backdrop-filter: blur(4px);
}

.pane-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.connection-status {
  font-size: 8px;
}

.connection-status.connected {
  color: var(--success);
}

.connection-status.disconnected {
  color: var(--error);
}

.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: var(--surface-variant);
  border-top: 1px solid var(--stroke);
  font-size: 12px;
  color: var(--muted);
}

.status-info {
  display: flex;
  gap: 12px;
}

.shortcuts-hint {
  display: flex;
  gap: 8px;
  align-items: center;
}

.shortcut {
  background: var(--surface);
  padding: 2px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 10px;
}

@keyframes bell-flash {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; background: var(--warning); }
}

/* Mobile: single active pane fills available space */
.layout-mobile {
  grid-template-columns: 1fr !important;
  grid-template-rows: 1fr !important;
}

.layout-mobile .terminal-pane {
  height: calc(var(--vh-full, 100vh) - 200px);
  min-height: 0;
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .terminal-tabs {
    padding: 6px 8px;
  }

  .tabs-container {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .tabs-container::-webkit-scrollbar {
    display: none;
  }

  .shortcuts-hint {
    display: none;
  }

  .layout-grid {
    grid-template-columns: 1fr;
    grid-template-rows: repeat(4, 1fr);
  }

  .layout-horizontal,
  .layout-vertical {
    grid-template-columns: 1fr;
    grid-template-rows: repeat(auto-fit, 1fr);
  }
}

/* Accessibility */
@media (prefers-reduced-motion: reduce) {
  .tabs-container :deep(.n-tag),
  .terminal-pane {
    transition: none;
  }
  
  @keyframes bell-flash {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
  }
}
</style>
