import type { ITerminalOptions } from '@xterm/xterm'

// The stream comes from a pty (tmux), which already turns "\n" into "\r\n" where a program
// means a new line. A bare LF from tmux means "down one row, same column": convertEol would
// send the cursor to column 0 and leave stale characters on full-screen redraws.
export const PTY_STREAM_OPTIONS: ITerminalOptions = {
  convertEol: false,
}
