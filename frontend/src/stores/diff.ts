import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { gitShow } from "@/api/http";
import type { SideSpec } from "@/utils/diffCommandParser";

// M2: multi-cell diff notebook. Each cell is one (left ↔ right) file pair;
// the page renders the cells vertically. URL state is base64-JSON in `?n=`
// (versioned). Old M1 single-cell URLs (?lb=…&rb=…) are migrated transparently
// so existing share-links keep working.

export type DiffSide = SideSpec;

export type SideStatus = "idle" | "loading" | "loaded" | "missing" | "error";

export interface SideState {
  config: DiffSide;
  content: string;
  status: SideStatus;
  error: string | null;
  truncated: boolean;
}

export type CellStatus = "idle" | "loading" | "ready" | "error" | "binary";

export interface DiffCellState {
  id: string; // stable client-side id for v-for keys
  left: SideState;
  right: SideState;
  status: CellStatus;
  error: string | null;
}

export interface DefaultRepo {
  box: string;
  directory: string;
}

const EMPTY_SIDE: DiffSide = { box: "", directory: "", ref: "", path: "" };

function emptySideState(seed?: Partial<DiffSide>): SideState {
  return {
    config: { ...EMPTY_SIDE, ...(seed ?? {}) },
    content: "",
    status: "idle",
    error: null,
    truncated: false,
  };
}

let _cellIdCounter = 0;
function newCellId(): string {
  _cellIdCounter += 1;
  return `c${_cellIdCounter}-${Math.random().toString(36).slice(2, 7)}`;
}

function emptyCellState(left?: Partial<DiffSide>, right?: Partial<DiffSide>): DiffCellState {
  return {
    id: newCellId(),
    left: emptySideState(left),
    right: emptySideState(right),
    status: "idle",
    error: null,
  };
}

// Server-side truncation cap from sshler/api/git.py (MAX_SHOW_BYTES). We use it to
// detect "exactly at the cap" and surface a truncated indicator. Decoupled from the
// real constant — only used for the UI label, so a drift here is cosmetic.
const MAX_SHOW_BYTES = 2_000_000;

function looksBinary(s: string): boolean {
  const sample = s.slice(0, 8192);
  return sample.includes("\0");
}

const TEXT_EXTENSIONS: Record<string, string> = {
  ts: "javascript", tsx: "javascript", js: "javascript", jsx: "javascript", mjs: "javascript",
  py: "python", pyi: "python",
  html: "html", htm: "html",
  css: "css", scss: "css", sass: "css",
  json: "json",
  md: "markdown", markdown: "markdown",
  xml: "xml", svg: "xml",
  vue: "html",
};

export function languageFromPath(path: string): string {
  const dot = path.lastIndexOf(".");
  if (dot === -1) return "text";
  const ext = path.slice(dot + 1).toLowerCase();
  return TEXT_EXTENSIONS[ext] ?? "text";
}

function isComplete(side: DiffSide): boolean {
  return !!(side.box && side.path);
}

// ----- URL encoding -----

const NOTEBOOK_VERSION = 1;

interface NotebookEnvelope {
  v: number;
  cells: Array<{ l: DiffSide; r: DiffSide }>;
  def?: DefaultRepo;
}

function toBase64Url(s: string): string {
  // btoa needs a binary string; the input is JSON (ASCII-only after escape).
  // Use encodeURIComponent → unescape → btoa to handle any non-ASCII (paths).
  const utf8 = unescape(encodeURIComponent(s));
  const b64 = btoa(utf8);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(s: string): string {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const utf8 = atob(b64);
  return decodeURIComponent(escape(utf8));
}

export function notebookToBase64(cells: DiffCellState[], def: DefaultRepo | null): string {
  const envelope: NotebookEnvelope = {
    v: NOTEBOOK_VERSION,
    cells: cells.map((c) => ({ l: { ...c.left.config }, r: { ...c.right.config } })),
  };
  if (def && (def.box || def.directory)) envelope.def = { ...def };
  return toBase64Url(JSON.stringify(envelope));
}

function tryDecodeNotebook(b64: string): NotebookEnvelope | null {
  try {
    const json = fromBase64Url(b64);
    const parsed = JSON.parse(json);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.cells) && parsed.v === NOTEBOOK_VERSION) {
      return parsed as NotebookEnvelope;
    }
  } catch {
    // fall through
  }
  return null;
}

// Legacy M1 flat-query support: ?lb=&ld=&lr=&lp=&rb=&rd=&rr=&rp= → one cell.
function tryDecodeLegacyFlatQuery(q: Record<string, string | string[] | undefined>): { l: DiffSide; r: DiffSide } | null {
  const pick = (k: string) => {
    const v = q[k];
    return (Array.isArray(v) ? v[0] : v) ?? "";
  };
  const left = { box: pick("lb"), directory: pick("ld"), ref: pick("lr"), path: pick("lp") };
  const right = { box: pick("rb"), directory: pick("rd"), ref: pick("rr"), path: pick("rp") };
  const anything =
    left.box || left.directory || left.ref || left.path ||
    right.box || right.directory || right.ref || right.path;
  return anything ? { l: left, r: right } : null;
}

// ----- Store -----

export const useDiffStore = defineStore("diff", () => {
  const cells = ref<DiffCellState[]>([emptyCellState()]);
  const defaultRepo = ref<DefaultRepo | null>(null);
  // Set when the notebook was hydrated from /api/v1/diff/notebooks/:id (i.e.
  // we're viewing /app/diff/n/<id>). Edits clear it (immutability — the user
  // is forking, not mutating the server copy).
  const serverId = ref<string | null>(null);

  const cellCount = computed(() => cells.value.length);

  function cellLanguage(idx: number): string {
    const c = cells.value[idx];
    if (!c) return "text";
    const rightPath = c.right.config.path;
    if (rightPath) return languageFromPath(rightPath);
    return languageFromPath(c.left.config.path);
  }

  function cellIsBinary(idx: number): boolean {
    const c = cells.value[idx];
    if (!c) return false;
    if (c.left.status === "loaded" && looksBinary(c.left.content)) return true;
    if (c.right.status === "loaded" && looksBinary(c.right.content)) return true;
    return false;
  }

  function setSide(idx: number, which: "left" | "right", side: DiffSide) {
    const c = cells.value[idx];
    if (!c) return;
    const target = which === "left" ? c.left : c.right;
    target.config = { ...side };
    target.status = "idle";
    target.error = null;
    target.content = "";
    target.truncated = false;
    c.status = "idle";
    c.error = null;
    // Mutating any cell forks from the server-saved snapshot.
    serverId.value = null;
  }

  function setDefaultRepo(def: DefaultRepo | null) {
    defaultRepo.value = def && (def.box || def.directory) ? { ...def } : null;
    serverId.value = null;
  }

  function _applyDefault(side: DiffSide): DiffSide {
    if (!defaultRepo.value) return side;
    return {
      box: side.box || defaultRepo.value.box,
      directory: side.directory || defaultRepo.value.directory,
      ref: side.ref,
      path: side.path,
    };
  }

  function addCell(prefill?: { left?: Partial<DiffSide> | null; right?: Partial<DiffSide> | null }): number {
    const prev = cells.value[cells.value.length - 1];
    let leftSeed: Partial<DiffSide> = {};
    let rightSeed: Partial<DiffSide> = {};
    if (prefill?.left) {
      leftSeed = prefill.left;
    } else if (prefill?.left !== null && prev) {
      // Default: copy box+dir+path from previous; clear ref (most common usage:
      // same files at a different commit).
      leftSeed = {
        box: prev.left.config.box,
        directory: prev.left.config.directory,
        path: prev.left.config.path,
      };
    }
    if (prefill?.right) {
      rightSeed = prefill.right;
    } else if (prefill?.right !== null && prev) {
      rightSeed = {
        box: prev.right.config.box,
        directory: prev.right.config.directory,
        path: prev.right.config.path,
      };
    }
    // Layer default-repo on top of whatever the seed didn't fill.
    leftSeed = _applyDefault({ ...EMPTY_SIDE, ...leftSeed });
    rightSeed = _applyDefault({ ...EMPTY_SIDE, ...rightSeed });
    const cell = emptyCellState(leftSeed, rightSeed);
    cells.value.push(cell);
    serverId.value = null;
    return cells.value.length - 1;
  }

  function removeCell(idx: number): void {
    if (idx < 0 || idx >= cells.value.length) return;
    cells.value.splice(idx, 1);
    if (cells.value.length === 0) cells.value.push(emptyCellState());
    serverId.value = null;
  }

  function swapCells(i: number, j: number): void {
    if (i === j) return;
    if (i < 0 || j < 0) return;
    if (i >= cells.value.length || j >= cells.value.length) return;
    const tmp = cells.value[i]!;
    cells.value[i] = cells.value[j]!;
    cells.value[j] = tmp;
    serverId.value = null;
  }

  function swapSides(idx: number): void {
    const c = cells.value[idx];
    if (!c) return;
    const tmp = c.left;
    c.left = c.right;
    c.right = tmp;
    c.status = "idle";
    c.error = null;
    serverId.value = null;
  }

  function clearAll(): void {
    cells.value = [emptyCellState()];
    serverId.value = null;
  }

  function markServerSaved(id: string): void {
    serverId.value = id;
  }

  function currentEnvelope() {
    return {
      v: NOTEBOOK_VERSION,
      cells: cells.value.map((c) => ({ l: { ...c.left.config }, r: { ...c.right.config } })),
      def: defaultRepo.value ? { ...defaultRepo.value } : undefined,
    };
  }

  async function _loadSide(c: DiffCellState, which: "left" | "right", token: string | null): Promise<void> {
    const side = which === "left" ? c.left : c.right;
    if (!isComplete(side.config)) {
      side.status = "idle";
      side.content = "";
      side.error = null;
      side.truncated = false;
      return;
    }
    side.status = "loading";
    side.error = null;
    try {
      const res = await gitShow(
        side.config.box,
        side.config.directory || "/",
        side.config.path,
        side.config.ref || "HEAD",
        token,
      );
      side.content = res.content ?? "";
      side.status = "loaded";
      side.truncated = side.content.length >= MAX_SHOW_BYTES;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/not found|not a git repo/i.test(msg)) {
        side.status = "missing";
        side.content = "";
        side.error = msg;
      } else {
        side.status = "error";
        side.content = "";
        side.error = msg;
      }
    }
  }

  async function fetchCell(idx: number, token: string | null): Promise<void> {
    const c = cells.value[idx];
    if (!c) return;
    const leftReady = isComplete(c.left.config);
    const rightReady = isComplete(c.right.config);
    if (!leftReady && !rightReady) {
      c.status = "idle";
      c.error = null;
      return;
    }
    c.status = "loading";
    c.error = null;
    await Promise.all([_loadSide(c, "left", token), _loadSide(c, "right", token)]);
    if (c.left.status === "error" && c.right.status === "error") {
      c.status = "error";
      c.error = `Left: ${c.left.error}\nRight: ${c.right.error}`;
      return;
    }
    if (c.left.status === "error" && !rightReady) {
      c.status = "error";
      c.error = c.left.error;
      return;
    }
    if (c.right.status === "error" && !leftReady) {
      c.status = "error";
      c.error = c.right.error;
      return;
    }
    if (cellIsBinary(idx)) {
      c.status = "binary";
      return;
    }
    c.status = "ready";
  }

  // ----- URL hydration -----

  function hydrateFromQuery(query: Record<string, string | string[] | undefined>): boolean {
    const raw = query["n"];
    const n = Array.isArray(raw) ? raw[0] : raw;
    if (n) {
      const decoded = tryDecodeNotebook(n);
      if (decoded) {
        _replaceCellsFromEnvelope(decoded);
        serverId.value = null;
        return true;
      }
      // Truthy `n` but failed to decode — caller should toast.
      return false;
    }
    const legacy = tryDecodeLegacyFlatQuery(query);
    if (legacy) {
      cells.value = [emptyCellState(legacy.l, legacy.r)];
      defaultRepo.value = null;
      serverId.value = null;
      return true;
    }
    // Nothing usable in URL — leave whatever the store had (default = one empty cell).
    return true;
  }

  function hydrateFromEnvelope(envelope: NotebookEnvelope, sourceServerId: string | null): void {
    _replaceCellsFromEnvelope(envelope);
    serverId.value = sourceServerId;
  }

  function _replaceCellsFromEnvelope(envelope: NotebookEnvelope): void {
    cells.value = envelope.cells.map((e) => emptyCellState(e.l, e.r));
    defaultRepo.value = envelope.def ? { ...envelope.def } : null;
    if (cells.value.length === 0) cells.value.push(emptyCellState());
  }

  function toQuery(): Record<string, string> {
    // When viewing a server-saved notebook, the URL is /app/diff/n/:id and the
    // query string should stay empty — don't echo `?n=base64` redundantly.
    if (serverId.value) return {};
    const onlyEmpty = cells.value.length === 1 && _cellIsEmpty(cells.value[0]!) && !defaultRepo.value;
    if (onlyEmpty) return {};
    return { n: notebookToBase64(cells.value, defaultRepo.value) };
  }

  function _cellIsEmpty(c: DiffCellState): boolean {
    const ls = c.left.config;
    const rs = c.right.config;
    return !ls.box && !ls.directory && !ls.ref && !ls.path && !rs.box && !rs.directory && !rs.ref && !rs.path;
  }

  return {
    cells,
    defaultRepo,
    serverId,
    cellCount,
    cellLanguage,
    cellIsBinary,
    setSide,
    setDefaultRepo,
    addCell,
    removeCell,
    swapCells,
    swapSides,
    clearAll,
    fetchCell,
    hydrateFromQuery,
    hydrateFromEnvelope,
    markServerSaved,
    currentEnvelope,
    toQuery,
  };
});
