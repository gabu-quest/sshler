import { ref } from "vue";
import { defineStore } from "pinia";

/**
 * Browser-side terminal tabs, scoped per (box, directory).
 *
 * Each tab is one terminal session: a `(sessionName, directory, shell)` triple
 * plus a stable `id` used as the Vue render key so its `<Terminal>` instance is
 * never remounted by tab reordering. Tab params are immutable after creation —
 * "changing" a tab means opening a new one (browser-tab semantics).
 *
 * Scoping is per (box, directory): every directory on a box gets its own
 * independent tab strip. Navigating to a directory swaps in that directory's
 * tabs; it never strands the user in a leftover tab from another directory.
 * Store methods take an opaque `scope` string built by {@link scopeKey} so the
 * store itself stays agnostic to what a scope is made of.
 *
 * This is purely a frontend convenience: the backend already supports multiple
 * concurrent sessions per box (each tab is just another `/ws/term` connection
 * with a distinct `session` name). Closing a tab only detaches the websocket;
 * the underlying shell keeps running and is reachable from the SessionSwitcher.
 *
 * State is persisted per scope in localStorage so tabs survive a reload.
 */
export interface TerminalTab {
  id: string;
  sessionName: string;
  directory: string;
  shell: string;
  label: string;
}

interface PersistedTabs {
  tabs: TerminalTab[];
  activeTabId: string;
}

const STORAGE_PREFIX = "sshler.terminal_tabs.";

/**
 * Build the opaque scope key for a (box, directory) pair. The key is only ever
 * constructed and looked up whole (never parsed back), so the `::` separator is
 * safe even though Windows paths contain colons.
 */
export function scopeKey(box: string, directory: string): string {
  return `${box}::${directory || "~"}`;
}

function storageKey(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`;
}

/** Last path component, handling both POSIX (/) and Windows (\) separators. */
export function lastPathSegment(path: string): string {
  const parts = path.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] || "";
}

/**
 * Derive a tmux/session-safe name from a directory path.
 *
 * Matches the `ts` CLI's naming so sshler and `ts here` land on the SAME
 * session for the same directory: dashes are kept (ts only collapses `.`/`:`),
 * everything else becomes `_`. Leading/trailing `_`/`-` are trimmed so the
 * name can never be mistaken for a tmux/CLI flag.
 */
export function generateSessionName(directory: string): string {
  if (!directory || directory === "~") return "home";
  const dirName = lastPathSegment(directory) || "root";
  const sanitized = dirName.replace(/[^a-zA-Z0-9-]/g, "_");
  return sanitized.replace(/^[-_]+|[-_]+$/g, "") || "root";
}

/** Return `base`, or `base-2`, `base-3`, … if `base` is already taken. */
function uniqueSessionName(existing: Iterable<string>, base: string): string {
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i++;
  return `${base}-${i}`;
}

function makeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function readStored(scope: string): PersistedTabs | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(storageKey(scope));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.tabs)) return null;
    const tabs: TerminalTab[] = parsed.tabs
      .filter(
        (t: unknown): t is TerminalTab =>
          !!t &&
          typeof (t as TerminalTab).id === "string" &&
          typeof (t as TerminalTab).sessionName === "string",
      )
      .map((t: TerminalTab) => ({
        id: t.id,
        sessionName: t.sessionName,
        directory: typeof t.directory === "string" ? t.directory : "~",
        shell: typeof t.shell === "string" ? t.shell : "",
        label: typeof t.label === "string" && t.label ? t.label : t.sessionName,
      }));
    if (tabs.length === 0) return null;
    const activeTabId =
      typeof parsed.activeTabId === "string" && tabs.some((t) => t.id === parsed.activeTabId)
        ? parsed.activeTabId
        : tabs[0]!.id;
    return { tabs, activeTabId };
  } catch {
    return null; // corrupt JSON — fall back to a fresh default tab
  }
}

function writeStored(scope: string, tabs: TerminalTab[], activeTabId: string): void {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(storageKey(scope), JSON.stringify({ tabs, activeTabId }));
  } catch {
    /* quota / private mode — ignore */
  }
}

export const useTerminalTabsStore = defineStore("terminalTabs", () => {
  const tabsByScope = ref<Record<string, TerminalTab[]>>({});
  const activeTabIdByScope = ref<Record<string, string>>({});

  function persist(scope: string): void {
    writeStored(scope, tabsByScope.value[scope] ?? [], activeTabIdByScope.value[scope] ?? "");
  }

  function setTabs(scope: string, list: TerminalTab[]): void {
    tabsByScope.value = { ...tabsByScope.value, [scope]: list };
  }

  function setActive(scope: string, id: string): void {
    activeTabIdByScope.value = { ...activeTabIdByScope.value, [scope]: id };
  }

  function tabs(scope: string): TerminalTab[] {
    return tabsByScope.value[scope] ?? [];
  }

  function activeTabId(scope: string): string | null {
    return activeTabIdByScope.value[scope] ?? null;
  }

  function activeTab(scope: string): TerminalTab | null {
    const id = activeTabIdByScope.value[scope];
    if (!id) return null;
    return (tabsByScope.value[scope] ?? []).find((t) => t.id === id) ?? null;
  }

  function makeTab(
    opts: { directory: string; shell: string; sessionName?: string; label?: string },
    existing: TerminalTab[],
  ): TerminalTab {
    const directory = opts.directory || "~";
    const base = opts.sessionName?.trim() || generateSessionName(directory);
    const sessionName = uniqueSessionName(
      existing.map((t) => t.sessionName),
      base,
    );
    return {
      id: makeId(),
      sessionName,
      directory,
      shell: opts.shell || "",
      label: opts.label?.trim() || sessionName,
    };
  }

  /** Hydrate tabs for a scope from storage, or seed a single default tab. */
  function loadScope(scope: string, seed: { directory: string; shell: string }): void {
    const stored = readStored(scope);
    if (stored) {
      setTabs(scope, stored.tabs);
      setActive(scope, stored.activeTabId);
      return;
    }
    const tab = makeTab(seed, []);
    setTabs(scope, [tab]);
    setActive(scope, tab.id);
    persist(scope);
  }

  function addTab(
    scope: string,
    opts: {
      directory: string;
      shell: string;
      sessionName?: string;
      label?: string;
      activate?: boolean;
    },
  ): TerminalTab {
    const list = tabsByScope.value[scope] ?? [];
    const tab = makeTab(opts, list);
    setTabs(scope, [...list, tab]);
    if (opts.activate !== false) setActive(scope, tab.id);
    persist(scope);
    return tab;
  }

  /** Close a tab; activate a neighbour, or recreate a default if it was the last. */
  function closeTab(scope: string, id: string): void {
    const list = tabsByScope.value[scope] ?? [];
    const idx = list.findIndex((t) => t.id === id);
    if (idx === -1) return;
    const next = list.filter((t) => t.id !== id);
    if (next.length === 0) {
      const closed = list[idx]!;
      const tab = makeTab({ directory: closed.directory, shell: closed.shell }, []);
      setTabs(scope, [tab]);
      setActive(scope, tab.id);
      persist(scope);
      return;
    }
    setTabs(scope, next);
    if (activeTabIdByScope.value[scope] === id) {
      const neighbor = next[Math.min(idx, next.length - 1)]!;
      setActive(scope, neighbor.id);
    }
    persist(scope);
  }

  function activateTab(scope: string, id: string): void {
    const list = tabsByScope.value[scope] ?? [];
    if (!list.some((t) => t.id === id)) return;
    setActive(scope, id);
    persist(scope);
  }

  /** Rename only the display label (does not touch the live session). */
  function renameTab(scope: string, id: string, label: string): void {
    const trimmed = label.trim();
    if (!trimmed) return;
    const list = tabsByScope.value[scope] ?? [];
    setTabs(
      scope,
      list.map((t) => (t.id === id ? { ...t, label: trimmed } : t)),
    );
    persist(scope);
  }

  /**
   * Open (or re-activate) a tab for a known session — used by the SessionSwitcher.
   * Reuses the exact session name (no suffixing) since the user is intentionally
   * reattaching to that session.
   */
  function openOrActivateSession(
    scope: string,
    opts: { sessionName: string; directory: string; shell: string },
  ): TerminalTab {
    const list = tabsByScope.value[scope] ?? [];
    const existing = list.find((t) => t.sessionName === opts.sessionName);
    if (existing) {
      activateTab(scope, existing.id);
      return existing;
    }
    const tab: TerminalTab = {
      id: makeId(),
      sessionName: opts.sessionName,
      directory: opts.directory || "~",
      shell: opts.shell || "",
      label: opts.sessionName,
    };
    setTabs(scope, [...list, tab]);
    setActive(scope, tab.id);
    persist(scope);
    return tab;
  }

  return {
    tabsByScope,
    activeTabIdByScope,
    tabs,
    activeTabId,
    activeTab,
    loadScope,
    addTab,
    closeTab,
    activateTab,
    renameTab,
    openOrActivateSession,
  };
});
