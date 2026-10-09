import { describe, expect, it } from 'vitest'
import { Terminal } from '@xterm/xterm'
import { PTY_STREAM_OPTIONS } from './terminalOptions'

const write = (term: Terminal, data: string) => new Promise<void>(resolve => term.write(data, resolve))

describe('PTY_STREAM_OPTIONS', () => {
  it('keeps the column on a bare line feed, as tmux redraws expect', async () => {
    const term = new Terminal({ ...PTY_STREAM_OPTIONS, cols: 20, rows: 5, allowProposedApi: true })
    // Cursor to row 2, column 10 (1-based), then LF and a character.
    await write(term, '\x1b[2;10H\nX')
    const row = term.buffer.active.getLine(2)!.translateToString(true)
    expect(row).toBe('         X')
    term.dispose()
  })
})
