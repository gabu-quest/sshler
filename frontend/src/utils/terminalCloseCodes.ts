/**
 * Terminal WebSocket close-code policy.
 *
 * Maps a WebSocket close code to what the UI should do: which (if any) i18n
 * message to toast, whether to attempt a reconnect, and the toast duration.
 *
 * Terminal.vue's `onclose` handler acts on this policy only, so the decision is
 * pure and unit-testable.
 *
 * Server-side close codes (see sshler/webapp.py `/ws/term`):
 *   4400  the session name was refused (reason "Invalid session name"), or the
 *         `shell` value was (reason "Invalid shell")
 *   4401  the session ended, or none came with the socket: the page goes to the
 *         sign-in form (`signInAfterSocketClose` in api/http.ts), with no toast
 *   4403  invalid CSRF token
 *   4404  the box is not known to the server
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

/** The reason the server gives when it refuses a `shell` value. */
const INVALID_SHELL_REASON = "Invalid shell";

export function classifyTerminalCloseCode(code: number, reason = ""): TerminalClosePolicy {
  switch (code) {
    case 4400:
      // A refused value is refused again on every attempt.
      if (reason === INVALID_SHELL_REASON) {
        return { messageKey: "terminal.shell_refused", reconnect: false, duration: 6000 };
      }
      return { messageKey: "terminal.session_name_refused", reconnect: false, duration: 6000 };
    case 4403:
      return { messageKey: "terminal.auth_failed", reconnect: false, duration: 5000 };
    case 4401:
      // The sign-in form is the message; after sign-in the page loads afresh and reconnects.
      return { messageKey: null, reconnect: false };
    case 4404:
      return { messageKey: "terminal.unknown_box", reconnect: false, duration: 6000 };
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
