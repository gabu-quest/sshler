<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NDrawer, NDrawerContent, NButton, NIcon, NInput, NEmpty,
  NSpace, NTag, NPopconfirm, NSpin, useMessage,
} from 'naive-ui'
import { PhTrash, PhPencilSimple, PhPlay, PhCopy, PhPlus } from '@phosphor-icons/vue'
import { useSnippetsStore } from '@/stores/snippets'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useI18n } from '@/i18n'
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
const { t } = useI18n()

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
    const cat = s.category || t('snippets.uncategorized')
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
    message.success(t('snippets.created'))
    resetAddForm()
  } else {
    message.error(snippetsStore.error || t('snippets.create_failed'))
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

const editSaving = ref(false)

const handleSaveEdit = async () => {
  if (!editingId.value) return
  editSaving.value = true
  const result = await snippetsStore.update(
    editingId.value,
    { label: editLabel.value, command: editCommand.value, category: editCategory.value },
    token.value,
  )
  editSaving.value = false
  if (result) {
    message.success(t('snippets.updated'))
    editingId.value = null
  } else {
    message.error(snippetsStore.error || t('snippets.update_failed'))
  }
}

const handleDelete = async (id: string) => {
  const ok = await snippetsStore.remove(id, token.value)
  if (ok) {
    message.success(t('snippets.deleted'))
    if (editingId.value === id) editingId.value = null
  } else {
    message.error(snippetsStore.error || t('snippets.delete_failed'))
  }
}
</script>

<template>
  <NDrawer :show="props.show" width="min(380px, calc(100vw - 16px))" placement="right" @update:show="emit('update:show', $event)">
    <NDrawerContent :title="t('snippets.title')" closable>
      <template #header-extra>
        <NButton size="small" type="primary" :aria-label="t('snippets.add')" @click="showAddForm = !showAddForm">
          <NIcon size="14"><PhPlus weight="bold" /></NIcon>
        </NButton>
      </template>

      <!-- Add form -->
      <div v-if="showAddForm" class="snippet-form" @keydown.esc.stop="resetAddForm">
        <NInput v-model:value="newLabel" :placeholder="t('snippets.label_placeholder')" size="small" />
        <NInput
          v-model:value="newCommand"
          type="textarea"
          :placeholder="t('snippets.command_placeholder')"
          size="small"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
        <NInput v-model:value="newCategory" :placeholder="t('snippets.category_placeholder')" size="small" />
        <NSpace size="small">
          <NButton size="small" type="primary" @click="handleAdd" :disabled="!newLabel.trim() || !newCommand.trim()">
            {{ t('snippets.add_btn') }}
          </NButton>
          <NButton size="small" @click="resetAddForm">{{ t('common.cancel') }}</NButton>
        </NSpace>
      </div>

      <!-- Search -->
      <NInput
        v-model:value="filterText"
        :placeholder="t('snippets.filter_placeholder')"
        size="small"
        clearable
        class="snippet-filter"
      />

      <!-- Loading state -->
      <div v-if="snippetsStore.loading" class="snippet-loading">
        <NSpin size="small" />
      </div>

      <!-- Error state -->
      <div v-else-if="snippetsStore.error && snippetsStore.items.length === 0" class="snippet-empty">
        <NEmpty :description="snippetsStore.error">
          <template #extra>
            <NButton size="small" @click="snippetsStore.load(props.boxName, token)">{{ t('common.retry') }}</NButton>
          </template>
        </NEmpty>
      </div>

      <!-- Empty state -->
      <div v-else-if="filteredSnippets.length === 0" class="snippet-empty">
        <NEmpty :description="snippetsStore.items.length === 0 ? t('snippets.empty') : t('snippets.no_matches')" />
      </div>

      <!-- Snippets list -->
      <div v-for="(snippets, category) in groupedSnippets" :key="category" class="snippet-group">
        <div class="snippet-category">{{ category }}</div>
        <div
          v-for="snippet in snippets"
          :key="snippet.id"
          class="snippet-item"
        >
          <!-- Edit mode -->
          <div v-if="editingId === snippet.id" class="snippet-form" @keydown.esc.stop="cancelEdit">
            <NInput v-model:value="editLabel" :placeholder="t('snippets.label_placeholder')" size="small" />
            <NInput
              v-model:value="editCommand"
              type="textarea"
              :placeholder="t('snippets.command_placeholder')"
              size="small"
              :autosize="{ minRows: 2, maxRows: 5 }"
            />
            <NInput v-model:value="editCategory" :placeholder="t('snippets.category_placeholder')" size="small" />
            <NSpace size="small">
              <NButton size="small" type="primary" @click="handleSaveEdit" :loading="editSaving" :disabled="editSaving">{{ t('snippets.save_btn') }}</NButton>
              <NButton size="small" @click="cancelEdit" :disabled="editSaving">{{ t('common.cancel') }}</NButton>
            </NSpace>
          </div>

          <!-- Display mode -->
          <template v-else>
            <div class="snippet-header">
              <span class="snippet-label">{{ snippet.label }}</span>
              <NTag v-if="snippet.box === '__global__'" size="tiny" type="info">{{ t('snippets.global_tag') }}</NTag>
            </div>
            <pre class="snippet-command">{{ snippet.command }}</pre>
            <div class="snippet-actions">
              <NButton size="tiny" quaternary @click="handleInsert(snippet.command, true)" :aria-label="t('snippets.execute')">
                <NIcon size="14"><PhPlay weight="duotone" /></NIcon>
              </NButton>
              <NButton size="tiny" quaternary @click="handleInsert(snippet.command, false)" :aria-label="t('snippets.insert')">
                <NIcon size="14"><PhCopy weight="duotone" /></NIcon>
              </NButton>
              <NButton size="tiny" quaternary @click="startEdit(snippet)" :aria-label="t('snippets.edit')">
                <NIcon size="14"><PhPencilSimple weight="duotone" /></NIcon>
              </NButton>
              <NPopconfirm @positive-click="handleDelete(snippet.id)">
                <template #trigger>
                  <NButton size="tiny" quaternary :aria-label="t('common.delete')">
                    <NIcon size="14"><PhTrash weight="duotone" /></NIcon>
                  </NButton>
                </template>
                {{ t('snippets.delete_confirm') }}
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

.snippet-loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
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
