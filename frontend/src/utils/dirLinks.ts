/** Links to a folder's terminal and file browser, shared by every page that lists folders. */

/** Windows folders (`C:\\Users\\x`) become forward-slash form, as sshler's local paths are. */
export function normalizeDir(path: string): string {
  return /^[A-Za-z]:[\\/]/.test(path) ? path.replace(/\\/g, "/") : path;
}

export function terminalUrl(box: string, dir: string): string {
  return `/app/terminal?box=${encodeURIComponent(box)}&dir=${encodeURIComponent(dir)}`;
}

export function filesUrl(box: string, path: string): string {
  return `/app/files?box=${encodeURIComponent(box)}&path=${encodeURIComponent(path)}`;
}

/** A plain left click opens a background tab (focus stays here). Any other button or a
 *  click with Ctrl, Meta, Shift or Alt is the browser's own (new tab, window, download). */
export function openInBackgroundTab(event: MouseEvent, url: string): void {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  window.open(url, "_blank");
  // Try to keep focus on current tab (not always reliable)
  window.focus();
}
