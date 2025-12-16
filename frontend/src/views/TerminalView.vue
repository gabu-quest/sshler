<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NCard, NSelect, NButton, NIcon, NAlert, useMessage } from 'naive-ui'
import { PhTerminalWindow, PhArrowLeft } from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import Terminal from '@/components/Terminal.vue'

const route = useRoute()
const message = useMessage()

const bootstrapStore = useBootstrapStore()
const boxesStore = useBoxesStore()

const selectedBox = ref<string | null>(null)
const initialDirectory = ref<string>('~')

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({ 
    label: `${box.name} (${box.host})`, 
    value: box.name 
  }))
)

const tokenValue = computed(() => 
  bootstrapStore.token || bootstrapStore.payload?.token || null
)

const selectedBoxData = computed(() => 
  boxesStore.items.find(box => box.name === selectedBox.value)
)

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
}

const handleBoxChange = (boxName: string) => {
  selectedBox.value = boxName
  
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
          <NSelect
            v-model:value="selectedBox"
            :options="boxOptions"
            placeholder="Choose box"
            :disabled="boxesStore.loading"
            @update:value="handleBoxChange"
            style="min-width: 200px"
          />
        </div>
      </div>
    </header>

    <!-- Connection Status -->
    <NAlert 
      v-if="selectedBoxData" 
      type="info" 
      :title="`Connected to ${selectedBoxData.name}`"
      closable
    >
      <template #icon>
        <NIcon size="16"><PhTerminalWindow /></NIcon>
      </template>
      Host: {{ selectedBoxData.host }}
      <span v-if="selectedBoxData.user"> • User: {{ selectedBoxData.user }}</span>
      <span v-if="selectedBoxData.port && selectedBoxData.port !== 22"> • Port: {{ selectedBoxData.port }}</span>
    </NAlert>

    <!-- Terminal Container -->
    <div class="terminal-container">
      <Terminal
        v-if="selectedBox"
        :box-name="selectedBox"
        :session-name="'main'"
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
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: calc(100vh - 96px);
  overflow: hidden;
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
