<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { NIcon } from 'naive-ui'

interface MenuItem {
  id: string
  label: string
  icon?: any
  disabled?: boolean
  separator?: boolean
  danger?: boolean
}

interface Props {
  items: MenuItem[]
  x: number
  y: number
  visible: boolean
}

interface Emits {
  (e: 'select', itemId: string): void
  (e: 'close'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const menuRef = ref<HTMLDivElement>()

// Space kept between the menu and the window's edges; the stylesheet reads it as --menu-margin.
const MARGIN = 8

// Where the template puts the menu: always the answer of `place`, never a hand-written style.
const position = ref({ left: props.x, top: props.y })

const handleItemClick = (item: MenuItem) => {
  if (item.disabled || item.separator) return
  emit('select', item.id)
  emit('close')
}

const handleClickOutside = (event: MouseEvent) => {
  if (menuRef.value && !menuRef.value.contains(event.target as Node)) {
    emit('close')
  }
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    emit('close')
  }
}

// Keep a box of `size` that starts at `wanted` inside `0..limit` with a margin on both sides.
const fit = (wanted: number, size: number, limit: number) =>
  Math.max(MARGIN, Math.min(wanted, limit - size - MARGIN))

// Measure the open menu and put it at the pointer, moved up or left where there is no room.
// Its height is capped by the stylesheet to the window less the margins, so it always fits.
const place = () => {
  const el = menuRef.value
  if (!props.visible || !el) return
  const { width, height } = el.getBoundingClientRect()
  const left = fit(props.x, width, document.documentElement.clientWidth)
  const top = fit(props.y, height, document.documentElement.clientHeight)
  if (left !== position.value.left || top !== position.value.top) {
    position.value = { left, top }
  }
}

// After the DOM update and before the paint: every open, a new point and a new item list
// are measured again.
watch(() => [props.visible, props.x, props.y, props.items], place, { flush: 'post' })

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleKeyDown)
  window.addEventListener('resize', place)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('resize', place)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuRef"
      class="context-menu"
      :style="{ left: `${position.left}px`, top: `${position.top}px`, '--menu-margin': `${MARGIN}px` }"
    >
      <template v-for="(item, index) in items" :key="index">
        <div
          v-if="item.separator"
          class="context-menu-separator"
        />
        <div
          v-else
          class="context-menu-item"
          :class="{
            disabled: item.disabled,
            danger: item.danger
          }"
          @click="handleItemClick(item)"
        >
          <NIcon v-if="item.icon" size="16" class="menu-icon">
            <component :is="item.icon" />
          </NIcon>
          <span>{{ item.label }}</span>
        </div>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.context-menu {
  position: fixed;
  z-index: 9999;
  background: var(--surface);
  border: 1px solid var(--stroke);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  padding: 4px 0;
  min-width: 160px;
  max-width: 240px;
  /* the width does not depend on where the menu sits, so one measurement is enough */
  width: max-content;
  box-sizing: border-box;
  /* taller than the window: scroll inside the menu (--menu-margin is MARGIN of the script) */
  max-height: calc(100vh - 2 * var(--menu-margin));
  max-height: calc(100dvh - 2 * var(--menu-margin));
  overflow-y: auto;
  font-size: 14px;
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background-color 0.15s ease;
  user-select: none;
}

.context-menu-item:hover:not(.disabled) {
  background: var(--hover);
}

.context-menu-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.context-menu-item.danger {
  color: var(--error);
}

.context-menu-item.danger:hover:not(.disabled) {
  background: var(--error-hover);
}

.context-menu-separator {
  height: 1px;
  background: var(--stroke);
  margin: 4px 0;
}

.menu-icon {
  flex-shrink: 0;
}
</style>
