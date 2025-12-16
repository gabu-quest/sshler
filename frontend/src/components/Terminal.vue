<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { SearchAddon } from '@xterm/addon-search'
import { useMessage } from 'naive-ui'
import { useBootstrapStore } from '@/stores/bootstrap'

interface Props {
  boxName: string
  sessionName?: string
  directory?: string
  theme?: 'default' | 'solarized' | 'dracula' | 'nord' | 'monokai'
  fontSize?: number
  fontFamily?: string
}

interface Emits {
  (e: 'connected'): void
  (e: 'disconnected'): void
  (e: 'bell'): void
  (e: 'notification', data: { title: string; message: string }): void
  (e: 'resize', data: { cols: number; rows: number }): void
}

const props = withDefaults(defineProps<Props>(), {
  sessionName: 'main',
  directory: '~',
  theme: 'default',
  fontSize: 14,
  fontFamily: 'SF Mono, Monaco, Cascadia Code, Roboto Mono, Consolas, Courier New, monospace'
})

const emit = defineEmits<Emits>()
const message = useMessage()
const bootstrapStore = useBootstrapStore()

const terminalRef = ref<HTMLDivElement>()
const connected = ref(false)
const connecting = ref(false)

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null)

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let searchAddon: SearchAddon | null = null
let websocket: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null
let resizeTimeout: number | null = null

const TERMINAL_THEMES = {
  default: {
    background: '#0f1115',
    foreground: '#e6e6e6',
    cursor: '#6aa6ff',
    cursorAccent: '#0f1115',
    selectionBackground: '#4a86df80',
    black: '#1a1a1a',
    red: '#ff6a6a',
    green: '#3ba86a',
    yellow: '#f5c542',
    blue: '#6aa6ff',
    magenta: '#c678dd',
    cyan: '#56b6c2',
    white: '#e6e6e6',
    brightBlack: '#6b7280',
    brightRed: '#ff8787',
    brightGreen: '#5fd75f',
    brightYellow: '#ffd75f',
    brightBlue: '#85b8ff',
    brightMagenta: '#d19aff',
    brightCyan: '#76d7c4',
    brightWhite: '#ffffff',
  },
  solarized: {
    background: '#002b36',
    foreground: '#839496',
    cursor: '#839496',
    cursorAccent: '#002b36',
    selectionBackground: '#073642',
    black: '#073642',
    red: '#dc322f',
    green: '#859900',
    yellow: '#b58900',
    blue: '#268bd2',
    magenta: '#d33682',
    cyan: '#2aa198',
    white: '#eee8d5',
    brightBlack: '#002b36',
    brightRed: '#cb4b16',
    brightGreen: '#586e75',
    brightYellow: '#657b83',
    brightBlue: '#839496',
    brightMagenta: '#6c71c4',
    brightCyan: '#93a1a1',
    brightWhite: '#fdf6e3',
  },
  dracula: {
    background: '#282a36',
    foreground: '#f8f8f2',
    cursor: '#f8f8f2',
    cursorAccent: '#282a36',
    selectionBackground: '#44475a',
    black: '#000000',
    red: '#ff5555',
    green: '#50fa7b',
    yellow: '#f1fa8c',
    blue: '#bd93f9',
    magenta: '#ff79c6',
    cyan: '#8be9fd',
    white: '#bfbfbf',
    brightBlack: '#4d4d4d',
    brightRed: '#ff6e67',
    brightGreen: '#5af78e',
    brightYellow: '#f4f99d',
    brightBlue: '#caa9fa',
    brightMagenta: '#ff92d0',
    brightCyan: '#9aedfe',
    brightWhite: '#e6e6e6',
  },
  nord: {
    background: '#2e3440',
    foreground: '#d8dee9',
    cursor: '#d8dee9',
    cursorAccent: '#2e3440',
    selectionBackground: '#4c566a',
    black: '#3b4252',
    red: '#bf616a',
    green: '#a3be8c',
    yellow: '#ebcb8b',
    blue: '#81a1c1',
    magenta: '#b48ead',
    cyan: '#88c0d0',
    white: '#e5e9f0',
    brightBlack: '#4c566a',
    brightRed: '#bf616a',
    brightGreen: '#a3be8c',
    brightYellow: '#ebcb8b',
    brightBlue: '#81a1c1',
    brightMagenta: '#b48ead',
    brightCyan: '#8fbcbb',
    brightWhite: '#eceff4',
  },
  monokai: {
    background: '#272822',
    foreground: '#f8f8f2',
    cursor: '#f8f8f0',
    cursorAccent: '#272822',
    selectionBackground: '#49483e',
    black: '#272822',
    red: '#f92672',
    green: '#a6e22e',
    yellow: '#f4bf75',
    blue: '#66d9ef',
    magenta: '#ae81ff',
    cyan: '#a1efe4',
    white: '#f8f8f2',
    brightBlack: '#75715e',
    brightRed: '#f92672',
    brightGreen: '#a6e22e',
    brightYellow: '#f4bf75',
    brightBlue: '#66d9ef',
    brightMagenta: '#ae81ff',
    brightCyan: '#a1efe4',
    brightWhite: '#f9f8f5',
  }
}

const handleTerminalClick = () => {
  console.log('Terminal clicked, focusing...') // Debug
  terminal?.focus()
}

const createTerminal = () => {
  if (!terminalRef.value) return

  const theme = TERMINAL_THEMES[props.theme]
  
  terminal = new Terminal({
    theme,
    fontSize: props.fontSize,
    fontFamily: props.fontFamily,
    cursorBlink: true,
    allowTransparency: false,
    scrollback: 10000,
    rightClickSelectsWord: true,
    macOptionIsMeta: true,
    macOptionClickForcesSelection: false,
    convertEol: true,
    allowProposedApi: true
  })

  fitAddon = new FitAddon()
  searchAddon = new SearchAddon()
  
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(new WebLinksAddon())
  terminal.loadAddon(searchAddon)

  terminal.open(terminalRef.value)
  
  // Handle bell
  terminal.onBell(() => {
    emit('bell')
    handleBell()
  })

  // Handle data input
  terminal.onData((data) => {
    console.log('Terminal onData triggered:', data) // Debug
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({
        type: 'input',
        data: data
      }))
    } else {
      console.log('WebSocket not open, cannot send data. State:', websocket?.readyState)
    }
  })

  // Handle resize
  terminal.onResize(({ cols, rows }) => {
    emit('resize', { cols, rows })
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({
        type: 'resize',
        cols,
        rows
      }))
    }
  })

  // Setup resize observer with throttling
  setupResizeObserver()
  
  // Initial fit
  nextTick(() => {
    fitAddon?.fit()
    terminal?.focus()
  })
}

const setupResizeObserver = () => {
  if (!terminalRef.value || !fitAddon) return

  resizeObserver = new ResizeObserver(() => {
    if (resizeTimeout) {
      clearTimeout(resizeTimeout)
    }
    resizeTimeout = window.setTimeout(() => {
      fitAddon?.fit()
    }, 100)
  })

  resizeObserver.observe(terminalRef.value)
}

const handleBell = () => {
  // Flash title if tab is not visible
  if (document.hidden) {
    const originalTitle = document.title
    document.title = '🔔 ' + originalTitle
    
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        document.title = originalTitle
        document.removeEventListener('visibilitychange', handleVisibilityChange)
      }
    }
    
    document.addEventListener('visibilitychange', handleVisibilityChange)
    
    // Show notification if permission granted
    if (Notification.permission === 'granted') {
      new Notification('Terminal Bell', {
        body: `Activity in ${props.boxName}`,
        icon: '/favicon.svg',
        tag: 'terminal-bell'
      })
    }
  }
}

const handleOSC777 = (data: string) => {
  try {
    // Parse OSC 777 notification
    const match = data.match(/notify=(.+)/)
    if (!match || !match[1]) return

    const payload = match[1]
    let title = 'Terminal Notification'
    let messageText = payload

    // Check if it's JSON
    if (payload.startsWith('{')) {
      try {
        const parsed = JSON.parse(payload)
        title = parsed.title || title
        messageText = parsed.message || parsed.body || messageText
      } catch {
        // Not JSON, try pipe format
        const parts = payload.split('|')
        if (parts.length >= 2 && parts[0] && parts[1]) {
          title = decodeURIComponent(parts[0])
          messageText = decodeURIComponent(parts[1])
        }
      }
    } else {
      // Try pipe format
      const parts = payload.split('|')
      if (parts.length >= 2 && parts[0] && parts[1]) {
        title = decodeURIComponent(parts[0])
        messageText = decodeURIComponent(parts[1])
      }
    }

    emit('notification', { title, message: messageText })
    
    // Show browser notification
    if (Notification.permission === 'granted') {
      new Notification(title, {
        body: messageText,
        icon: '/favicon.svg',
        tag: 'terminal-osc777'
      })
    }
    
    // Show toast
    message.info(`${title}: ${messageText}`)
  } catch (error) {
    console.warn('Failed to parse OSC 777 notification:', error)
  }
}

const connect = async () => {
  if (connecting.value || connected.value) return

  connecting.value = true
  
  try {
    // Get WebSocket URL from backend handshake to ensure proper host resolution
    const handshakeResponse = await fetch('/api/v1/terminal/handshake', {
      headers: {
        'Accept': 'application/json',
        ...(tokenValue.value ? { 'X-SSHLER-TOKEN': tokenValue.value } : {})
      }
    })
    
    if (!handshakeResponse.ok) {
      throw new Error(`Handshake failed: ${handshakeResponse.status}`)
    }
    
    const handshake = await handshakeResponse.json()
    let wsUrl = handshake.ws_url
    
    // Add required query parameters
    const params = new URLSearchParams({
      host: props.boxName,
      dir: props.directory || '~',
      session: props.sessionName || 'main',
      cols: '120',
      rows: '32'
    })
    
    const token = tokenValue.value || bootstrapStore.token || bootstrapStore.payload?.token
    if (token) {
      params.set('token', token)
    }
    
    wsUrl += '?' + params.toString()
    
    websocket = new WebSocket(wsUrl)
    websocket.binaryType = 'arraybuffer' // Critical for binary terminal data!
    
    websocket.onopen = () => {
      connecting.value = false
      connected.value = true
      emit('connected')
      
      // Focus terminal to enable input
      nextTick(() => {
        terminal?.focus()
      })
    }
    
    websocket.onmessage = async (event) => {
      // Handle binary data (actual terminal output)
      if (event.data instanceof ArrayBuffer) {
        terminal?.write(new Uint8Array(event.data))
        return
      }
      
      if (event.data instanceof Blob) {
        const buffer = await event.data.arrayBuffer()
        terminal?.write(new Uint8Array(buffer))
        return
      }
      
      // Handle text data
      if (typeof event.data === 'string') {
        try {
          const message = JSON.parse(event.data)
          // Filter out control messages - don't display them in terminal
          if (message.op === 'ping' || message.op === 'windows') {
            return // Ignore these control messages
          }
          // Any other JSON messages we don't recognize - ignore for now
          console.log('Unknown control message:', message)
        } catch {
          // Not JSON - this is raw terminal text data
          terminal?.write(event.data)
        }
      }
    }
    
    websocket.onclose = (event) => {
      connected.value = false
      connecting.value = false
      emit('disconnected')
      
      // Log close reason for debugging
      console.log('WebSocket closed:', event.code, event.reason)
      
      if (event.code === 4403) {
        message.error('Authentication failed - please refresh the page')
      } else if (event.code === 4401) {
        message.error('Authorization failed')
      } else if (event.code === 1005) {
        // Connection dropped - try to reconnect automatically
        console.log('Connection dropped, attempting reconnect in 2s...')
        setTimeout(() => {
          if (!connected.value && !connecting.value) {
            connect()
          }
        }, 2000)
      }
    }
    
    websocket.onerror = (error) => {
      console.error('WebSocket error:', error)
      connected.value = false
      connecting.value = false
      message.error('Terminal connection failed')
    }
    
  } catch (error) {
    console.error('Failed to connect:', error)
    connecting.value = false
    message.error(`Connection failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
  }
}

const disconnect = () => {
  if (websocket) {
    websocket.close()
    websocket = null
  }
  connected.value = false
  connecting.value = false
}

const fit = () => {
  fitAddon?.fit()
}

const focus = () => {
  terminal?.focus()
}

const clear = () => {
  terminal?.clear()
}

const search = (term: string) => {
  searchAddon?.findNext(term)
}

// Handle mobile viewport changes
const handleViewportChange = () => {
  if (window.visualViewport) {
    const viewport = window.visualViewport
    const handleResize = () => {
      if (resizeTimeout) {
        clearTimeout(resizeTimeout)
      }
      resizeTimeout = window.setTimeout(() => {
        fitAddon?.fit()
      }, 150)
    }
    
    viewport.addEventListener('resize', handleResize)
    
    return () => {
      viewport.removeEventListener('resize', handleResize)
    }
  }
}

onMounted(async () => {
  await nextTick()
  createTerminal()
  
  // Request notification permission
  if (Notification.permission === 'default') {
    Notification.requestPermission()
  }
  
  // Setup mobile viewport handling
  const cleanupViewport = handleViewportChange()
  
  // Auto-connect after terminal is created
  setTimeout(() => {
    connect()
  }, 100)
  
  onUnmounted(() => {
    cleanupViewport?.()
  })
})

onUnmounted(() => {
  disconnect()
  
  if (resizeTimeout) {
    clearTimeout(resizeTimeout)
  }
  
  if (resizeObserver) {
    resizeObserver.disconnect()
  }
  
  if (terminal) {
    terminal.dispose()
  }
})

// Watch for prop changes
watch(() => props.boxName, () => {
  if (connected.value) {
    disconnect()
    nextTick(() => connect())
  }
})

watch(() => props.theme, () => {
  if (terminal) {
    terminal.options.theme = TERMINAL_THEMES[props.theme]
  }
})

defineExpose({
  connect,
  disconnect,
  fit,
  focus,
  clear,
  search,
  terminal: () => terminal,
  connected: () => connected.value,
  connecting: () => connecting.value
})
</script>

<template>
  <div class="terminal-container">
    <div ref="terminalRef" class="terminal" @click="handleTerminalClick" />
    <div v-if="connecting" class="terminal-overlay">
      <div class="connecting-indicator">
        <div class="spinner" />
        <span>Connecting to {{ boxName }}...</span>
      </div>
    </div>
    <div v-else-if="!connected" class="terminal-overlay">
      <div class="disconnected-indicator">
        <span>Disconnected from {{ boxName }}</span>
        <button @click="connect" class="reconnect-btn">Reconnect</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.terminal-container {
  position: relative;
  width: 100%;
  height: 100%;
  background: var(--terminal-bg, #0f1115);
  border-radius: 8px;
  overflow: hidden;
}

.terminal {
  width: 100%;
  height: 100%;
}

.terminal-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-family: var(--font-mono);
}

.connecting-indicator,
.disconnected-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top: 2px solid white;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.reconnect-btn {
  padding: 8px 16px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.reconnect-btn:hover {
  background: var(--accent-hover);
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .terminal-container {
    border-radius: 0;
  }
  
  .terminal :deep(.xterm-viewport) {
    /* Prevent zoom on iOS */
    touch-action: manipulation;
  }
  
  .terminal :deep(.xterm-screen) {
    /* Ensure proper touch handling */
    touch-action: none;
  }
}

/* High contrast mode */
@media (prefers-contrast: high) {
  .terminal-container {
    border: 2px solid var(--stroke);
  }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
  }
}
</style>
