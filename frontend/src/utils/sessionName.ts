/**
 * Session naming + coloring.
 *
 * A tmux session name is the *key* tmux uses to attach-or-create.
 *
 * TMUX boxes: we match the `ts` CLI's naming EXACTLY — the directory basename
 * with only `.`/`:` replaced by `_` (hyphens preserved), no hash. This makes
 * sshler and `ts` share one tmux session per directory, so if sshler is down
 * the user can `ts` into the same session from a plain terminal. (Consequence:
 * two different dirs with the same basename share a session — `ts`'s own
 * behavior; accepted for parity.) Keep this byte-identical to `ts_session_name`
 * in sshler/tmux.py.
 *
 * Remote sessions use `tmux -L ts-<name>` on the remote host too, so running
 * `ts` after a normal `ssh host` attaches to the exact same session.
 */
import { fnv1aHash } from './emoji-favicon'

/** A drive-letter (`C:\`, `C:/`) or UNC (`\\server\`) path. */
const WINDOWS_PATH = /^(?:[A-Za-z]:[/\\]|\\\\)/

/**
 * Deterministic tmux session name for a directory.
 *
 * @param directory Absolute path (or `~`) the terminal opens in.
 * @param _boxName  Box the terminal lives on. Unused: every box uses `ts` naming;
 *                  kept so callers need not change.
 */
export function generateSessionName(directory: string, _boxName?: string): string {
  const dir = directory && directory !== '~' ? directory : '~'
  // `\` separates segments only in a Windows-looking path; in a POSIX path it
  // is an ordinary character, as in ts_session_name.
  const pathParts = dir.split(WINDOWS_PATH.test(dir) ? /[/\\]/ : '/').filter(Boolean)

  // ts rule: `.`/`:` -> `_`. Then the same tmux-safe filter the backend's
  // /ws/term applies (PathValidator.sanitize_session_name) so the name we
  // compute == the session the backend actually opens == what `ts` uses (for
  // clean names — hyphens are preserved by both). Kept byte-identical to
  // ts_session_name in sshler/tmux.py, including the home mapping.
  let base = pathParts[pathParts.length - 1] || 'home'
  if (base === '~' || base === '.' || base === '..') base = 'home'
  // `.`/`:` gone first (also keeps them out of tmux `session:window.pane` targets).
  // Unicode-aware (\p{L}\p{N}, per code point) to equal Python's str.isalnum().
  return base.replace(/[.:]/g, '_').replace(/[^\p{L}\p{N}_-]/gu, '_') || 'home'
}

/**
 * Last path segment of a path, handling both POSIX (/) and Windows (\)
 * separators. Used for human-readable directory labels.
 */
export function lastPathSegment(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean)
  return parts[parts.length - 1] || ''
}

/**
 * Distinct, high-contrast palette for per-session coloring. Hand-picked hues
 * that stay legible on both light and dark chrome.
 */
const SESSION_COLORS = [
  '#e6584d', // red
  '#e8893d', // orange
  '#d9b13b', // amber
  '#67ad4b', // green
  '#3bb2a6', // teal
  '#3d8fe8', // blue
  '#6b6be6', // indigo
  '#a85fd6', // purple
  '#d65fa8', // magenta
  '#8a6d4b', // brown
  '#5a8a8a', // slate-teal
  '#c0573d', // rust
]

/**
 * Deterministic color for a session, keyed by whatever stable string identifies
 * it (session name, or `box:path`). Same key → same color, every render.
 */
export function getColorForSession(key: string): string {
  if (!key) return '#888888'
  return SESSION_COLORS[fnv1aHash(key) % SESSION_COLORS.length]! // modulo keeps the index in range
}
