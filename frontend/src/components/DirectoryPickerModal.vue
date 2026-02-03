<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NModal, NButton, NIcon, NSpace, NSpin, NEmpty, NInput } from 'naive-ui'
import { PhFolder, PhFolderOpen, PhStar, PhArrowLeft, PhHouse, PhCheck } from '@phosphor-icons/vue'

import { http, buildHeaders } from '@/api/http'
import { useFavoritesStore } from '@/stores/favorites'
import type { DirectoryEntry } from '@/api/types'

const props = defineProps<{
  show: boolean
  boxName: string
  initialPath?: string
  token: string | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'select', path: string): void
}>()

const favoritesStore = useFavoritesStore()

const currentPath = ref(props.initialPath || '~')
const entries = ref<DirectoryEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const manualPath = ref('')
const showManualInput = ref(false)

const directories = computed(() => 
  entries.value.filter(e => e.is_directory).sort((a, b) => a.name.localeCompare(b.name))
)

const favorites = computed(() => 
  Array.from(favoritesStore.favoritesForBox(props.boxName).values())
)

const isFavorite = (path: string) => favoritesStore.isFavorite(props.boxName, path)

const loadDirectory = async (path: string) => {
  loading.value = true
  error.value = null

  try {
    const url = `/api/v1/boxes/${encodeURIComponent(props.boxName)}/ls?directory=${encodeURIComponent(path)}`
    const response = await http.get<{ entries: DirectoryEntry[], directory: string }>(url, {
      headers: buildHeaders(props.token)
    })
    entries.value = response.data.entries
    currentPath.value = response.data.directory // Use resolved path from server
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Failed to load directory'
    entries.value = []
  } finally {
    loading.value = false
  }
}

const navigateTo = (path: string) => {
  loadDirectory(path)
}

const navigateUp = () => {
  if (currentPath.value === '/' || currentPath.value === '~') return
  const parent = currentPath.value.split('/').slice(0, -1).join('/') || '/'
  loadDirectory(parent)
}

const navigateHome = () => {
  loadDirectory('~')
}

const selectCurrent = () => {
  emit('select', currentPath.value)
  emit('update:show', false)
}

const selectPath = (path: string) => {
  emit('select', path)
  emit('update:show', false)
}

const toggleFavorite = async (path: string) => {
  await favoritesStore.toggle(props.boxName, path, props.token)
}

const handleManualSubmit = () => {
  if (manualPath.value.trim()) {
    loadDirectory(manualPath.value.trim())
    showManualInput.value = false
    manualPath.value = ''
  }
}

// Load initial directory when modal opens
watch(() => props.show, (show) => {
  if (show) {
    loadDirectory(props.initialPath || '~')
  }
})
</script>

<template>
  <NModal 
    :show="show" 
    @update:show="emit('update:show', $event)"
    preset="card"
    style="width: 500px; max-width: 90vw;"
    title="Choose Directory"
    :bordered="false"
  >
    <div class="directory-picker">
      <!-- Navigation bar -->
      <div class="nav-bar">
        <NSpace>
          <NButton size="small" quaternary @click="navigateUp" :disabled="currentPath === '/' || currentPath === '~'">
            <NIcon><PhArrowLeft /></NIcon>
          </NButton>
          <NButton size="small" quaternary @click="navigateHome">
            <NIcon><PhHouse /></NIcon>
          </NButton>
        </NSpace>
        
        <div class="current-path" @click="showManualInput = true">
          <template v-if="showManualInput">
            <NInput 
              v-model:value="manualPath" 
              size="small"
              :placeholder="currentPath"
              @keyup.enter="handleManualSubmit"
              @blur="showManualInput = false"
              autofocus
            />
          </template>
          <template v-else>
            {{ currentPath }}
          </template>
        </div>
        
        <NButton 
          size="small" 
          :type="isFavorite(currentPath) ? 'warning' : 'default'"
          quaternary
          @click="toggleFavorite(currentPath)"
          title="Toggle favorite"
        >
          <NIcon :color="isFavorite(currentPath) ? '#faad14' : undefined">
            <PhStar :weight="isFavorite(currentPath) ? 'fill' : 'regular'" />
          </NIcon>
        </NButton>
      </div>
      
      <!-- Favorites section -->
      <div v-if="favorites.length > 0" class="favorites-section">
        <div class="section-label">Favorites</div>
        <div class="favorites-list">
          <div 
            v-for="fav in favorites" 
            :key="fav" 
            class="favorite-item"
            @click="selectPath(fav)"
          >
            <NIcon color="#faad14"><PhStar weight="fill" /></NIcon>
            <span class="fav-path">{{ fav }}</span>
          </div>
        </div>
      </div>
      
      <!-- Directory list -->
      <div class="directory-list">
        <div v-if="loading" class="loading-state">
          <NSpin size="medium" />
        </div>
        
        <NEmpty v-else-if="error" :description="error" />
        
        <NEmpty v-else-if="directories.length === 0" description="No subdirectories" />
        
        <template v-else>
          <div 
            v-for="dir in directories" 
            :key="dir.path" 
            class="directory-item"
            @click="navigateTo(dir.path)"
            @dblclick="selectPath(dir.path)"
          >
            <NIcon class="folder-icon">
              <PhFolderOpen v-if="isFavorite(dir.path)" />
              <PhFolder v-else />
            </NIcon>
            <span class="dir-name">{{ dir.name }}</span>
            <NButton 
              size="tiny" 
              quaternary 
              class="star-btn"
              @click.stop="toggleFavorite(dir.path)"
            >
              <NIcon :color="isFavorite(dir.path) ? '#faad14' : 'var(--muted)'">
                <PhStar :weight="isFavorite(dir.path) ? 'fill' : 'regular'" />
              </NIcon>
            </NButton>
          </div>
        </template>
      </div>
      
      <!-- Footer actions -->
      <div class="footer-actions">
        <NButton @click="emit('update:show', false)">Cancel</NButton>
        <NButton type="primary" @click="selectCurrent">
          <template #icon>
            <NIcon><PhCheck /></NIcon>
          </template>
          Select "{{ currentPath }}"
        </NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.directory-picker {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.nav-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: var(--surface);
  border-radius: 6px;
}

.current-path {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 4px 8px;
  background: var(--bg);
  border-radius: 4px;
  cursor: text;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.favorites-section {
  padding: 8px;
  background: rgba(250, 173, 20, 0.1);
  border-radius: 6px;
  border: 1px solid rgba(250, 173, 20, 0.2);
}

.section-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--muted);
  margin-bottom: 8px;
}

.favorites-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.favorite-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.favorite-item:hover {
  background: rgba(250, 173, 20, 0.15);
}

.fav-path {
  font-family: var(--font-mono);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.directory-list {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--stroke);
  border-radius: 6px;
}

.loading-state {
  display: flex;
  justify-content: center;
  padding: 32px;
}

.directory-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: background 0.15s ease;
  border-bottom: 1px solid var(--stroke);
}

.directory-item:last-child {
  border-bottom: none;
}

.directory-item:hover {
  background: var(--surface);
}

.folder-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.dir-name {
  flex: 1;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.star-btn {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.directory-item:hover .star-btn {
  opacity: 1;
}

/* Touch devices: always show star button since there is no hover */
@media (hover: none) and (pointer: coarse) {
  .star-btn {
    opacity: 1;
  }

  .directory-item {
    padding: 14px 12px;
    min-height: 48px;
  }

  .favorite-item {
    padding: 10px 8px;
    min-height: 48px;
  }
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--stroke);
}
</style>
