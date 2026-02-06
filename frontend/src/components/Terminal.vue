<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { SearchAddon } from '@xterm/addon-search'
import { useMessage } from 'naive-ui'
import { useBootstrapStore } from '@/stores/bootstrap'
import { useResponsive } from '@/composables/useResponsive'
import { useI18n } from '@/i18n'

interface Props {
  boxName: string
  sessionName?: string
  directory?: string
  theme?: 'cyberpunk' | 'default' | 'solarized' | 'dracula' | 'nord' | 'monokai'
  fontSize?: number
  fontFamily?: string
  showTitleBar?: boolean
  /** When true, suppress xterm.js keyboard capture — external input handles it */
  externalInput?: boolean
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
  // Monaspace Neon for text, Nerd Fonts for Starship symbols
  fontFamily: '"Monaspace Neon", "CaskaydiaCove Nerd Font", "JetBrains Mono Nerd Font", "FiraCode Nerd Font", "Symbols Nerd Font Mono", "JetBrains Mono", "Fira Code", monospace',
  showTitleBar: true,
  externalInput: false
})

const emit = defineEmits<Emits>()
const message = useMessage()
const bootstrapStore = useBootstrapStore()
const { isMobile, isTouch } = useResponsive()
const { t } = useI18n()

// Computed font size: cap at 12px on mobile
const effectiveFontSize = computed(() => isMobile.value ? Math.min(props.fontSize, 12) : props.fontSize)

const terminalRef = ref<HTMLDivElement>()
const connected = ref(false)
const connecting = ref(false)
const reconnecting = ref(false)
const reconnectAttempts = ref(0)
const maxReconnectAttempts = ref(10)
const mouseMode = ref(true) // tmux mouse mode state
const hasActivity = ref(false) // for glow effect
const isFullscreen = ref(false) // fullscreen mode
const titleBarCollapsed = ref(false) // minimize title bar
let activityTimeout: number | null = null

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

// Fallback clipboard method for when navigator.clipboard fails
const copyWithFallback = (text: string) => {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    document.execCommand('copy')
  } catch (err) {
    console.warn('[Terminal] Fallback copy failed:', err)
  }
  document.body.removeChild(textarea)
}

const copySelection = () => {
  const selection = terminal?.getSelection()
  if (selection) {
    navigator.clipboard.writeText(selection).then(() => {
      message.success(t('terminal.copied'), { duration: 1000 })
    }).catch(() => {
      copyWithFallback(selection)
    })
  }
}

const pasteFromClipboard = async () => {
  try {
    const text = await navigator.clipboard.readText()
    if (text && terminal) {
      // Use xterm's built-in paste method
      terminal.paste(text)
      // Refocus terminal so Enter sends to terminal, not the button
      terminal.focus()
    }
  } catch (err) {
    message.error(t('terminal.paste_failed'))
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

// Toggle tmux mouse mode for clipboard access
const toggleMouseMode = () => {
  if (!websocket || websocket.readyState !== WebSocket.OPEN) return

  mouseMode.value = !mouseMode.value
  const cmd = mouseMode.value
    ? 'tmux set -g mouse on\n'
    : 'tmux set -g mouse off\n'

  websocket.send(textEncoder.encode(cmd))

  message.info(
    mouseMode.value
      ? t('terminal.mouse_on_msg')
      : t('terminal.mouse_off_msg'),
    { duration: 2000 }
  )
}

// Trigger activity indicator
const triggerActivity = () => {
  hasActivity.value = true
  if (activityTimeout) clearTimeout(activityTimeout)
  activityTimeout = window.setTimeout(() => {
    hasActivity.value = false
  }, 150)
}

// Toggle fullscreen mode
const toggleFullscreen = async () => {
  const wrapper = document.querySelector('.terminal-wrapper')
  if (!wrapper) return

  if (!document.fullscreenElement) {
    await wrapper.requestFullscreen()
    isFullscreen.value = true
  } else {
    await document.exitFullscreen()
    isFullscreen.value = false
  }
  // Re-fit terminal after fullscreen change
  nextTick(() => fitAddon?.fit())
}

// Toggle title bar collapse
const toggleTitleBar = () => {
  titleBarCollapsed.value = !titleBarCollapsed.value
  // Re-fit terminal after title bar change
  nextTick(() => fitAddon?.fit())
}

// Tmux control sequences (Ctrl+B prefix)
const TMUX_PREFIX = '\x02' // Ctrl+B

// Kill current tmux window
const tmuxKillWindow = () => {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    // Send Ctrl+B & to kill window (will prompt for confirmation)
    websocket.send(textEncoder.encode(TMUX_PREFIX + '&'))
    terminal?.focus()
  }
}

// Create new tmux window
const tmuxNewWindow = () => {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.send(textEncoder.encode(TMUX_PREFIX + 'c'))
    terminal?.focus()
  }
}

const createTerminal = () => {
  if (!terminalRef.value) return

  const theme = TERMINAL_THEMES[props.theme]
  const isCyberpunk = props.theme === 'cyberpunk'
  
  terminal = new Terminal({
    theme,
    fontSize: effectiveFontSize.value,
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
    rendererType: 'canvas',
    // When external input is active, suppress xterm keyboard capture
    disableStdin: props.externalInput
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
    }
  })

  // Copy on selection (like tmux mouse mode)
  // Note: Uses mouseup event for better clipboard API compatibility
  terminalRef.value?.addEventListener('mouseup', (event) => {
    // Small delay to ensure selection is complete
    setTimeout(() => {
      const selection = terminal?.getSelection()
      if (selection && selection.length > 0) {
        // Use clipboard API with fallback
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(selection).then(() => {
            // Show brief confirmation
            message.success(t('terminal.copied_chars', { n: selection.length }), { duration: 1000 })
          }).catch((err) => {
            console.warn('[Terminal] Clipboard API failed, trying fallback:', err)
            copyWithFallback(selection)
            message.success(t('terminal.copied_chars', { n: selection.length }), { duration: 1000 })
          })
        } else {
          copyWithFallback(selection)
          message.success(t('terminal.copied_chars', { n: selection.length }), { duration: 1000 })
        }
      }
    }, 50)
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
  })
}

const setupResizeObserver = () => {
  if (!terminalRef.value || !fitAddon) return

  resizeObserver = new ResizeObserver((entries) => {
    if (resizeTimeout) {
      clearTimeout(resizeTimeout)
    }
    resizeTimeout = window.setTimeout(() => {
      fitAddon?.fit()
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
    return
  }

  // Check if we've exceeded max attempts
  if (reconnectAttempts.value >= maxReconnectAttempts.value) {
    message.error(t('terminal.max_reconnect'), {
      duration: 5000
    })
    reconnecting.value = false
    return
  }

  const delay = calculateReconnectDelay(reconnectAttempts.value)
  reconnecting.value = true

  reconnectTimeout = window.setTimeout(() => {
    reconnectAttempts.value++
    connect()
  }, delay)
}

const connect = async () => {
  if (connecting.value || connected.value) return

  connecting.value = true
  intentionalDisconnect = false

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

      // Re-fit after connection and ALWAYS send resize to backend
      // This is critical - the PTY defaults to 80x24 and needs the real dimensions
      nextTick(() => {
        fitAddon?.fit()
        // Always send resize after connect, even if dimensions didn't change
        if (terminal && websocket?.readyState === WebSocket.OPEN) {
          const cols = terminal.cols
          const rows = terminal.rows
          websocket.send(JSON.stringify({ op: 'resize', cols, rows }))
        }
      })

      // Show success message - more prominent if recovering from reconnect
      if (reconnectAttempts.value > 0) {
        message.success(t('terminal.reconnected_to', { box: props.boxName }), {
          duration: 3000,
          closable: false
        })
      } else {
        message.success(t('terminal.connected_to', { box: props.boxName }), {
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
        triggerActivity()
        return
      }

      if (event.data instanceof Blob) {
        const buffer = await event.data.arrayBuffer()
        terminal?.write(new Uint8Array(buffer))
        triggerActivity()
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
          // Any other JSON messages we don't recognize - ignore silently
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

      if (event.code === 4403) {
        message.error(t('terminal.auth_failed'), {
          duration: 5000
        })
        reconnecting.value = false
      } else if (event.code === 4401) {
        message.error(t('terminal.auth_denied'), {
          duration: 5000
        })
        reconnecting.value = false
      } else if (event.code === 1000) {
        // Normal closure - don't reconnect
        reconnecting.value = false
      } else {
        // Unexpected disconnect - attempt reconnect with backoff
        scheduleReconnect()
      }
    }
    
    websocket.onerror = (error) => {
      console.error('[Connection] WebSocket error:', error)
      connected.value = false
      connecting.value = false

      // Only show error message if not already reconnecting
      if (!reconnecting.value && reconnectAttempts.value === 0) {
        message.error(t('terminal.connection_failed'))
      }
    }

  } catch (error) {
    console.error('[Connection] Failed to connect:', error)
    connecting.value = false

    // Only show error message if not already reconnecting
    if (!reconnecting.value && reconnectAttempts.value === 0) {
      message.error(t('terminal.connection_failed') + ': ' + (error instanceof Error ? error.message : 'Unknown error'))
    }

    // Schedule reconnect on connection failure
    scheduleReconnect()
  }
}

const disconnect = () => {
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

// Handle mobile viewport changes (keyboard show/hide)
const handleViewportChange = () => {
  if (!window.visualViewport) return undefined

  const viewport = window.visualViewport
  const wrapperEl = () => document.querySelector('.terminal-wrapper') as HTMLElement | null

  const handleResize = () => {
    if (resizeTimeout) {
      clearTimeout(resizeTimeout)
    }
    resizeTimeout = window.setTimeout(() => {
      if (isMobile.value) {
        // On mobile, the visual viewport shrinks when keyboard opens.
        // We need to resize the terminal container to fit the visible area.
        const wrapper = wrapperEl()
        if (wrapper) {
          const viewportHeight = viewport.height
          const wrapperTop = wrapper.getBoundingClientRect().top
          const availableHeight = viewportHeight - wrapperTop
          if (availableHeight > 100) {
            wrapper.style.height = `${availableHeight}px`
          }
        }
        // Scroll the page so terminal input is visible
        viewport.addEventListener('scroll', () => {
          window.scrollTo(0, 0)
        }, { once: true })
      }
      fitAddon?.fit()
    }, 50) // Faster response for keyboard events
  }

  // Reset height when keyboard closes (viewport returns to full height)
  const handleScroll = () => {
    // Prevent browser from scrolling the page behind the keyboard
    if (isMobile.value) {
      window.scrollTo(0, 0)
    }
  }

  viewport.addEventListener('resize', handleResize)
  viewport.addEventListener('scroll', handleScroll)

  return () => {
    viewport.removeEventListener('resize', handleResize)
    viewport.removeEventListener('scroll', handleScroll)
    // Reset any inline height
    const wrapper = wrapperEl()
    if (wrapper) {
      wrapper.style.height = ''
    }
  }
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

// Auto-disable tmux mouse mode on touch devices so native scroll/selection works
watch([isTouch, connected], ([touch, conn]) => {
  if (touch && conn && mouseMode.value) {
    toggleMouseMode()
  }
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

// Refit when responsive font size changes
watch(effectiveFontSize, (newSize) => {
  if (terminal) {
    terminal.options.fontSize = newSize
    nextTick(() => fitAddon?.fit())
  }
})

// Toggle stdin capture when externalInput prop changes
watch(() => props.externalInput, (external) => {
  if (terminal) {
    terminal.options.disableStdin = external
  }
})

// Send data directly to terminal (for tmux commands etc)
const send = (data: string) => {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    websocket.send(textEncoder.encode(data))
  }
}

// Send raw bytes (for control sequences from MobileInputBar)
const sendRaw = (data: string) => {
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
  sendRaw,
  toggleMouseMode,
  toggleFullscreen,
  tmuxNewWindow,
  tmuxKillWindow,
  copySelection,
  pasteFromClipboard,
  terminal: () => terminal,
  connected: () => connected.value,
  connecting: () => connecting.value,
  mouseMode: () => mouseMode.value,
  isFullscreen: () => isFullscreen.value
})
</script>

<template>
  <div class="terminal-wrapper" :class="{ 'has-activity': hasActivity, 'is-fullscreen': isFullscreen }">
    <!-- macOS-style Title Bar -->
    <div v-if="showTitleBar && !titleBarCollapsed" class="terminal-titlebar">
      <div class="titlebar-left">
        <div class="traffic-lights">
          <span
            class="dot dot-close"
            :title="t('terminal.kill_window')"
            @click="tmuxKillWindow"
          />
          <span
            class="dot dot-new"
            :title="t('terminal.new_window')"
            @click="tmuxNewWindow"
          />
          <span
            class="dot dot-maximize"
            :class="{ active: isFullscreen }"
            :title="isFullscreen ? t('terminal.exit_fullscreen') : t('terminal.fullscreen')"
            @click="toggleFullscreen"
          />
        </div>
        <span class="connection-indicator" :class="{ connected, connecting }" />
      </div>
      <div class="titlebar-center">
        <span class="titlebar-text">{{ boxName }} — {{ sessionName }}</span>
      </div>
      <div class="titlebar-right">
        <button
          class="titlebar-btn"
          :class="{ active: !mouseMode }"
          @click="toggleMouseMode"
          :title="mouseMode ? t('terminal.mouse_on') : t('terminal.mouse_off')"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 4l7.07 17 2.51-7.39L21 11.07z"/>
          </svg>
        </button>
        <button class="titlebar-btn" @click="copySelection" :title="t('terminal.copy_selection')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
        </button>
        <button class="titlebar-btn" @click="pasteFromClipboard" :title="t('terminal.paste')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>
            <rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>
          </svg>
        </button>
        <button class="titlebar-btn" @click="clear" :title="t('terminal.clear')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Collapsed Title Bar -->
    <div v-if="showTitleBar && titleBarCollapsed" class="terminal-titlebar-collapsed" @click="toggleTitleBar">
      <span class="connection-indicator" :class="{ connected, connecting }" />
      <span class="collapsed-text">{{ boxName }}</span>
      <span class="expand-hint">{{ t('terminal.click_expand') }}</span>
    </div>

    <!-- Terminal Container with Effects -->
    <div class="terminal-container" :class="{ 'no-titlebar': !showTitleBar || titleBarCollapsed }">
      <!-- CRT Scanlines Overlay -->
      <div class="scanlines" />

      <div
        ref="terminalRef"
        class="terminal"
        @click="handleTerminalClick"
        @contextmenu="handleContextMenu"
      />

      <!-- Connection Overlays -->
      <div v-if="connecting || reconnecting" class="terminal-overlay">
        <div class="connecting-indicator">
          <div class="spinner" />
          <span v-if="reconnecting">
            {{ t('terminal.reconnecting_to', { box: boxName }) }}
            <small v-if="reconnectAttempts > 0">(attempt {{ reconnectAttempts }}/{{ maxReconnectAttempts }})</small>
          </span>
          <span v-else>{{ t('terminal.connecting_to', { box: boxName }) }}</span>
        </div>
      </div>
      <div v-else-if="!connected" class="terminal-overlay">
        <div class="disconnected-indicator">
          <span>{{ t('terminal.disconnected_from', { box: boxName }) }}</span>
          <button @click="connect" class="reconnect-btn">{{ t('terminal.reconnect') }}</button>
        </div>
      </div>

      <!-- Activity Accent Line -->
      <div class="activity-line" :class="{ active: hasActivity }" />
    </div>
  </div>
</template>

<style scoped>
/* =============================================================================
   TERMINAL WRAPPER - The outermost container with activity glow
   ============================================================================= */
.terminal-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 14px;
  overflow: hidden;
  /* Glassmorphism base */
  background: rgba(10, 14, 20, 0.92);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  /* Layered borders for depth */
  border: 1px solid rgba(255, 255, 255, 0.08);
  /* Deep shadow stack */
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.5),
    0 8px 32px rgba(0, 0, 0, 0.5),
    0 2px 8px rgba(0, 0, 0, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  transition: box-shadow 0.3s ease, border-color 0.3s ease;
}

/* Focus state - cyan glow */
.terminal-wrapper:focus-within {
  border-color: rgba(83, 189, 250, 0.25);
  box-shadow:
    0 0 0 1px rgba(83, 189, 250, 0.15),
    0 0 40px rgba(83, 189, 250, 0.1),
    0 8px 32px rgba(0, 0, 0, 0.5),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

/* Activity pulse */
.terminal-wrapper.has-activity {
  border-color: rgba(230, 180, 80, 0.3);
  box-shadow:
    0 0 0 1px rgba(230, 180, 80, 0.2),
    0 0 30px rgba(230, 180, 80, 0.08),
    0 8px 32px rgba(0, 0, 0, 0.5);
}

/* =============================================================================
   TITLE BAR - macOS-style window chrome
   ============================================================================= */
.terminal-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.04) 0%, rgba(255, 255, 255, 0.01) 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  user-select: none;
  flex-shrink: 0;
}

.titlebar-left,
.titlebar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 80px;
}

.titlebar-right {
  justify-content: flex-end;
}

.titlebar-center {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  justify-content: center;
}

.titlebar-text {
  font-size: 12px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.5);
  letter-spacing: 0.02em;
}

/* Traffic light dots - functional */
.traffic-lights {
  display: flex;
  gap: 6px;
}

.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  transition: all 0.15s ease;
  cursor: pointer;
}

/* Close dot (red) - kill tmux window */
.dot-close:hover {
  background: #ff5f56;
  box-shadow: 0 0 6px rgba(255, 95, 86, 0.4);
}

/* New window dot (yellow) */
.dot-new:hover {
  background: #ffbd2e;
  box-shadow: 0 0 6px rgba(255, 189, 46, 0.4);
}

/* Maximize dot (green) - fullscreen */
.dot-maximize:hover,
.dot-maximize.active {
  background: #27c93f;
  box-shadow: 0 0 6px rgba(39, 201, 63, 0.4);
}

/* Connection indicator - small dot next to traffic lights */
.connection-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #ff5f56;
  margin-left: 8px;
  transition: all 0.3s ease;
}

.connection-indicator.connected {
  background: #27c93f;
  animation: breathe 2s ease-in-out infinite;
}

.connection-indicator.connecting {
  background: #ffbd2e;
  animation: pulse 1s ease-in-out infinite;
}

/* Collapsed title bar */
.terminal-titlebar-collapsed {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 20px;
  padding: 0 12px;
  background: rgba(255, 255, 255, 0.02);
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.terminal-titlebar-collapsed:hover {
  background: rgba(255, 255, 255, 0.04);
}

.terminal-titlebar-collapsed .connection-indicator {
  width: 6px;
  height: 6px;
  margin-left: 0;
}

.collapsed-text {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
}

.expand-hint {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.2);
  margin-left: auto;
}

/* Fullscreen mode adjustments */
.terminal-wrapper.is-fullscreen {
  border-radius: 0;
}

.terminal-wrapper.is-fullscreen .terminal-titlebar {
  border-radius: 0;
}

@keyframes breathe {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(39, 201, 63, 0.4);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(39, 201, 63, 0);
    transform: scale(1.05);
  }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* Titlebar buttons */
.titlebar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  transition: all 0.15s ease;
}

.titlebar-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.8);
}

.titlebar-btn.active {
  background: rgba(230, 180, 80, 0.2);
  color: #e6b450;
}

.titlebar-btn.active:hover {
  background: rgba(230, 180, 80, 0.3);
}

/* =============================================================================
   TERMINAL CONTAINER - Main terminal area
   ============================================================================= */
.terminal-container {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 8px 12px 12px;
  overflow: hidden;
}

.terminal-container.no-titlebar {
  border-radius: 14px 14px 0 0;
}

/* =============================================================================
   CRT SCANLINES - Subtle retro effect
   ============================================================================= */
.scanlines {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
  z-index: 10;
  background: repeating-linear-gradient(
    0deg,
    rgba(0, 0, 0, 0.02) 0px,
    rgba(0, 0, 0, 0.02) 1px,
    transparent 1px,
    transparent 2px
  );
  /* Subtle vignette */
  mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, black 60%, transparent 100%);
  -webkit-mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, black 60%, transparent 100%);
}

/* =============================================================================
   TERMINAL - xterm.js container
   ============================================================================= */
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

/* Phosphor text glow - subtle CRT effect */
.terminal :deep(.xterm-rows) {
  text-shadow: 0 0 1px currentColor;
}

/* Make selection more visible and persistent */
.terminal :deep(.xterm-selection) {
  background: rgba(83, 189, 250, 0.35) !important;
}

.terminal :deep(.xterm-selection div) {
  background: rgba(83, 189, 250, 0.35) !important;
}

/* Styled scrollbars */
.terminal :deep(.xterm-viewport)::-webkit-scrollbar {
  width: 8px;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-track {
  background: transparent;
  border-radius: 4px;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-thumb {
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.12) 0%,
    rgba(255, 255, 255, 0.08) 100%
  );
  border-radius: 4px;
  border: 2px solid transparent;
  background-clip: padding-box;
}

.terminal :deep(.xterm-viewport)::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.2) 0%,
    rgba(255, 255, 255, 0.15) 100%
  );
}

/* Firefox scrollbars */
.terminal :deep(.xterm-viewport) {
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.1) transparent;
}

/* =============================================================================
   ACTIVITY LINE - Accent underline that pulses on activity
   ============================================================================= */
.activity-line {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(230, 180, 80, 0.3), transparent);
  opacity: 0;
  transition: opacity 0.15s ease;
}

.activity-line.active {
  opacity: 1;
  animation: line-glow 0.3s ease;
}

@keyframes line-glow {
  0% {
    background: linear-gradient(90deg, transparent, rgba(230, 180, 80, 0.8), transparent);
  }
  100% {
    background: linear-gradient(90deg, transparent, rgba(230, 180, 80, 0.3), transparent);
  }
}

/* =============================================================================
   OVERLAYS - Connection states
   ============================================================================= */
.terminal-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(10, 14, 20, 0.95);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #b3b1ad;
  font-family: 'Monaspace Neon', var(--font-mono);
  z-index: 20;
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
  background: rgba(83, 189, 250, 0.15);
  color: #53bdfa;
  border: 1px solid rgba(83, 189, 250, 0.3);
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  font-family: inherit;
  transition: all 0.2s ease;
}

.reconnect-btn:hover {
  background: rgba(83, 189, 250, 0.25);
  border-color: rgba(83, 189, 250, 0.5);
  box-shadow: 0 0 20px rgba(83, 189, 250, 0.15);
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* =============================================================================
   RESPONSIVE & ACCESSIBILITY
   ============================================================================= */
@media (max-width: 768px) {
  .terminal-wrapper {
    border-radius: 0;
    border: none;
    box-shadow: none;
    background: #0a0e14;
  }

  .terminal-wrapper:focus-within {
    border-color: transparent;
    box-shadow: none;
  }

  .terminal-titlebar {
    height: 28px;
    padding: 0 6px;
  }

  .titlebar-text {
    font-size: 10px;
  }

  .traffic-lights {
    display: none;
  }

  .titlebar-btn {
    width: 28px;
    height: 28px;
  }

  /* Hide copy/paste buttons on mobile — use OS gestures instead */
  .titlebar-right .titlebar-btn:nth-child(2),
  .titlebar-right .titlebar-btn:nth-child(3) {
    display: none;
  }

  .terminal-container {
    padding: 2px 2px 0;
  }

  /* Remove text glow on mobile for perf */
  .terminal :deep(.xterm-rows) {
    text-shadow: none;
  }

  .scanlines {
    display: none;
  }

  .terminal :deep(.xterm-viewport) {
    touch-action: pan-y pinch-zoom;
  }

  .terminal :deep(.xterm-screen) {
    touch-action: pan-y;
  }

  /* Hide scrollbar on mobile to save space */
  .terminal :deep(.xterm-viewport)::-webkit-scrollbar {
    width: 0;
  }

  .terminal :deep(.xterm-viewport) {
    scrollbar-width: none;
  }
}

@media (prefers-contrast: high) {
  .terminal-wrapper {
    border: 2px solid var(--stroke);
  }

  .scanlines {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
  }

  .connection-dot.connected {
    animation: none;
  }

  .connection-dot.connecting {
    animation: none;
  }

  .activity-line.active {
    animation: none;
  }

  .terminal-wrapper,
  .terminal-wrapper:focus-within,
  .terminal-wrapper.has-activity {
    transition: none;
  }
}
</style>
