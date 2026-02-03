<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { PhKeyReturn, PhArrowUp, PhArrowDown, PhKeyboard } from '@phosphor-icons/vue'

interface Emits {
  (e: 'send', data: string): void
  (e: 'send-raw', data: string): void
  (e: 'toggle-raw-mode'): void
}

interface Props {
  rawMode?: boolean
  connected?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  rawMode: false,
  connected: false,
})

const emit = defineEmits<Emits>()

const inputText = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const historyStack = ref<string[]>([])
const historyIndex = ref(-1)

// Quick-action keys: things that are hard to type on mobile
const quickKeys = [
  { label: 'Tab', raw: '\t' },
  { label: 'Esc', raw: '\x1b' },
  { label: 'C-c', raw: '\x03' },
  { label: 'C-d', raw: '\x04' },
  { label: 'C-z', raw: '\x1a' },
  { label: 'C-l', raw: '\x0c' },
  { label: '|', raw: '|' },
  { label: '/', raw: '/' },
  { label: '-', raw: '-' },
  { label: '~', raw: '~' },
  { label: './', raw: './' },
]

const handleSubmit = () => {
  const text = inputText.value
  if (!text && !props.rawMode) {
    // Empty enter: just send newline to shell
    emit('send-raw', '\r')
    return
  }

  // Push to history
  if (text.trim()) {
    historyStack.value.unshift(text)
    if (historyStack.value.length > 100) {
      historyStack.value.pop()
    }
  }
  historyIndex.value = -1

  // Send the text character by character then carriage return
  emit('send', text + '\r')
  inputText.value = ''
}

const handleQuickKey = (raw: string) => {
  emit('send-raw', raw)
  // Refocus textarea after quick key
  nextTick(() => textareaRef.value?.focus())
}

const handleArrowUp = () => {
  if (historyStack.value.length === 0) {
    // No local history, send up arrow to shell
    emit('send-raw', '\x1b[A')
    return
  }
  if (historyIndex.value < historyStack.value.length - 1) {
    historyIndex.value++
    inputText.value = historyStack.value[historyIndex.value] || ''
  }
}

const handleArrowDown = () => {
  if (historyIndex.value <= 0) {
    historyIndex.value = -1
    inputText.value = ''
    return
  }
  historyIndex.value--
  inputText.value = historyStack.value[historyIndex.value] || ''
}

const handleKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSubmit()
  }
  if (e.key === 'ArrowUp' && !inputText.value) {
    e.preventDefault()
    handleArrowUp()
  }
  if (e.key === 'ArrowDown' && !inputText.value) {
    e.preventDefault()
    handleArrowDown()
  }
}

const toggleRawMode = () => {
  emit('toggle-raw-mode')
}

// Auto-focus on mount
onMounted(() => {
  if (!props.rawMode) {
    nextTick(() => textareaRef.value?.focus())
  }
})

// Focus when switching out of raw mode
watch(() => props.rawMode, (raw) => {
  if (!raw) {
    nextTick(() => textareaRef.value?.focus())
  }
})
</script>

<template>
  <div class="mobile-input-bar" :class="{ 'raw-mode': rawMode }">
    <!-- Quick keys row -->
    <div v-if="!rawMode" class="quick-keys">
      <button
        v-for="key in quickKeys"
        :key="key.label"
        class="quick-key"
        @click="handleQuickKey(key.raw)"
        :disabled="!connected"
      >
        {{ key.label }}
      </button>
      <button
        class="quick-key arrow-key"
        @click="handleArrowUp"
        :disabled="!connected"
        title="Up"
      >
        <NIcon size="12"><PhArrowUp weight="bold" /></NIcon>
      </button>
      <button
        class="quick-key arrow-key"
        @click="handleArrowDown"
        :disabled="!connected"
        title="Down"
      >
        <NIcon size="12"><PhArrowDown weight="bold" /></NIcon>
      </button>
    </div>

    <!-- Input row -->
    <div class="input-row">
      <button
        class="mode-toggle"
        :class="{ active: rawMode }"
        @click="toggleRawMode"
        :title="rawMode ? 'Smart input (autocorrect ON)' : 'Raw mode (xterm direct)'"
      >
        <NIcon size="14"><PhKeyboard weight="bold" /></NIcon>
      </button>

      <div v-if="!rawMode" class="input-wrapper">
        <textarea
          ref="textareaRef"
          v-model="inputText"
          class="smart-input"
          placeholder="command..."
          rows="1"
          autocomplete="off"
          autocapitalize="none"
          spellcheck="true"
          enterkeyhint="send"
          @keydown="handleKeydown"
          :disabled="!connected"
        />
        <button
          class="send-btn"
          @click="handleSubmit"
          :disabled="!connected"
        >
          <NIcon size="16"><PhKeyReturn weight="bold" /></NIcon>
        </button>
      </div>

      <div v-else class="raw-mode-label">
        <span>Raw mode — tapping terminal directly</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mobile-input-bar {
  flex-shrink: 0;
  background: rgba(10, 14, 20, 0.98);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  z-index: 20;
  /* Keep above keyboard */
  position: relative;
}

/* Quick keys row */
.quick-keys {
  display: flex;
  gap: 4px;
  padding: 4px 6px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.quick-keys::-webkit-scrollbar {
  display: none;
}

.quick-key {
  flex-shrink: 0;
  height: 28px;
  min-width: 32px;
  padding: 0 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  color: rgba(255, 255, 255, 0.7);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.1s ease, border-color 0.1s ease;
}

.quick-key:active {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.25);
}

.quick-key:disabled {
  opacity: 0.3;
  cursor: default;
}

.quick-key.arrow-key {
  min-width: 28px;
  padding: 0 4px;
}

/* Input row */
.input-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 6px;
}

.mode-toggle {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: transparent;
  color: rgba(255, 255, 255, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mode-toggle.active {
  background: rgba(230, 180, 80, 0.2);
  border-color: rgba(230, 180, 80, 0.4);
  color: #e6b450;
}

.mode-toggle:active {
  background: rgba(255, 255, 255, 0.1);
}

.input-wrapper {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0;
  min-width: 0;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  overflow: hidden;
}

.smart-input {
  flex: 1;
  min-width: 0;
  height: 32px;
  padding: 6px 10px;
  background: transparent;
  border: none;
  outline: none;
  color: #e6e6e6;
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  font-size: 14px;
  line-height: 1.4;
  resize: none;
  overflow: hidden;
  /* Enable mobile typing features */
  -webkit-text-size-adjust: none;
}

.smart-input::placeholder {
  color: rgba(255, 255, 255, 0.2);
}

.smart-input:disabled {
  opacity: 0.3;
}

.send-btn {
  flex-shrink: 0;
  width: 36px;
  height: 32px;
  background: rgba(83, 189, 250, 0.15);
  border: none;
  color: #53bdfa;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.1s ease;
}

.send-btn:active {
  background: rgba(83, 189, 250, 0.3);
}

.send-btn:disabled {
  opacity: 0.3;
  cursor: default;
}

.raw-mode-label {
  flex: 1;
  display: flex;
  align-items: center;
  height: 32px;
  padding: 0 8px;
  color: rgba(255, 255, 255, 0.3);
  font-size: 12px;
  font-style: italic;
}

/* Raw mode: minimal bar */
.mobile-input-bar.raw-mode {
  border-top-color: rgba(230, 180, 80, 0.2);
}

.mobile-input-bar.raw-mode .input-row {
  padding: 4px 6px;
}
</style>
