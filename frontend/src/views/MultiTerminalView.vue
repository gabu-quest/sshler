<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NIcon, NSpace, NModal, NSelect, NInput, useMessage } from 'naive-ui'
import { PhPlus, PhTerminalWindow, PhArrowLeft } from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import { useAppStore } from '@/stores/app'
import { useI18n } from '@/i18n'
import Terminal from '@/components/Terminal.vue'
import DirectoryPickerModal from '@/components/DirectoryPickerModal.vue'
import { getEmojiForBox } from '@/utils/emoji-favicon'

interface TerminalInstance {
  id: string
  boxName: string
  sessionName: string
  directory: string
}

const route = useRoute()
const message = useMessage()
const bootstrapStore = useBootstrapStore()
const boxesStore = useBoxesStore()
const favoritesStore = useFavoritesStore()
const appStore = useAppStore()
const { t } = useI18n()

const terminals = ref<TerminalInstance[]>([])
const showAddModal = ref(false)
const showDirPicker = ref(false)
const newTerminal = ref({
  boxName: '',
  sessionName: '',
  directory: '~'
})

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({
    label: `${getEmojiForBox(box.name)} ${box.name} (${box.host})`,
    value: box.name
  }))
)

const tokenValue = computed(() => 
  bootstrapStore.token || bootstrapStore.payload?.token || null
)

const gridCols = computed(() => {
  const count = terminals.value.length
  if (count === 0) return 1
  if (count === 1) return 1
  if (count <= 4) return 2
  if (count <= 9) return 3
  return 4
})

const terminalFontSize = computed(() => {
  const count = terminals.value.length
  if (count <= 4) return 14
  if (count <= 8) return 12
  return 11
})

// Fixed terminal height for consistent grid layout
const terminalMinHeight = computed(() => {
  const count = terminals.value.length
  if (count <= 1) return '400px'
  if (count <= 4) return '350px'
  return '300px' // Many terminals: allow scrolling
})

const terminalColors = ['#6aa6ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2', '#eb2f96', '#f5222d']

const getTerminalColor = (index: number) => {
  return terminalColors[index % terminalColors.length]
}

// T7: Directory options for dropdown - favorites from selected box only
const directoryOptions = computed(() => {
  const options: Array<{ label: string; value: string }> = []
  
  // Add favorites from selected box
  if (newTerminal.value.boxName) {
    const boxData = boxesStore.items.find(b => b.name === newTerminal.value.boxName)
    if (boxData?.favorites) {
      boxData.favorites.forEach(fav => {
        const label = fav.split('/').pop() || fav
        options.push({ label: `★ ${label}`, value: fav })
      })
    }
    // Also check favoritesStore
    const storeFavs = Array.from(favoritesStore.favoritesForBox(newTerminal.value.boxName).values())
    storeFavs.forEach(fav => {
      if (!options.some(o => o.value === fav)) {
        const label = fav.split('/').pop() || fav
        options.push({ label: `★ ${label}`, value: fav })
      }
    })
  }
  
  // Default home if no favorites
  if (options.length === 0) {
    options.push({ label: '~ (Home)', value: '~' })
  }
  
  // Add Browse option
  options.push({ label: '📁 Browse...', value: '__browse__' })
  
  return options
})

const handleDirectoryChange = (value: string) => {
  if (value === '__browse__') {
    showDirPicker.value = true
    return
  }
  newTerminal.value.directory = value
}

const handleDirPickerSelect = (path: string) => {
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

// Generate session name from directory (last component only, matching TerminalView)
const generateSessionName = (directory: string) => {
  if (!directory || directory === '~') {
    return 'home'
  }
  // Extract last path component (directory name)
  const pathParts = directory.split('/').filter(Boolean)
  const dirName = pathParts[pathParts.length - 1] || 'root'
  // Sanitize: replace non-alphanumeric with underscore
  const sanitized = dirName.replace(/[^a-zA-Z0-9]/g, '_')
  // Remove leading/trailing underscores
  return sanitized.replace(/^_+|_+$/g, '') || 'root'
}

const addTerminal = () => {
  if (!newTerminal.value.boxName) {
    message.error(t('multi.select_box'))
    return
  }

  // Generate session name based on directory for tmux window sharing
  const dirBasedSession = generateSessionName(newTerminal.value.directory || '~')
  
  const id = `term-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  terminals.value.push({
    id,
    boxName: newTerminal.value.boxName,
    sessionName: newTerminal.value.sessionName || dirBasedSession,
    directory: newTerminal.value.directory || '~'
  })
  saveLayout()

  showAddModal.value = false
  newTerminal.value = {
    boxName: newTerminal.value.boxName, // Keep same box selected
    sessionName: '', // Reset to auto-generate
    directory: '~'
  }
}

const removeTerminal = (id: string) => {
  terminals.value = terminals.value.filter(t => t.id !== id)
  saveLayout()
}

const openAddModal = () => {
  if (boxOptions.value.length > 0 && !newTerminal.value.boxName) {
    newTerminal.value.boxName = boxOptions.value[0].value
  }
  // Load favorites for selected box
  if (newTerminal.value.boxName) {
    favoritesStore.loadBox(newTerminal.value.boxName, tokenValue.value || null)
  }
  showAddModal.value = true
}

// Watch for box selection changes to load favorites
watch(() => newTerminal.value.boxName, async (boxName) => {
  if (boxName) {
    await favoritesStore.loadBox(boxName, tokenValue.value || null)
  }
})

const goBack = () => {
  window.history.back()
}

// Layout persistence
const LAYOUT_KEY = 'sshler:multi-terminal:layout'

function saveLayout() {
  if (terminals.value.length === 0) {
    localStorage.removeItem(LAYOUT_KEY)
    return
  }
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(terminals.value.map(t => ({
    boxName: t.boxName,
    sessionName: t.sessionName,
    directory: t.directory,
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
    }))
  } catch { return null }
}

onMounted(async () => {
  await ensureData()

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
    <!-- Header -->
    <div class="header">
      <div class="header-left">
        <NButton size="small" quaternary @click="goBack" :title="t('terminal.go_back')">
          <NIcon size="16"><PhArrowLeft weight="duotone" /></NIcon>
        </NButton>
        <div>
          <h1>{{ t('multi.title') }}</h1>
          <p class="text-muted">{{ terminals.length }} {{ terminals.length !== 1 ? t('multi.terminals') : t('multi.terminal') }} {{ t('multi.active') }}</p>
        </div>
      </div>
      
      <NButton type="primary" @click="openAddModal">
        <NIcon size="16"><PhPlus weight="duotone" /></NIcon>
        {{ t('multi.add_terminal') }}
      </NButton>
    </div>

    <!-- Terminal Grid -->
    <div 
      class="terminal-grid" 
      :style="{ 
        gridTemplateColumns: `repeat(${gridCols}, 1fr)`,
        '--terminal-min-height': terminalMinHeight
      }"
    >
      <div
        v-for="(terminal, index) in terminals"
        :key="terminal.id"
        class="terminal-container"
        :style="{ 
          borderColor: getTerminalColor(index),
          borderWidth: '2px'
        }"
      >
        <div 
          class="terminal-header"
          :style="{ backgroundColor: getTerminalColor(index) + '20' }"
        >
          <div class="terminal-info">
            <NIcon size="14"><PhTerminalWindow weight="duotone" /></NIcon>
            <span>{{ getEmojiForBox(terminal.boxName) }} {{ terminal.boxName }}</span>
            <span class="session-name">{{ terminal.sessionName }}</span>
            <span class="directory-name">{{ terminal.directory }}</span>
          </div>
          <NButton size="tiny" quaternary @click="removeTerminal(terminal.id)" :title="t('common.close')">
            ×
          </NButton>
        </div>
        <div class="terminal-wrapper">
          <Terminal
            :box-name="terminal.boxName"
            :session-name="terminal.sessionName"
            :directory="terminal.directory"
            :font-size="terminalFontSize"
          />
        </div>
      </div>
      
      <!-- Empty state -->
      <div v-if="terminals.length === 0" class="empty-state">
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
            :value="newTerminal.directory"
            :options="directoryOptions"
            :placeholder="t('multi.dir_placeholder')"
            @update:value="handleDirectoryChange"
          />
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
.multi-terminal-page {
  height: var(--vh-full, 100vh);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0; /* Remove default padding */
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 8px;
  border-bottom: 1px solid var(--stroke);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header h1 {
  margin: 0;
  font-size: 20px;
}

.header p {
  margin: 0;
  font-size: 12px;
}

.terminal-grid {
  flex: 1;
  display: grid;
  gap: 4px;
  padding: 4px;
  min-height: 0;
  overflow-y: auto;
  grid-auto-rows: minmax(var(--terminal-min-height, 300px), 1fr);
}

.terminal-container {
  display: flex;
  flex-direction: column;
  border: 2px solid var(--stroke);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  min-height: var(--terminal-min-height, 300px);
}

.terminal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--surface-variant);
  border-bottom: 1px solid var(--stroke);
  font-size: 12px;
  flex-shrink: 0;
}

.terminal-info {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.session-name {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.directory-name {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 10px;
  opacity: 0.7;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.terminal-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.empty-state {
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

/* Responsive grid */
@media (max-width: 1200px) {
  .terminal-grid {
    grid-template-columns: repeat(3, 1fr) !important;
  }
}

@media (max-width: 900px) {
  .terminal-grid {
    grid-template-columns: repeat(2, 1fr) !important;
  }
}

@media (max-width: 768px) {
  .terminal-grid {
    grid-template-columns: 1fr !important;
    grid-auto-rows: minmax(250px, 1fr);
  }

  .header {
    flex-direction: column;
    gap: 8px;
    align-items: stretch;
  }

  .header-left {
    justify-content: center;
  }

  .terminal-container {
    min-height: 250px;
  }

  .directory-name {
    display: none;
  }
}

/* Ensure terminals fit properly */
.terminal-wrapper :deep(.terminal-container) {
  height: 100%;
}

.terminal-wrapper :deep(.xterm) {
  height: 100%;
}
</style>
