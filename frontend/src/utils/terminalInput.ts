/**
 * Keyboard and OSC helpers for the xterm.js terminal.
 *
 * xterm.js 5.5 has no kitty-keyboard or modifyOtherKeys support, so Shift+Enter
 * reaches the PTY as a bare CR, identical to Enter. TUIs that want a "newline
 * without submit" (Claude Code, other chat-style CLIs) accept Meta+Enter
 * (ESC CR) for it; VS Code's Claude Code terminal setup binds Shift+Enter to the
 * same sequence. tmux forwards ESC CR unchanged.
 */
export const SHIFT_ENTER_SEQUENCE = '\x1b\r'

type KeyLike = Pick<KeyboardEvent, 'type' | 'key' | 'shiftKey' | 'ctrlKey' | 'altKey' | 'metaKey' | 'isComposing'>

/** The bytes to send for a plain Shift+Enter keydown, or null for any other key. */
export function shiftEnterSequence(event: KeyLike): string | null {
  if (event.type !== 'keydown' || event.key !== 'Enter' || event.isComposing) return null
  if (!event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return null
  return SHIFT_ENTER_SEQUENCE
}

export type OscLinkTarget =
  | { kind: 'web'; url: string }
  | { kind: 'file'; url: string }
  | { kind: 'blocked' }

/**
 * Classify an OSC 8 hyperlink target. Only http(s) opens in a browser tab and
 * file:// resolves through the file viewer; every other scheme (javascript:,
 * data:, custom handlers) is refused because the URI comes from program output.
 */
export function classifyOscLink(uri: string): OscLinkTarget {
  let parsed: URL
  try {
    parsed = new URL(uri)
  } catch {
    return { kind: 'blocked' }
  }
  if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return { kind: 'web', url: parsed.href }
  if (parsed.protocol === 'file:') return { kind: 'file', url: uri }
  return { kind: 'blocked' }
}

/**
 * OSC 52 clipboard bridge for @xterm/addon-clipboard.
 *
 * The addon's default provider only honours selection `c`, but tmux forwards
 * copies with an empty selection parameter, so tmux copy-mode and apps behind
 * tmux never reached the browser clipboard. This provider writes for every
 * selection, skips empty writes (an invalid payload decodes to '' and would
 * otherwise wipe the clipboard), falls back when the async Clipboard API
 * rejects, and never reports clipboard contents back to the program.
 */
export class TerminalClipboardProvider {
  private readonly write: (text: string) => Promise<void>
  private readonly fallback: (text: string) => void

  constructor(write: (text: string) => Promise<void>, fallback: (text: string) => void) {
    this.write = write
    this.fallback = fallback
  }

  readText(): string {
    return ''
  }

  async writeText(_selection: string, text: string): Promise<void> {
    if (!text) return
    try {
      await this.write(text)
    } catch {
      this.fallback(text)
    }
  }
}
