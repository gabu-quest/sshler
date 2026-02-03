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
  theme?: 'cyberpunk' | 'default' | 'solarized' | 'dracula' | 'nord' | 'monokai'
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
  theme: 'cyberpunk',
  fontSize: 14,
  fontFamily: '"JetBrains Mono Nerd Font", "Iosevka Nerd Font", "FiraCode Nerd Font", "CaskaydiaCove Nerd Font", "CaskaydiaMono Nerd Font", "SF Mono", Monaco, Consolas, monospace'
})

const emit = defineEmits<Emits>()
const message = useMessage()
const bootstrapStore = useBootstrapStore()

const terminalRef = ref<HTMLDivElement>()
const connected = ref(false)
const connecting = ref(false)
const reconnecting = ref(false)
const reconnectAttempts = ref(0)
const maxReconnectAttempts = ref(10)

const tokenValue = computed(() => bootstrapStore.token || bootstrapStore.payload?.token || null)
const textEncoder = new TextEncoder()

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let searchAddon: SearchAddon | null = null
let websocket: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null
let resizeTimeout: number | null = null
let reconnectTimeout: number | null = null
let intentionalDisconnect = false

const TERMINAL_THEMES = {
  cyberpunk: {
    background: '#0a0e14',      // Deep near-black
    foreground: '#b3b1ad',      // Muted warm gray
    cursor: '#e6b450',          // Amber cursor
    cursorAccent: '#0a0e14',
    selectionBackground: '#273747',
    selectionForeground: '#e6e6e6',
    // ANSI colors tuned for Starship + readability
    black: '#01060e',
    red: '#ea6c73',             // Errors: visible but not screaming
    green: '#91b362',           // Success: calm green
    yellow: '#f9af4f',          // Warnings: readable amber
    blue: '#53bdfa',            // Info: cool blue
    magenta: '#fae994',         // Accent
    cyan: '#90e1c6',            // Secondary
    white: '#c7c7c7',
    brightBlack: '#686868',
    brightRed: '#f07178',
    brightGreen: '#c2d94c',
    brightYellow: '#ffb454',
    brightBlue: '#59c2ff',
    brightMagenta: '#ffee99',
    brightCyan: '#95e6cb',
    brightWhite: '#ffffff',
  },
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

const handleContextMenu = (event: MouseEvent) => {
  event.preventDefault()
  
  const hasSelection = terminal?.hasSelection() || false
  
  if (hasSelection) {
    copySelection()
  } else {
    pasteFromClipboard()
  }
}

const copySelection = () => {
  const selection = terminal?.getSelection()
  if (selection) {
    navigator.clipboard.writeText(selection).then(() => {
      message.success('Copied to clipboard', { duration: 1000 })
    }).catch(() => {
      message.error('Failed to copy')
    })
  }
}

const pasteFromClipboard = async () => {
  try {
    const text = await navigator.clipboard.readText()
    if (text && terminal) {
      // Use xterm's built-in paste method
      terminal.paste(text)
    }
  } catch (err) {
    message.error('Failed to paste from clipboard')
  }
}

const handleKeyDown = (event: KeyboardEvent) => {
  // Smart Ctrl+C handling
  if (event.ctrlKey && event.key === 'c') {
    if (terminal?.hasSelection()) {
      event.preventDefault()
      copySelection()
      return
    }
    // Otherwise let Ctrl+C send interrupt signal as normal
  }
  
  // Ctrl+V for paste
  if (event.ctrlKey && event.key === 'v') {
    event.preventDefault()
    pasteFromClipboard()
  }
}

const createTerminal = () => {
  if (!terminalRef.value) return

  const theme = TERMINAL_THEMES[props.theme]
  const isCyberpunk = props.theme === 'cyberpunk'
  
  terminal = new Terminal({
    theme,
    fontSize: props.fontSize,
    fontFamily: props.fontFamily,
    lineHeight: isCyberpunk ? 1.25 : 1.2,
    letterSpacing: 0,
    cursorBlink: true,
    cursorStyle: isCyberpunk ? 'bar' : 'block',
    cursorInactiveStyle: 'outline',
    allowTransparency: false,
    scrollback: 10000,
    smoothScrollDuration: isCyberpunk ? 150 : 100,
    rightClickSelectsWord: true,
    macOptionIsMeta: true,
    macOptionClickForcesSelection: false,
    convertEol: true,
    allowProposedApi: true,
    // Use canvas renderer for more stable rendering (avoid WebGL glitches)
    rendererType: 'canvas'
  })

  fitAddon = new FitAddon()
  searchAddon = new SearchAddon()
  
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(new WebLinksAddon())
  terminal.loadAddon(searchAddon)

  terminal.open(terminalRef.value)
  
  // Add event listeners
  terminalRef.value.addEventListener('contextmenu', handleContextMenu)
  terminalRef.value.addEventListener('keydown', handleKeyDown)
  
  // Handle bell
  terminal.onBell(() => {
    emit('bell')
    handleBell()
  })

  // Handle data input
  terminal.onData((data) => {
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      // Backend expects binary frames for stdin
      websocket.send(textEncoder.encode(data))
    } else {
      console.log('WebSocket not open, cannot send data. State:', websocket?.readyState)
    }
  })

  // Copy on selection (like tmux mouse mode)
  // Note: Uses mouseup event for better clipboard API compatibility
  terminalRef.value?.addEventListener('mouseup', () => {
    const selection = terminal?.getSelection()
    if (selection && selection.length > 0) {
      // Use clipboard API with fallback
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(selection).then(() => {
          console.log('[Terminal] Selection copied:', selection.length, 'chars')
        }).catch((err) => {
          console.warn('[Terminal] Clipboard API failed, trying fallback:', err)
          copyWithFallback(selection)
        })
      } else {
        copyWithFallback(selection)
      }
    }
  })

  // Handle resize
  terminal.onResize(({ cols, rows }) => {
    emit('resize', { cols, rows })
    if (websocket && websocket.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({
        op: 'resize',
        cols,
        rows
      }))
    }
  })

  // Setup resize observer with throttling
  setupResizeObserver()
  
  // Expose terminal globally for debugging
  ;(window as any).term = terminal
  ;(window as any).fitAddon = fitAddon
  
  // Initial fit - MUST wait for fonts to load for correct cell measurements
  nextTick(async () => {
    // Wait for fonts (critical for Nerd Fonts)
    await (document as any).fonts?.ready
    fitAddon?.fit()
    terminal?.focus()
    
    // Debug: log actual dimensions
    console.log('[Terminal] Initial fit complete - cols:', terminal?.cols, 'rows:', terminal?.rows)
  })
}

const setupResizeObserver = () => {
  if (!terminalRef.value || !fitAddon) return

  resizeObserver = new ResizeObserver((entries) => {
    if (resizeTimeout) {
      clearTimeout(resizeTimeout)
    }
    resizeTimeout = window.setTimeout(() => {
      const entry = entries[0]
      if (entry) {
        console.log('[Terminal] Container size:', entry.contentRect.width, 'x', entry.contentRect.height)
      }
      fitAddon?.fit()
      const dims = fitAddon?.proposeDimensions()
      console.log('[Terminal] After fit - cols:', dims?.cols, 'rows:', dims?.rows)
    }, 150)
  })

  // Observe both the terminal element and its parent container
  resizeObserver.observe(terminalRef.value)
  if (terminalRef.value.parentElement) {
    resizeObserver.observe(terminalRef.value.parentElement)
  }
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

const calculateReconnectDelay = (attempt: number): number => {
  // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
  const baseDelay = 1000
  const maxDelay = 30000
  const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay)
  return delay
}

const scheduleReconnect = () => {
  // Clear any existing reconnect timeout
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout)
    reconnectTimeout = null
  }

  // Don't reconnect if it was an intentional disconnect
  if (intentionalDisconnect) {
    console.log('[Connection] Skipping reconnect - intentional disconnect')
    return
  }

  // Check if we've exceeded max attempts
  if (reconnectAttempts.value >= maxReconnectAttempts.value) {
    console.log('[Connection] Max reconnect attempts reached')
    message.error('Connection lost - max reconnect attempts reached', {
      duration: 5000
    })
    reconnecting.value = false
    return
  }

  const delay = calculateReconnectDelay(reconnectAttempts.value)
  reconnecting.value = true

  console.log(`[Connection] Scheduling reconnect attempt ${reconnectAttempts.value + 1}/${maxReconnectAttempts.value} in ${delay}ms`)

  reconnectTimeout = window.setTimeout(() => {
    reconnectAttempts.value++
    connect()
  }, delay)
}

const connect = async () => {
  if (connecting.value || connected.value) return

  connecting.value = true
  intentionalDisconnect = false

  console.log(`[Connection] Connecting to ${props.boxName} (attempt ${reconnectAttempts.value + 1})`)

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
      reconnecting.value = false
      emit('connected')

      console.log(`[Connection] Connected to ${props.boxName}`)
      
      // Re-fit after connection and ALWAYS send resize to backend
      // This is critical - the PTY defaults to 80x24 and needs the real dimensions
      nextTick(() => {
        fitAddon?.fit()
        // Always send resize after connect, even if dimensions didn't change
        if (terminal && websocket?.readyState === WebSocket.OPEN) {
          const cols = terminal.cols
          const rows = terminal.rows
          console.log(`[Connection] Sending initial resize: ${cols}x${rows}`)
          websocket.send(JSON.stringify({ op: 'resize', cols, rows }))
        }
      })

      // Show success message - more prominent if recovering from reconnect
      if (reconnectAttempts.value > 0) {
        message.success(`Reconnected to ${props.boxName}`, {
          duration: 3000,
          closable: false
        })
      } else {
        message.success(`Connected to ${props.boxName}`, {
          duration: 2000,
          closable: false
        })
      }

      // Reset reconnect attempts on successful connection
      reconnectAttempts.value = 0

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
      console.log(`[Connection] WebSocket closed: code=${event.code} reason="${event.reason}"`)

      if (event.code === 4403) {
        message.error('Authentication failed - please refresh the page', {
          duration: 5000
        })
        reconnecting.value = false
      } else if (event.code === 4401) {
        message.error('Authorization failed', {
          duration: 5000
        })
        reconnecting.value = false
      } else if (event.code === 1000) {
        // Normal closure - don't reconnect
        console.log('[Connection] Normal close - not reconnecting')
        reconnecting.value = false
      } else {
        // Unexpected disconnect - attempt reconnect with backoff
        console.log('[Connection] Unexpected disconnect - scheduling reconnect')
        scheduleReconnect()
      }
    }
    
    websocket.onerror = (error) => {
      console.error('[Connection] WebSocket error:', error)
      connected.value = false
      connecting.value = false

      // Only show error message if not already reconnecting
      if (!reconnecting.value && reconnectAttempts.value === 0) {
        message.error('Terminal connection failed')
      }
    }

  } catch (error) {
    console.error('[Connection] Failed to connect:', error)
    connecting.value = false

    // Only show error message if not already reconnecting
    if (!reconnecting.value && reconnectAttempts.value === 0) {
      message.error(`Connection failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }

    // Schedule reconnect on connection failure
    scheduleReconnect()
  }
}

const disconnect = () => {
  console.log('[Connection] Intentional disconnect')
  intentionalDisconnect = true
  reconnecting.value = false
  reconnectAttempts.value = 0

  // Clear any pending reconnect timeout
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout)
    reconnectTimeout = null
  }

  if (websocket) {
    websocket.close(1000) // Normal closure
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
  return undefined
}

// Store cleanup function for viewport
let cleanupViewport: (() => void) | undefined

onMounted(async () => {
  await nextTick()
  createTerminal()
  
  // Request notification permission
  if (Notification.permission === 'default') {
    Notification.requestPermission()
  }
  
  // Setup mobile viewport handling
  cleanupViewport = handleViewportChange()
  
  // Auto-connect after terminal is created
  setTimeout(() => {
    connect()
  }, 100)
})

onUnmounted(() => {
  // Cleanup viewport handler
  cleanupViewport?.()
  
  disconnect()

  if (resizeTimeout) {
    clearTimeout(resizeTimeout)
  }

  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout)
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

// Send data directly to terminal (for tmux commands etc)
const send = (data: string) => {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.send(textEncoder.encode(data))
  }
}

defineExpose({
  connect,
  disconnect,
  fit,
  focus,
  clear,
  search,
  send,
  terminal: () => terminal,
  connected: () => connected.value,
  connecting: () => connecting.value
})
</script>

<template>
  <div class="terminal-container">
    <div 
      ref="terminalRef" 
      class="terminal" 
      @click="handleTerminalClick"
      @contextmenu="handleContextMenu"
    />
    <div v-if="connecting || reconnecting" class="terminal-overlay">
      <div class="connecting-indicator">
        <div class="spinner" />
        <span v-if="reconnecting">
          Reconnecting to {{ boxName }}...
          <small v-if="reconnectAttempts > 0">(attempt {{ reconnectAttempts }}/{{ maxReconnectAttempts }})</small>
        </span>
        <span v-else>Connecting to {{ boxName }}...</span>
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
  min-width: 0;  /* Critical for flex layouts */
  min-height: 0;
  background: var(--terminal-bg, #0a0e14);
  border-radius: 14px;
  overflow: hidden;
  /* Cyberpunk depth */
  box-shadow: 
    0 0 0 1px rgba(255, 255, 255, 0.05),
    0 4px 24px rgba(0, 0, 0, 0.4),
    inset 0 1px 0 rgba(255, 255, 255, 0.02);
  /* Inner padding for breathing room */
  padding: 10px 12px;
}

/* Focus glow */
.terminal-container:focus-within {
  box-shadow: 
    0 0 0 1px rgba(83, 189, 250, 0.3),
    0 0 20px rgba(83, 189, 250, 0.08),
    0 4px 24px rgba(0, 0, 0, 0.4);
}

.terminal {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}

/* Force xterm internal elements to fill the container */
.terminal :deep(.xterm) {
  width: 100% !important;
  height: 100% !important;
}

.terminal :deep(.xterm-viewport) {
  width: 100% !important;
}

.terminal :deep(.xterm-screen) {
  width: 100% !important;
}

/* Ultra-minimal scrollbars */
.terminal :deep(.xterm-viewport)::-webkit-scrollbar {
  width: 6px;
  background: transparent;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-track {
  background: transparent;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.2);
}

/* Firefox scrollbars */
.terminal :deep(.xterm-viewport) {
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.1) transparent;
}

.terminal-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(10, 14, 20, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #b3b1ad;
  font-family: var(--font-mono);
  border-radius: 14px;
}

.connecting-indicator,
.disconnected-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.connecting-indicator small {
  display: block;
  font-size: 12px;
  opacity: 0.7;
  margin-top: 4px;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(230, 180, 80, 0.2);
  border-top: 2px solid #e6b450;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.reconnect-btn {
  padding: 8px 16px;
  background: rgba(83, 189, 250, 0.2);
  color: #53bdfa;
  border: 1px solid rgba(83, 189, 250, 0.3);
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s ease;
}

.reconnect-btn:hover {
  background: rgba(83, 189, 250, 0.3);
  border-color: rgba(83, 189, 250, 0.5);
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Mobile optimizations */
@media (max-width: 768px) {
  .terminal-container {
    border-radius: 0;
    padding: 6px 8px;
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
