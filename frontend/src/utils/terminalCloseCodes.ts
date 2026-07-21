/**
 * Terminal WebSocket close-code policy.
 *
 * Maps a WebSocket close code to what the UI should do: which (if any) i18n
 * message to toast, whether to attempt a reconnect, and the toast duration.
 *
 * Extracted from Terminal.vue's `onclose` handler so the decision is pure and
 * unit-testable (the handler itself is a closure inside `connect()` that pulls
 * in xterm, refs, and the message API — untestable in isolation).
 *
 * Server-side close codes (see sshler/webapp.py `/ws/term`):
 *   4401  unauthorized (no valid session/credentials)
 *   4403  invalid CSRF token
 *   4502  WSL selected but no distro installed
 *   4503  pywinpty missing in the server environment
 *   4504  too many concurrent terminal sessions (ConPTY cap hit)
 *   1000  normal closure
 *   (anything else) unexpected disconnect → reconnect with backoff
 */
export interface TerminalClosePolicy {
  /** i18n key to toast, or null for no message. */
  messageKey: string | null;
  /** Whether the client should attempt to reconnect. */
  reconnect: boolean;
  /** Toast duration in ms (only meaningful when messageKey is set). */
  duration?: number;
}

export function classifyTerminalCloseCode(code: number): TerminalClosePolicy {
  switch (code) {
    case 4403:
      return { messageKey: "terminal.auth_failed", reconnect: false, duration: 5000 };
    case 4401:
      return { messageKey: "terminal.auth_denied", reconnect: false, duration: 5000 };
    case 4502:
      return { messageKey: "terminal.wsl_not_installed", reconnect: false, duration: 6000 };
    case 4503:
      return { messageKey: "terminal.pywinpty_missing", reconnect: false, duration: 8000 };
    case 4504:
      // ConPTY session cap hit. Reconnecting would immediately re-hit the cap,
      // so stop and tell the user to close a terminal first.
      return { messageKey: "terminal.too_many_terminals", reconnect: false, duration: 8000 };
    case 1000:
      // Normal closure - don't reconnect, don't toast.
      return { messageKey: null, reconnect: false };
    default:
      // Unexpected disconnect - attempt reconnect with backoff.
      return { messageKey: null, reconnect: true };
  }
}
