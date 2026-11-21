/**
 * Load the page afresh at `href` (a path of this site). Signing in and signing out go
 * through it, so every socket and store of the page starts from the new state instead
 * of keeping what it opened with.
 */
export function loadPage(href: string): void {
  window.location.assign(href);
}
