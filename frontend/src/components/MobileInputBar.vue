<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, watch, type Component } from 'vue'
import { NIcon } from 'naive-ui'
import {
  PhKeyReturn,
  PhArrowUp,
  PhArrowDown,
  PhArrowLeft,
  PhArrowRight,
  PhKeyboard,
  PhHandPalm,
  PhStopCircle,
  PhScroll,
  PhArrowFatLinesUp,
  PhArrowFatLinesDown,
  PhSignOut,
  PhArrowElbowDownRight,
  PhCaretUp,
  PhCaretDown,
  PhCaretLeft,
  PhCaretRight,
  PhQuestion,
} from '@phosphor-icons/vue'
import { useI18n } from '@/i18n'

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
const { t } = useI18n()

const inputText = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const historyStack = ref<string[]>([])
const historyIndex = ref(-1)
const showLegend = ref(false)

// Quick-action keys: things that are hard to type on mobile
// Using Phosphor icons for a clean, consistent look
interface QuickKey {
  icon?: Component
  label?: string
  raw?: string
  tmuxCommand?: string
  title: string
  isCommand?: boolean
  color?: 'blue' | 'purple' | 'yellow' | 'red' | 'orange' | 'teal'
}

const quickKeys: QuickKey[] = [
  // Arrow keys for menu navigation (default/neutral)
  { icon: PhCaretUp, raw: '\x1b[A', title: 'Up' },
  { icon: PhCaretDown, raw: '\x1b[B', title: 'Down' },
  { icon: PhCaretLeft, raw: '\x1b[D', title: 'Left' },
  { icon: PhCaretRight, raw: '\x1b[C', title: 'Right' },
  // Confirm (blue)
  { icon: PhKeyReturn, raw: '\r', title: 'Enter', color: 'blue' },
  // Tab (purple)
  { icon: PhArrowElbowDownRight, raw: '\t', title: 'Tab', color: 'purple' },
  // Escape (yellow - caution, stops things)
  { icon: PhStopCircle, raw: '\x1b', title: 'Escape / Stop', color: 'yellow' },
  // Interrupt (red - dangerous, kills processes)
  { icon: PhHandPalm, raw: '\x03', title: 'Interrupt (Ctrl+C)', color: 'red' },
  // Tmux scroll (orange group)
  { icon: PhScroll, raw: '\x02[', title: 'Scroll mode', color: 'orange' },
  { icon: PhArrowFatLinesUp, raw: '\x1b[5~', title: 'Page Up', color: 'orange' },
  { icon: PhArrowFatLinesDown, raw: '\x1b[6~', title: 'Page Down', color: 'orange' },
  // Exit (teal - graceful exit, sends EOF)
  { icon: PhSignOut, raw: '\x04', title: 'Exit (Ctrl+D)', color: 'teal' },
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

const handleQuickKey = (key: any) => {
  if (key.isCommand) {
    // Send as a command (like typing and hitting enter)
    emit('send', key.raw + '\r')
    // Refocus textarea after quick key
    nextTick(() => textareaRef.value?.focus())
  } else if (key.tmuxCommand) {
    // Tmux command mode: send Ctrl+B : first, then the command after a delay
    emit('send-raw', '\x02:')
    setTimeout(() => {
      emit('send-raw', key.tmuxCommand + '\r')
      // Refocus AFTER the command is sent
      nextTick(() => textareaRef.value?.focus())
    }, 150)
  } else {
    // Send as raw escape sequence
    emit('send-raw', key.raw)
    // Refocus textarea after quick key
    nextTick(() => textareaRef.value?.focus())
  }
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
        v-for="(key, index) in quickKeys"
        :key="index"
        class="quick-key"
        :class="key.color"
        @click="handleQuickKey(key)"
        :disabled="!connected"
        :title="key.title"
      >
        <component v-if="key.icon" :is="key.icon" :size="18" weight="bold" />
        <span v-else>{{ key.label }}</span>
      </button>
      <!-- Help button -->
      <button
        class="quick-key help-btn"
        @click="showLegend = true"
        title="Show key legend"
      >
        <PhQuestion :size="18" weight="bold" />
      </button>
    </div>

    <!-- Legend overlay -->
    <Teleport to="body">
      <div v-if="showLegend" class="legend-overlay" @click="showLegend = false">
        <div class="legend-card" @click.stop>
          <div class="legend-header">
            <span>{{ t('mobile.quick_keys') }}</span>
            <button class="legend-close" @click="showLegend = false">✕</button>
          </div>
          <div class="legend-grid">
            <div class="legend-item" v-for="(key, index) in quickKeys" :key="index">
              <span class="legend-icon" :class="key.color">
                <component v-if="key.icon" :is="key.icon" :size="20" weight="bold" />
              </span>
              <span class="legend-label">{{ key.title }}</span>
            </div>
          </div>
          <div class="legend-footer">
            {{ t('mobile.close_hint') }}
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Input row -->
    <div class="input-row">
      <button
        class="mode-toggle"
        :class="{ active: rawMode }"
        @click="toggleRawMode"
        :title="rawMode ? t('mobile.smart_input') : t('mobile.raw_mode')"
      >
        <NIcon size="14"><PhKeyboard weight="bold" /></NIcon>
      </button>

      <div v-if="!rawMode" class="input-wrapper">
        <textarea
          ref="textareaRef"
          v-model="inputText"
          class="smart-input"
          :placeholder="t('mobile.command_placeholder')"
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
        <span>{{ t('mobile.raw_mode_hint') }}</span>
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

/* Color variants */
.quick-key.blue {
  background: rgba(83, 189, 250, 0.12);
  border-color: rgba(83, 189, 250, 0.25);
  color: #53bdfa;
}
.quick-key.blue:active {
  background: rgba(83, 189, 250, 0.25);
}

.quick-key.purple {
  background: rgba(180, 130, 250, 0.12);
  border-color: rgba(180, 130, 250, 0.25);
  color: #b482fa;
}
.quick-key.purple:active {
  background: rgba(180, 130, 250, 0.25);
}

.quick-key.yellow {
  background: rgba(250, 200, 80, 0.12);
  border-color: rgba(250, 200, 80, 0.25);
  color: #fac850;
}
.quick-key.yellow:active {
  background: rgba(250, 200, 80, 0.25);
}

.quick-key.red {
  background: rgba(250, 100, 100, 0.12);
  border-color: rgba(250, 100, 100, 0.25);
  color: #fa6464;
}
.quick-key.red:active {
  background: rgba(250, 100, 100, 0.25);
}

.quick-key.orange {
  background: rgba(250, 160, 80, 0.12);
  border-color: rgba(250, 160, 80, 0.25);
  color: #faa050;
}
.quick-key.orange:active {
  background: rgba(250, 160, 80, 0.25);
}

.quick-key.teal {
  background: rgba(80, 200, 180, 0.12);
  border-color: rgba(80, 200, 180, 0.25);
  color: #50c8b4;
}
.quick-key.teal:active {
  background: rgba(80, 200, 180, 0.25);
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

/* Help button */
.help-btn {
  background: rgba(83, 189, 250, 0.1) !important;
  border-color: rgba(83, 189, 250, 0.2) !important;
  color: #53bdfa !important;
}

.help-btn:active {
  background: rgba(83, 189, 250, 0.25) !important;
}

/* Legend overlay */
.legend-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  padding: 16px;
}

.legend-card {
  background: rgba(20, 24, 32, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  max-width: 320px;
  width: 100%;
  max-height: 80vh;
  overflow: hidden;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5);
}

.legend-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  font-weight: 600;
  color: #e6e6e6;
  font-size: 14px;
}

.legend-close {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  color: rgba(255, 255, 255, 0.5);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.legend-close:active {
  background: rgba(255, 255, 255, 0.1);
}

.legend-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 2px;
  padding: 8px;
  max-height: 50vh;
  overflow-y: auto;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.02);
}

.legend-item:active {
  background: rgba(255, 255, 255, 0.05);
}

.legend-icon {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.6);
}

/* Legend icon colors */
.legend-icon.blue { color: #53bdfa; background: rgba(83, 189, 250, 0.15); }
.legend-icon.purple { color: #b482fa; background: rgba(180, 130, 250, 0.15); }
.legend-icon.yellow { color: #fac850; background: rgba(250, 200, 80, 0.15); }
.legend-icon.red { color: #fa6464; background: rgba(250, 100, 100, 0.15); }
.legend-icon.orange { color: #faa050; background: rgba(250, 160, 80, 0.15); }
.legend-icon.teal { color: #50c8b4; background: rgba(80, 200, 180, 0.15); }

.legend-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.7);
  line-height: 1.3;
}

.legend-footer {
  padding: 10px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 11px;
  color: rgba(255, 255, 255, 0.3);
  text-align: center;
}
</style>
