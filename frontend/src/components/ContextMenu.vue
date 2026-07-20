<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
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

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleKeyDown)
  
  nextTick(() => {
    if (menuRef.value) {
      // Adjust position if menu would go off screen
      const rect = menuRef.value.getBoundingClientRect()
      const viewportWidth = window.innerWidth
      const viewportHeight = window.innerHeight
      
      let adjustedX = props.x
      let adjustedY = props.y
      
      if (rect.right > viewportWidth) {
        adjustedX = viewportWidth - rect.width - 10
      }
      
      if (rect.bottom > viewportHeight) {
        adjustedY = viewportHeight - rect.height - 10
      }
      
      menuRef.value.style.left = `${Math.max(0, adjustedX)}px`
      menuRef.value.style.top = `${Math.max(0, adjustedY)}px`
    }
  })
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('keydown', handleKeyDown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      ref="menuRef"
      class="context-menu"
      :style="{ left: `${x}px`, top: `${y}px` }"
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
