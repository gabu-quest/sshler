<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSelect, NButton, NIcon, NInput, NSpace, NButtonGroup, NDivider } from 'naive-ui'
import { PhTerminalWindow, PhArrowLeft, PhStar, PhCaretLeft, PhCaretRight, PhPlusCircle, PhFolderOpen, PhClipboard } from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import Terminal from '@/components/Terminal.vue'
import DirectoryPickerModal from '@/components/DirectoryPickerModal.vue'

const route = useRoute()
const router = useRouter()

const bootstrapStore = useBootstrapStore()
const boxesStore = useBoxesStore()
const favoritesStore = useFavoritesStore()

const selectedBox = ref<string | null>(null)
const initialDirectory = ref<string>('~')
const sessionName = ref<string>('main')
const showManualDir = ref(false)
const showDirPicker = ref(false)
const terminalRef = ref<InstanceType<typeof Terminal> | null>(null)

// Tmux control sequences (Ctrl+B prefix)
const TMUX_PREFIX = '\x02' // Ctrl+B
const sendTmuxCommand = (key: string) => {
  terminalRef.value?.send(TMUX_PREFIX + key)
  terminalRef.value?.focus()
}

const tmuxPrevWindow = () => sendTmuxCommand('p')
const tmuxNextWindow = () => sendTmuxCommand('n')
const tmuxNewWindow = () => sendTmuxCommand('c')

const goToFiles = () => {
  if (selectedBox.value) {
    // Open in new tab at the terminal's current directory
    const path = initialDirectory.value || '~'
    window.open(`/app/files?box=${encodeURIComponent(selectedBox.value)}&path=${encodeURIComponent(path)}`, '_blank')
  }
}

const pasteFromClipboard = async () => {
  try {
    const text = await navigator.clipboard.readText()
    if (text) {
      terminalRef.value?.send(text)
      terminalRef.value?.focus()
    }
  } catch (err) {
    console.error('Failed to paste:', err)
  }
}

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({ 
    label: `${box.name} (${box.host})`, 
    value: box.name 
  }))
)

const tokenValue = computed(() => 
  bootstrapStore.token || bootstrapStore.payload?.token || null
)

const isCurrentDirFavorite = computed(() => 
  selectedBox.value ? favoritesStore.isFavorite(selectedBox.value, initialDirectory.value) : false
)

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
      const label = fav.split('/').pop() || fav
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
  sessionName.value = generateSessionName(value)
}

const handleDirPickerSelect = (path: string) => {
  initialDirectory.value = path
  sessionName.value = generateSessionName(path)
}

// Generate directory-based session name from the directory path
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
  
  // Set session name based on directory
  sessionName.value = generateSessionName(initialDirectory.value)
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

onMounted(async () => {
  await ensureData()
  initializeFromRoute()
})

watch(() => boxesStore.items, () => {
  if (boxesStore.items.length > 0 && !selectedBox.value) {
    initializeFromRoute()
  }
}, { immediate: true })
</script>

<template>
  <div class="page">
    <!-- Header -->
    <header class="page-header">
      <div class="header-content">
        <div class="header-info">
          <NButton size="small" quaternary @click="goBack" title="Go Back">
            <NIcon size="16"><PhArrowLeft /></NIcon>
          </NButton>
          <div>
            <p class="eyebrow">terminal</p>
            <h1>Single Terminal</h1>
            <p class="text-muted">Full-screen terminal session</p>
          </div>
        </div>
        
        <div class="header-actions">
          <NSpace align="center">
            <NSelect
              v-model:value="selectedBox"
              :options="boxOptions"
              placeholder="Choose box"
              :disabled="boxesStore.loading"
              @update:value="handleBoxChange"
              style="min-width: 180px"
            />
            <NSelect
              v-if="selectedBox && !showManualDir"
              :value="initialDirectory"
              :options="directoryOptions"
              placeholder="Directory"
              @update:value="handleDirectoryChange"
              style="min-width: 180px"
            />
            <NInput
              v-if="showManualDir"
              v-model:value="initialDirectory"
              placeholder="/path/to/dir"
              @blur="sessionName = generateSessionName(initialDirectory)"
              @keyup.enter="sessionName = generateSessionName(initialDirectory)"
              style="min-width: 200px"
            />
            <NButton v-if="showManualDir" size="small" @click="showManualDir = false">
              Cancel
            </NButton>
            
            <!-- Tmux Window Controls -->
            <template v-if="selectedBox">
              <NDivider vertical />
              <NButtonGroup size="small">
                <NButton 
                  class="tmux-nav-btn tmux-prev"
                  @click="tmuxPrevWindow" 
                  title="Previous Window (Ctrl+B p)"
                >
                  <NIcon size="14"><PhCaretLeft weight="bold" /></NIcon>
                </NButton>
                <NButton 
                  class="tmux-nav-btn tmux-next"
                  @click="tmuxNextWindow" 
                  title="Next Window (Ctrl+B n)"
                >
                  <NIcon size="14"><PhCaretRight weight="bold" /></NIcon>
                </NButton>
              </NButtonGroup>
              
              <NButton 
                size="small"
                class="tmux-new-btn"
                @click="tmuxNewWindow" 
                title="New Window (Ctrl+B c)"
              >
                <template #icon>
                  <NIcon size="14"><PhPlusCircle weight="fill" /></NIcon>
                </template>
                New
              </NButton>
              
              <NDivider vertical />
              
              <NButton 
                size="small"
                @click="goToFiles"
                title="Browse Files (opens in new tab)"
              >
                <template #icon>
                  <NIcon size="14"><PhFolderOpen /></NIcon>
                </template>
                Files
              </NButton>
              
              <NButton 
                size="small"
                @click="pasteFromClipboard"
                title="Paste from clipboard"
              >
                <template #icon>
                  <NIcon size="14"><PhClipboard /></NIcon>
                </template>
                Paste
              </NButton>
              
              <NButton 
                size="small"
                :type="isCurrentDirFavorite ? 'warning' : 'default'"
                @click="toggleCurrentDirFavorite"
                :title="isCurrentDirFavorite ? 'Remove from favorites' : 'Add to favorites'"
                class="favorite-btn"
              >
                <template #icon>
                  <NIcon size="14" :color="isCurrentDirFavorite ? '#faad14' : undefined">
                    <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'regular'" />
                  </NIcon>
                </template>
              </NButton>
            </template>
          </NSpace>
        </div>
      </div>
    </header>

    <!-- Terminal Container -->
    <div class="terminal-container">
      <Terminal
        v-if="selectedBox"
        ref="terminalRef"
        :key="selectedBox + '-' + initialDirectory"
        :box-name="selectedBox"
        :session-name="sessionName"
        :directory="initialDirectory"
      />
      
      <div v-else class="no-box-selected">
        <div class="empty-state">
          <NIcon size="48" class="empty-icon"><PhTerminalWindow /></NIcon>
          <h3>No Box Selected</h3>
          <p class="text-muted">Choose a box from the dropdown above to start a terminal session.</p>
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
    
    <!-- Directory Picker Modal -->
    <DirectoryPickerModal
      v-if="selectedBox"
      v-model:show="showDirPicker"
      :box-name="selectedBox"
      :initial-path="initialDirectory"
      :token="tokenValue"
      @select="handleDirPickerSelect"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: calc(100vh - 96px);
  overflow: hidden;
  width: 100%;
  max-width: none;  /* Override any parent constraints */
  min-width: 0;     /* Allow flex shrinking */
  padding: 0 16px;
  box-sizing: border-box;
}

.page-header {
  flex-shrink: 0;
}

.header-content {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.header-info {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-size: 12px;
  margin: 0 0 4px 0;
  color: var(--muted);
}

h1 {
  margin: 0 0 8px 0;
  font-size: 26px;
}

.text-muted {
  color: var(--muted);
  font-size: 14px;
  margin: 0;
}

.terminal-container {
  flex: 1;
  min-height: 0;
  min-width: 0;  /* Critical for flex layouts - allows shrinking */
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
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

@media (max-width: 768px) {
  .page {
    gap: 12px;
  }
  
  .header-content {
    flex-direction: column;
    align-items: stretch;
  }
  
  .header-info {
    flex-direction: column;
    gap: 8px;
  }
}

.terminal-container :deep(.terminal-container) {
  height: 100%;
}

/* Tmux control buttons - colorful and cute */
.tmux-nav-btn {
  transition: all 0.15s ease;
}

.tmux-prev {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  border-color: #667eea !important;
  color: white !important;
}

.tmux-prev:hover {
  background: linear-gradient(135deg, #5a67d8 0%, #6b46c1 100%) !important;
  transform: translateX(-1px);
}

.tmux-next {
  background: linear-gradient(135deg, #764ba2 0%, #667eea 100%) !important;
  border-color: #764ba2 !important;
  color: white !important;
}

.tmux-next:hover {
  background: linear-gradient(135deg, #6b46c1 0%, #5a67d8 100%) !important;
  transform: translateX(1px);
}

.tmux-new-btn {
  background: linear-gradient(135deg, #48bb78 0%, #38a169 100%) !important;
  border-color: #48bb78 !important;
  color: white !important;
  margin-left: 8px;
}

.tmux-new-btn:hover {
  background: linear-gradient(135deg, #38a169 0%, #2f855a 100%) !important;
  transform: scale(1.02);
}

.favorite-btn {
  transition: transform 0.15s ease;
}

.favorite-btn:hover {
  transform: scale(1.1);
}

@media (prefers-contrast: high) {
  .no-box-selected {
    border: 2px solid var(--stroke);
  }
}

.terminal-container:focus-within {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
  border-radius: 8px;
}
</style>
