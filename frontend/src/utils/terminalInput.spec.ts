import { describe, it, expect } from 'vitest'
import { shiftEnterSequence, classifyOscLink, SHIFT_ENTER_SEQUENCE, TerminalClipboardProvider } from './terminalInput'

const key = (over: Partial<KeyboardEvent>) => ({
  type: 'keydown',
  key: 'Enter',
  shiftKey: false,
  ctrlKey: false,
  altKey: false,
  metaKey: false,
  isComposing: false,
  ...over,
})

describe('shiftEnterSequence', () => {
  it('sends ESC CR for Shift+Enter', () => {
    expect(shiftEnterSequence(key({ shiftKey: true }))).toBe('\x1b\r')
    expect(SHIFT_ENTER_SEQUENCE).toBe('\x1b\r')
  })

  it('leaves plain Enter to xterm', () => {
    expect(shiftEnterSequence(key({}))).toBeNull()
  })

  it('leaves Shift+Enter with another modifier to xterm', () => {
    expect(shiftEnterSequence(key({ shiftKey: true, ctrlKey: true }))).toBeNull()
    expect(shiftEnterSequence(key({ shiftKey: true, altKey: true }))).toBeNull()
    expect(shiftEnterSequence(key({ shiftKey: true, metaKey: true }))).toBeNull()
  })

  it('ignores keyup and IME composition', () => {
    expect(shiftEnterSequence(key({ shiftKey: true, type: 'keyup' }))).toBeNull()
    expect(shiftEnterSequence(key({ shiftKey: true, isComposing: true }))).toBeNull()
  })

  it('ignores other keys with Shift', () => {
    expect(shiftEnterSequence(key({ shiftKey: true, key: 'A' }))).toBeNull()
  })
})

describe('classifyOscLink', () => {
  it('opens http and https', () => {
    expect(classifyOscLink('https://example.com/a?b=1')).toEqual({ kind: 'web', url: 'https://example.com/a?b=1' })
    expect(classifyOscLink('http://localhost:8822/app')).toEqual({ kind: 'web', url: 'http://localhost:8822/app' })
  })

  it('routes file URLs to the file viewer', () => {
    expect(classifyOscLink('file:///home/u/notes.md')).toEqual({ kind: 'file', url: 'file:///home/u/notes.md' })
  })

  it('refuses script, data and custom schemes', () => {
    expect(classifyOscLink('javascript:alert(1)')).toEqual({ kind: 'blocked' })
    expect(classifyOscLink('data:text/html,<b>x</b>')).toEqual({ kind: 'blocked' })
    expect(classifyOscLink('vscode://file/x')).toEqual({ kind: 'blocked' })
  })

  it('refuses text that is not a URL', () => {
    expect(classifyOscLink('not a url')).toEqual({ kind: 'blocked' })
  })
})

describe('TerminalClipboardProvider', () => {
  const make = (writeImpl: (t: string) => Promise<void>) => {
    const written: string[] = []
    const fallen: string[] = []
    const provider = new TerminalClipboardProvider(
      (t) => { written.push(t); return writeImpl(t) },
      (t) => { fallen.push(t) },
    )
    return { provider, written, fallen }
  }

  it('writes tmux copies that carry an empty selection parameter', async () => {
    const { provider, written, fallen } = make(() => Promise.resolve())
    await provider.writeText('', 'from tmux')
    expect(written).toEqual(['from tmux'])
    expect(fallen).toEqual([])
  })

  it('writes the clipboard selection', async () => {
    const { provider, written } = make(() => Promise.resolve())
    await provider.writeText('c', 'from app')
    expect(written).toEqual(['from app'])
  })

  it('does not clear the clipboard on an empty payload', async () => {
    const { provider, written, fallen } = make(() => Promise.resolve())
    await provider.writeText('c', '')
    expect(written).toEqual([])
    expect(fallen).toEqual([])
  })

  it('falls back when the Clipboard API rejects', async () => {
    const { provider, fallen } = make(() => Promise.reject(new Error('NotAllowedError')))
    await provider.writeText('c', 'needs fallback')
    expect(fallen).toEqual(['needs fallback'])
  })

  it('never reports clipboard contents to the program', () => {
    const { provider } = make(() => Promise.resolve())
    expect(provider.readText()).toBe('')
  })
})
