<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import { NIcon } from 'naive-ui'
import { PhX, PhPlus } from '@phosphor-icons/vue'
import type { TerminalTab } from '@/stores/terminalTabs'
import { useI18n } from '@/i18n'

const props = defineProps<{
  tabs: TerminalTab[]
  activeTabId: string | null
  connectedByTab: Record<string, boolean>
}>()

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'close', id: string): void
  (e: 'kill', id: string): void
  (e: 'add'): void
  (e: 'rename', id: string, label: string): void
}>()

const { t } = useI18n()

const editingId = ref<string | null>(null)
const editValue = ref('')
const renameInput = ref<HTMLInputElement | null>(null)

// Right-click context menu — the deliberate place to KILL a terminal for good
// (terminate the server-side shell), as opposed to the X button which only
// closes/detaches the tab and leaves the shell running for re-attachment.
const menuTabId = ref<string | null>(null)
const menuX = ref(0)
const menuY = ref(0)

function tabById(id: string): TerminalTab | undefined {
  return props.tabs.find((tab) => tab.id === id)
}

function openMenu(event: MouseEvent, tabId: string) {
  event.preventDefault()
  menuTabId.value = tabId
  menuX.value = event.clientX
  menuY.value = event.clientY
}

function closeMenu() {
  menuTabId.value = null
}

function onMenuClose(id: string) {
  closeMenu()
  emit('close', id)
}

// --- Rename modal (opened from the context menu's top item) ----------------
// Distinct from the inline double-click rename: same `rename` emit, but a
// roomier modal so the user can rename without first finding the tiny label.
const renameModalId = ref<string | null>(null)
const renameModalValue = ref('')
const renameModalInput = ref<HTMLInputElement | null>(null)

function openRenameModal(id: string) {
  closeMenu()
  const tab = tabById(id)
  if (!tab) return
  renameModalId.value = id
  renameModalValue.value = tab.label
  nextTick(() => {
    const el = renameModalInput.value
    if (el && typeof el.focus === 'function') {
      el.focus()
      el.select()
    }
  })
}

function commitRenameModal() {
  const id = renameModalId.value
  if (!id) return
  const trimmed = renameModalValue.value.trim()
  if (trimmed) emit('rename', id, trimmed)
  renameModalId.value = null
  renameModalValue.value = ''
}

function cancelRenameModal() {
  renameModalId.value = null
  renameModalValue.value = ''
}

// --- Kill confirmation modal -----------------------------------------------
// Killing terminates the server-side shell irreversibly, so the menu item now
// arms a confirm step instead of emitting `kill` outright.
const killConfirmId = ref<string | null>(null)
const killConfirmLabel = computed(
  () => (killConfirmId.value ? tabById(killConfirmId.value)?.label ?? '' : ''),
)

function requestKill(id: string) {
  closeMenu()
  killConfirmId.value = id
}

function confirmKill() {
  const id = killConfirmId.value
  if (!id) return
  killConfirmId.value = null
  emit('kill', id)
}

function cancelKill() {
  killConfirmId.value = null
}

function startEdit(tab: TerminalTab) {
  editingId.value = tab.id
  editValue.value = tab.label
  nextTick(() => {
    const el = renameInput.value
    if (el && typeof el.focus === 'function') {
      el.focus()
      el.select()
    }
  })
}

function commitRename(id: string) {
  const trimmed = editValue.value.trim()
  if (trimmed) {
    emit('rename', id, trimmed)
  }
  editingId.value = null
  editValue.value = ''
}

function cancelRename() {
  editingId.value = null
  editValue.value = ''
}

function onLabelDblClick(tab: TerminalTab) {
  startEdit(tab)
}

function onTabClick(tab: TerminalTab) {
  if (editingId.value === tab.id) return
  emit('select', tab.id)
}

function onCloseClick(event: MouseEvent, tabId: string) {
  event.stopPropagation()
  emit('close', tabId)
}
</script>

<template>
  <div class="tab-bar">
    <div
      v-for="tab in tabs"
      :key="tab.id"
      class="tab-item"
      :class="{ active: tab.id === activeTabId }"
      data-testid="tab-item"
      :data-tab-id="tab.id"
      role="tab"
      tabindex="0"
      :aria-label="tab.label"
      :aria-current="tab.id === activeTabId ? 'true' : undefined"
      @click="onTabClick(tab)"
      @keyup.enter="onTabClick(tab)"
      @contextmenu="openMenu($event, tab.id)"
    >
      <span
        class="tab-dot"
        :class="connectedByTab[tab.id] ? 'tab-dot--connected' : 'tab-dot--disconnected'"
        data-testid="tab-dot"
      />

      <template v-if="editingId === tab.id">
        <input
          ref="renameInput"
          v-model="editValue"
          class="tab-rename-input"
          data-testid="tab-rename-input"
          @click.stop
          @keyup.enter.stop="commitRename(tab.id)"
          @keyup.escape.stop="cancelRename"
          @blur="cancelRename"
        />
      </template>
      <template v-else>
        <span
          class="tab-label"
          data-testid="tab-label"
          @dblclick.stop="onLabelDblClick(tab)"
        >{{ tab.label }}</span>
      </template>

      <button
        class="tab-close"
        data-testid="tab-close"
        :title="t('terminal.tabs.close')"
        :aria-label="t('terminal.tabs.close')"
        @click="onCloseClick($event, tab.id)"
      >
        <NIcon size="11"><PhX weight="bold" /></NIcon>
      </button>
    </div>

    <button
      class="tab-add"
      data-testid="tab-add"
      :title="t('terminal.tabs.new')"
      :aria-label="t('terminal.tabs.new')"
      @click="emit('add')"
    >
      <NIcon size="13"><PhPlus weight="bold" /></NIcon>
    </button>
  </div>

  <!--
    Tab context menu (right-click). Full-viewport backdrop closes it on any
    outside click. "Kill terminal" is the deliberate, danger-styled action that
    terminates the server-side shell so reopening spawns a fresh one.
  -->
  <Teleport to="body">
    <div
      v-if="menuTabId"
      class="tab-menu-backdrop"
      data-testid="tab-menu-backdrop"
      @click="closeMenu"
      @contextmenu.prevent="closeMenu"
    >
      <div
        class="tab-menu"
        data-testid="tab-context-menu"
        :style="{ left: `${menuX}px`, top: `${menuY}px` }"
        @click.stop
      >
        <button
          class="tab-menu-item"
          data-testid="tab-menu-rename"
          @click="openRenameModal(menuTabId!)"
        >
          {{ t('terminal.tabs.rename') }}
        </button>
        <button
          class="tab-menu-item"
          data-testid="tab-menu-close"
          @click="onMenuClose(menuTabId!)"
        >
          {{ t('terminal.tabs.close_keep') }}
        </button>
        <button
          class="tab-menu-item tab-menu-item--danger"
          data-testid="tab-menu-kill"
          @click="requestKill(menuTabId!)"
        >
          {{ t('terminal.tabs.kill') }}
        </button>
      </div>
    </div>
  </Teleport>

  <!-- Rename modal (opened from the context menu's "Rename" item). -->
  <Teleport to="body">
    <div
      v-if="renameModalId"
      class="tab-modal-backdrop"
      data-testid="tab-rename-modal-backdrop"
      @click="cancelRenameModal"
      @contextmenu.prevent="cancelRenameModal"
    >
      <div
        class="tab-modal"
        data-testid="tab-rename-modal"
        role="dialog"
        aria-modal="true"
        @click.stop
      >
        <div class="tab-modal-title">{{ t('terminal.tabs.rename_title') }}</div>
        <input
          ref="renameModalInput"
          v-model="renameModalValue"
          class="tab-modal-input"
          data-testid="tab-rename-modal-input"
          @keyup.enter.stop="commitRenameModal"
          @keyup.escape.stop="cancelRenameModal"
        />
        <div class="tab-modal-actions">
          <button
            class="tab-modal-btn"
            data-testid="tab-rename-modal-cancel"
            @click="cancelRenameModal"
          >
            {{ t('common.cancel') }}
          </button>
          <button
            class="tab-modal-btn tab-modal-btn--primary"
            data-testid="tab-rename-modal-save"
            @click="commitRenameModal"
          >
            {{ t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- Kill confirmation modal — killing terminates the server-side shell. -->
  <Teleport to="body">
    <div
      v-if="killConfirmId"
      class="tab-modal-backdrop"
      data-testid="tab-kill-modal-backdrop"
      @click="cancelKill"
      @contextmenu.prevent="cancelKill"
    >
      <div
        class="tab-modal"
        data-testid="tab-kill-modal"
        role="dialog"
        aria-modal="true"
        @click.stop
      >
        <div class="tab-modal-title">{{ t('terminal.tabs.kill_confirm_title') }}</div>
        <div class="tab-modal-body" data-testid="tab-kill-modal-body">
          {{ t('terminal.tabs.kill_confirm', { label: killConfirmLabel }) }}
        </div>
        <div class="tab-modal-actions">
          <button
            class="tab-modal-btn"
            data-testid="tab-kill-modal-cancel"
            @click="cancelKill"
          >
            {{ t('common.cancel') }}
          </button>
          <button
            class="tab-modal-btn tab-modal-btn--danger"
            data-testid="tab-kill-modal-confirm"
            @click="confirmKill"
          >
            {{ t('terminal.tabs.kill') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.tab-bar {
  display: flex;
  align-items: center;
  overflow-x: auto;
  scrollbar-width: none;
  background: var(--surface);
  border-bottom: 1px solid var(--stroke);
  gap: 2px;
  padding: 0 4px;
  min-height: 34px;
}

.tab-bar::-webkit-scrollbar {
  display: none;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border: none;
  border-radius: 6px 6px 0 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  font-family: inherit;
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 0;
  transition: background 0.12s ease, color 0.12s ease;
}

.tab-item:hover {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--text);
}

.tab-item.active {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--text);
  border-bottom: 2px solid var(--accent);
}

.tab-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tab-dot--connected {
  background: #22c55e;
}

.tab-dot--disconnected {
  background: var(--muted);
  opacity: 0.45;
}

.tab-label {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  user-select: none;
}

.tab-rename-input {
  width: 90px;
  padding: 1px 4px;
  font-size: 12px;
  font-family: inherit;
  background: var(--surface-variant, var(--surface));
  border: 1px solid var(--accent);
  border-radius: 3px;
  color: var(--text);
  outline: none;
}

.tab-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px;
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  border-radius: 3px;
  line-height: 1;
  flex-shrink: 0;
}

.tab-close:hover {
  background: color-mix(in srgb, var(--accent) 20%, transparent);
  color: var(--text);
}

.tab-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 6px;
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  border-radius: 6px;
  flex-shrink: 0;
  margin-left: 2px;
  transition: background 0.12s ease, color 0.12s ease;
}

.tab-add:hover {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--text);
}

.tab-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 4000;
}

.tab-menu {
  position: fixed;
  min-width: 200px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tab-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 7px 10px;
  border: none;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  font-family: inherit;
  border-radius: 5px;
  cursor: pointer;
  white-space: nowrap;
}

.tab-menu-item:hover {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}

.tab-menu-item--danger {
  color: #ef4444;
}

.tab-menu-item--danger:hover {
  background: color-mix(in srgb, #ef4444 16%, transparent);
  color: #ef4444;
}

/* --- Rename / Kill-confirm modals --------------------------------------- */
.tab-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 4100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}

.tab-modal {
  width: 320px;
  max-width: calc(100vw - 32px);
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 10px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tab-modal-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.tab-modal-body {
  font-size: 13px;
  line-height: 1.45;
  color: var(--muted);
}

.tab-modal-input {
  width: 100%;
  padding: 7px 9px;
  font-size: 13px;
  font-family: inherit;
  background: var(--surface-variant, var(--surface));
  border: 1px solid var(--accent);
  border-radius: 6px;
  color: var(--text);
  outline: none;
  box-sizing: border-box;
}

.tab-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.tab-modal-btn {
  padding: 6px 14px;
  font-size: 12px;
  font-family: inherit;
  border: 1px solid var(--stroke);
  background: transparent;
  color: var(--text);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}

.tab-modal-btn:hover {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}

.tab-modal-btn--primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.tab-modal-btn--primary:hover {
  background: color-mix(in srgb, var(--accent) 85%, #000);
}

.tab-modal-btn--danger {
  border-color: #ef4444;
  background: #ef4444;
  color: #fff;
}

.tab-modal-btn--danger:hover {
  background: color-mix(in srgb, #ef4444 85%, #000);
}
</style>
