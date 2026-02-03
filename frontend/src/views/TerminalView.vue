<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSelect, NButton, NIcon, NInput } from 'naive-ui'
import { PhTerminalWindow, PhArrowLeft, PhStar, PhFolderOpen } from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import { useFavoritesStore } from '@/stores/favorites'
import Terminal from '@/components/Terminal.vue'
import DirectoryPickerModal from '@/components/DirectoryPickerModal.vue'
import { setEmojiFavicon, resetFavicon } from '@/utils/emoji-favicon'

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

const filesUrl = computed(() => {
  if (!selectedBox.value) return '#'
  const path = initialDirectory.value || '~'
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

// Display name for the directory (last component)
const displayDirName = computed(() => {
  if (!initialDirectory.value || initialDirectory.value === '~') {
    return 'Home'
  }
  const parts = initialDirectory.value.split('/').filter(Boolean)
  return parts[parts.length - 1] || 'Root'
})

// Update browser tab title and favicon
watch([selectedBox, initialDirectory], () => {
  if (selectedBox.value) {
    document.title = `${displayDirName.value} — ${selectedBox.value}`
    // Set deterministic emoji favicon based on box + directory
    setEmojiFavicon(`${selectedBox.value}:${initialDirectory.value}`)
  } else {
    document.title = 'Terminal'
    resetFavicon()
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
    <!-- Compact Header - Directory Name Prominent -->
    <header class="page-header">
      <div class="header-content">
        <div class="header-left">
          <NButton size="small" quaternary @click="goBack" title="Go Back">
            <NIcon size="16"><PhArrowLeft /></NIcon>
          </NButton>
          <h1 class="dir-title">{{ displayDirName }}</h1>
          <span class="box-badge">{{ selectedBox || 'No box' }}</span>
        </div>

        <div class="header-controls">
          <NSelect
            v-model:value="selectedBox"
            :options="boxOptions"
            placeholder="Box"
            :disabled="boxesStore.loading"
            @update:value="handleBoxChange"
            size="small"
            style="min-width: 140px"
          />
          <NSelect
            v-if="selectedBox && !showManualDir"
            :value="initialDirectory"
            :options="directoryOptions"
            placeholder="Dir"
            @update:value="handleDirectoryChange"
            size="small"
            style="min-width: 140px"
          />
          <NInput
            v-if="showManualDir"
            v-model:value="initialDirectory"
            placeholder="/path/to/dir"
            size="small"
            @blur="sessionName = generateSessionName(initialDirectory)"
            @keyup.enter="sessionName = generateSessionName(initialDirectory)"
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
            :title="isCurrentDirFavorite ? 'Remove from favorites' : 'Add to favorites'"
            class="favorite-btn"
          >
            <NIcon size="14" :color="isCurrentDirFavorite ? '#faad14' : undefined">
              <PhStar :weight="isCurrentDirFavorite ? 'fill' : 'regular'" />
            </NIcon>
          </NButton>

          <a
            v-if="selectedBox"
            :href="filesUrl"
            class="header-link-btn"
            title="Browse Files (middle-click for new tab)"
            @click.prevent="goToFiles"
          >
            <NIcon size="14"><PhFolderOpen /></NIcon>
          </a>
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
  gap: 12px;
  height: calc(100vh - 96px);
  overflow: hidden;
  width: 100%;
  max-width: none;
  min-width: 0;
  padding: 0 16px;
  box-sizing: border-box;
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

.box-badge {
  font-size: 12px;
  padding: 2px 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
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
    gap: 8px;
  }

  .header-content {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }

  .header-left {
    justify-content: flex-start;
  }

  .dir-title {
    font-size: 18px;
  }

  .header-controls {
    flex-wrap: wrap;
  }
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
  background: rgba(255, 255, 255, 0.1);
  border-color: var(--accent);
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
