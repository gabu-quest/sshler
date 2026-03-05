<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NDrawer, NDrawerContent, NButton, NIcon, NInput, NEmpty,
  NSpace, NTag, NPopconfirm, useMessage,
} from 'naive-ui'
import { PhTrash, PhPencilSimple, PhPlay, PhCopy, PhPlus } from '@phosphor-icons/vue'
import { useSnippetsStore } from '@/stores/snippets'
import { useBootstrapStore } from '@/stores/bootstrap'
import type { ApiSnippet } from '@/api/types'

interface Props {
  show: boolean
  boxName: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'insert', command: string, execute: boolean): void
}>()

const message = useMessage()
const snippetsStore = useSnippetsStore()
const bootstrapStore = useBootstrapStore()

const token = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null)

const filterText = ref('')
const showAddForm = ref(false)
const editingId = ref<string | null>(null)

// Add form state
const newLabel = ref('')
const newCommand = ref('')
const newCategory = ref('')

// Edit form state
const editLabel = ref('')
const editCommand = ref('')
const editCategory = ref('')

watch(() => props.show, (visible) => {
  if (visible && snippetsStore.loadedBox !== props.boxName) {
    snippetsStore.load(props.boxName, token.value)
  }
})

const filteredSnippets = computed(() => {
  const q = filterText.value.toLowerCase()
  if (!q) return snippetsStore.items
  return snippetsStore.items.filter(
    s => s.label.toLowerCase().includes(q)
      || s.command.toLowerCase().includes(q)
      || s.category.toLowerCase().includes(q)
  )
})

const groupedSnippets = computed(() => {
  const groups: Record<string, ApiSnippet[]> = {}
  for (const s of filteredSnippets.value) {
    const cat = s.category || 'Uncategorized'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(s)
  }
  return groups
})

const close = () => emit('update:show', false)

const handleInsert = (command: string, execute: boolean) => {
  emit('insert', command, execute)
  close()
}

const resetAddForm = () => {
  newLabel.value = ''
  newCommand.value = ''
  newCategory.value = ''
  showAddForm.value = false
}

const handleAdd = async () => {
  if (!newLabel.value.trim() || !newCommand.value.trim()) return
  const result = await snippetsStore.create(
    props.boxName,
    newLabel.value.trim(),
    newCommand.value,
    newCategory.value.trim(),
    token.value,
  )
  if (result) {
    message.success('Snippet created')
    resetAddForm()
  } else {
    message.error(snippetsStore.error || 'Failed to create snippet')
  }
}

const startEdit = (snippet: ApiSnippet) => {
  editingId.value = snippet.id
  editLabel.value = snippet.label
  editCommand.value = snippet.command
  editCategory.value = snippet.category
}

const cancelEdit = () => {
  editingId.value = null
}

const handleSaveEdit = async () => {
  if (!editingId.value) return
  const result = await snippetsStore.update(
    editingId.value,
    { label: editLabel.value, command: editCommand.value, category: editCategory.value },
    token.value,
  )
  if (result) {
    message.success('Snippet updated')
    editingId.value = null
  } else {
    message.error(snippetsStore.error || 'Failed to update snippet')
  }
}

const handleDelete = async (id: string) => {
  const ok = await snippetsStore.remove(id, token.value)
  if (ok) {
    message.success('Snippet deleted')
    if (editingId.value === id) editingId.value = null
  }
}
</script>

<template>
  <NDrawer :show="props.show" :width="380" placement="right" @update:show="emit('update:show', $event)">
    <NDrawerContent title="Snippets" closable>
      <template #header-extra>
        <NButton size="small" type="primary" @click="showAddForm = !showAddForm">
          <NIcon size="14"><PhPlus weight="bold" /></NIcon>
        </NButton>
      </template>

      <!-- Add form -->
      <div v-if="showAddForm" class="snippet-form">
        <NInput v-model:value="newLabel" placeholder="Label" size="small" />
        <NInput
          v-model:value="newCommand"
          type="textarea"
          placeholder="Command"
          size="small"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
        <NInput v-model:value="newCategory" placeholder="Category (optional)" size="small" />
        <NSpace size="small">
          <NButton size="small" type="primary" @click="handleAdd" :disabled="!newLabel.trim() || !newCommand.trim()">
            Add
          </NButton>
          <NButton size="small" @click="resetAddForm">Cancel</NButton>
        </NSpace>
      </div>

      <!-- Search -->
      <NInput
        v-model:value="filterText"
        placeholder="Filter snippets..."
        size="small"
        clearable
        class="snippet-filter"
      />

      <!-- Snippets list -->
      <div v-if="filteredSnippets.length === 0" class="snippet-empty">
        <NEmpty :description="snippetsStore.items.length === 0 ? 'No snippets yet' : 'No matches'" />
      </div>

      <div v-for="(snippets, category) in groupedSnippets" :key="category" class="snippet-group">
        <div class="snippet-category">{{ category }}</div>
        <div
          v-for="snippet in snippets"
          :key="snippet.id"
          class="snippet-item"
        >
          <!-- Edit mode -->
          <div v-if="editingId === snippet.id" class="snippet-form">
            <NInput v-model:value="editLabel" placeholder="Label" size="small" />
            <NInput
              v-model:value="editCommand"
              type="textarea"
              placeholder="Command"
              size="small"
              :autosize="{ minRows: 2, maxRows: 5 }"
            />
            <NInput v-model:value="editCategory" placeholder="Category" size="small" />
            <NSpace size="small">
              <NButton size="small" type="primary" @click="handleSaveEdit">Save</NButton>
              <NButton size="small" @click="cancelEdit">Cancel</NButton>
            </NSpace>
          </div>

          <!-- Display mode -->
          <template v-else>
            <div class="snippet-header">
              <span class="snippet-label">{{ snippet.label }}</span>
              <NTag v-if="snippet.box === '__global__'" size="tiny" type="info">global</NTag>
            </div>
            <pre class="snippet-command">{{ snippet.command }}</pre>
            <div class="snippet-actions">
              <NButton size="tiny" quaternary @click="handleInsert(snippet.command, true)" title="Execute">
                <NIcon size="14"><PhPlay weight="duotone" /></NIcon>
              </NButton>
              <NButton size="tiny" quaternary @click="handleInsert(snippet.command, false)" title="Insert without executing">
                <NIcon size="14"><PhCopy weight="duotone" /></NIcon>
              </NButton>
              <NButton size="tiny" quaternary @click="startEdit(snippet)" title="Edit">
                <NIcon size="14"><PhPencilSimple weight="duotone" /></NIcon>
              </NButton>
              <NPopconfirm @positive-click="handleDelete(snippet.id)">
                <template #trigger>
                  <NButton size="tiny" quaternary title="Delete">
                    <NIcon size="14"><PhTrash weight="duotone" /></NIcon>
                  </NButton>
                </template>
                Delete this snippet?
              </NPopconfirm>
            </div>
          </template>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.snippet-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: var(--surface-variant);
  border-radius: 6px;
  margin-bottom: 12px;
}

.snippet-filter {
  margin-bottom: 12px;
}

.snippet-empty {
  padding: 24px 0;
}

.snippet-group {
  margin-bottom: 16px;
}

.snippet-category {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  padding: 4px 0;
  margin-bottom: 4px;
}

.snippet-item {
  padding: 8px 12px;
  border-radius: 6px;
  border: 1px solid var(--stroke);
  margin-bottom: 6px;
  transition: border-color 0.15s ease;
}

.snippet-item:hover {
  border-color: var(--accent);
}

.snippet-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.snippet-label {
  font-weight: 500;
  font-size: 13px;
}

.snippet-command {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--surface-variant);
  padding: 6px 8px;
  border-radius: 4px;
  margin: 4px 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow: auto;
}

.snippet-actions {
  display: flex;
  gap: 2px;
  justify-content: flex-end;
}
</style>
