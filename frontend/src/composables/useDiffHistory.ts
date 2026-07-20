// localStorage-backed history of recent diff notebooks. Client-side only — the
// server has no notion of "notebooks." Capped at 10 entries; dedupes by b64.

const STORAGE_KEY = "sshler:diff:history";
const MAX_ENTRIES = 10;

export interface HistoryEntry {
  b64: string;
  label: string;
  savedAt: number;
}

interface StoredShape {
  v: number;
  notebooks: HistoryEntry[];
}

const VERSION = 1;

function safeRead(): HistoryEntry[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredShape;
    if (parsed && parsed.v === VERSION && Array.isArray(parsed.notebooks)) {
      return parsed.notebooks.filter(
        (e) => typeof e?.b64 === "string" && typeof e?.label === "string" && typeof e?.savedAt === "number",
      );
    }
  } catch {
    // fall through
  }
  return [];
}

function safeWrite(notebooks: HistoryEntry[]) {
  if (typeof localStorage === "undefined") return;
  try {
    const payload: StoredShape = { v: VERSION, notebooks };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // localStorage may be full or disabled — best effort.
  }
}

export function useDiffHistory() {
  function list(): HistoryEntry[] {
    return safeRead();
  }

  function record(b64: string, label: string): void {
    if (!b64) return;
    const now = Date.now();
    const existing = safeRead();
    // Drop any existing entry with the same b64 (dedupe), then push to the front.
    const deduped = existing.filter((e) => e.b64 !== b64);
    const next: HistoryEntry[] = [{ b64, label, savedAt: now }, ...deduped].slice(0, MAX_ENTRIES);
    safeWrite(next);
  }

  function remove(b64: string): void {
    const next = safeRead().filter((e) => e.b64 !== b64);
    safeWrite(next);
  }

  function clear(): void {
    safeWrite([]);
  }

  return { list, record, remove, clear };
}
