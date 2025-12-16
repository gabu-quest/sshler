<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NIcon, NSpace, NModal, NSelect, NInput, useMessage } from 'naive-ui'
import { PhPlus, PhTerminalWindow, PhArrowLeft } from '@phosphor-icons/vue'

import { useBootstrapStore } from '@/stores/bootstrap'
import { useBoxesStore } from '@/stores/boxes'
import Terminal from '@/components/Terminal.vue'

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

const terminals = ref<TerminalInstance[]>([])
const showAddModal = ref(false)
const newTerminal = ref({
  boxName: '',
  sessionName: 'main',
  directory: '~'
})

const boxOptions = computed(() =>
  boxesStore.items.map((box) => ({ 
    label: `${box.name} (${box.host})`, 
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
  if (count <= 8) return 13
  return 12
})

const terminalMinHeight = computed(() => {
  const count = terminals.value.length
  if (count <= 2) return '400px'
  if (count <= 6) return '300px'
  return '250px'
})

const terminalColors = ['#6aa6ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2', '#eb2f96', '#f5222d']

const getTerminalColor = (index: number) => {
  return terminalColors[index % terminalColors.length]
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
    message.error('Please select a box')
    return
  }
  
  // Generate session name based on directory for tmux window sharing
  const dirBasedSession = newTerminal.value.directory 
    ? newTerminal.value.directory.replace(/[^a-zA-Z0-9]/g, '_').replace(/^_+|_+$/g, '') || 'root'
    : 'home'
  
  const id = `term-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  terminals.value.push({
    id,
    boxName: newTerminal.value.boxName,
    sessionName: newTerminal.value.sessionName || dirBasedSession,
    directory: newTerminal.value.directory || '~'
  })
  
  showAddModal.value = false
  newTerminal.value = {
    boxName: newTerminal.value.boxName, // Keep same box selected
    sessionName: '', // Reset to auto-generate
    directory: '~'
  }
}

const removeTerminal = (id: string) => {
  terminals.value = terminals.value.filter(t => t.id !== id)
}

const openAddModal = () => {
  if (boxOptions.value.length > 0 && !newTerminal.value.boxName) {
    newTerminal.value.boxName = boxOptions.value[0].value
  }
  showAddModal.value = true
}

const goBack = () => {
  window.history.back()
}

onMounted(async () => {
  await ensureData()
  
  // Initialize from route
  const boxFromRoute = route.query.box as string
  if (boxFromRoute && boxOptions.value.some(opt => opt.value === boxFromRoute)) {
    newTerminal.value.boxName = boxFromRoute
  }
})
</script>

<template>
  <div class="multi-terminal-page">
    <!-- Header -->
    <div class="header">
      <div class="header-left">
        <NButton size="small" quaternary @click="goBack" title="Go Back">
          <NIcon size="16"><PhArrowLeft /></NIcon>
        </NButton>
        <div>
          <h1>Multi-Terminal Grid</h1>
          <p class="text-muted">{{ terminals.length }} terminal{{ terminals.length !== 1 ? 's' : '' }} active</p>
        </div>
      </div>
      
      <NButton type="primary" @click="openAddModal">
        <NIcon size="16"><PhPlus /></NIcon>
        Add Terminal
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
            <NIcon size="14"><PhTerminalWindow /></NIcon>
            <span>{{ terminal.boxName }}</span>
            <span class="session-name">{{ terminal.sessionName }}</span>
            <span class="directory-name">{{ terminal.directory }}</span>
          </div>
          <NButton size="tiny" quaternary @click="removeTerminal(terminal.id)" title="Close">
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
        <NIcon size="48" class="empty-icon"><PhTerminalWindow /></NIcon>
        <h3>No Terminals</h3>
        <p class="text-muted">Click "Add Terminal" to start</p>
      </div>
    </div>

    <!-- Add Terminal Modal -->
    <NModal v-model:show="showAddModal" preset="card" title="Add Terminal" style="max-width: 400px">
      <NSpace vertical size="medium">
        <div>
          <label class="form-label">Box</label>
          <NSelect
            v-model:value="newTerminal.boxName"
            :options="boxOptions"
            placeholder="Choose box"
          />
        </div>
        
        <div>
          <label class="form-label">Session Name</label>
          <NInput
            v-model:value="newTerminal.sessionName"
            placeholder="Auto-generated from directory"
          />
          <p class="form-help">Leave empty to auto-generate from directory (same dir = same tmux window)</p>
        </div>
        
        <div>
          <label class="form-label">Directory</label>
          <NInput
            v-model:value="newTerminal.directory"
            placeholder="~"
          />
        </div>
      </NSpace>
      
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showAddModal = false">Cancel</NButton>
          <NButton type="primary" @click="addTerminal">Add Terminal</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.multi-terminal-page {
  height: 100vh;
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
  gap: 4px; /* Slightly larger gap for colored borders */
  padding: 4px;
  min-height: 0;
  overflow-y: auto; /* Allow vertical scrolling */
  grid-auto-rows: var(--terminal-min-height, 300px); /* Consistent row height */
}

.terminal-container {
  display: flex;
  flex-direction: column;
  border: 2px solid var(--stroke);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  height: var(--terminal-min-height, 300px); /* Fixed height for consistency */
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

@media (max-width: 600px) {
  .terminal-grid {
    grid-template-columns: 1fr !important;
  }
  
  .header {
    flex-direction: column;
    gap: 8px;
    align-items: stretch;
  }
  
  .header-left {
    justify-content: center;
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
