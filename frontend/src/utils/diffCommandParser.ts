// Pure parser for the Diff Notebook command bar. No Vue, no side effects.
// Returns a tagged union — the caller dispatches the action.
//
// Grammar (whitespace-permissive):
//   :add [<side>] [<side>]      append a cell; sides default to "prefill from previous"
//   :rm  <n>                    remove the 1-indexed n-th cell
//   :swap <n>                   swap left/right of cell n
//   :swap <n> <m>               swap cells n and m
//   :repo <box> <dir>           set default repo (applied to future :add prefills)
//   :clear                      reset to a single empty cell
//   :help                       open the help overlay
//   ?                           same as :help (bare ? is also accepted by the bar)
//
// Side syntax: `box:dir@ref:path` — any segment may be omitted but the order must
// hold. Examples:
//   local:/repo@main:src/foo.ts
//   :@feat:src/foo.ts          (no box/dir; ref + path only)
//   local::main:src/foo.ts     (no dir)
//   local:/repo@:src/foo.ts    (no ref)
//   local:/repo@main:          (no path — useful for setting repo defaults, not strictly meaningful for :add)

export interface SideSpec {
  box: string;
  directory: string;
  ref: string;
  path: string;
}

export type Command =
  | { type: "add"; left: SideSpec | null; right: SideSpec | null }
  | { type: "rm"; index: number }
  | { type: "swap"; index: number; other: number | null }
  | { type: "repo"; box: string; directory: string }
  | { type: "clear" }
  | { type: "help" };

export interface ParseError {
  type: "error";
  message: string;
}

export type ParseResult = Command | ParseError;

const COMMAND_RE = /^\s*(?::?\s*([a-z?]+))(?:\s+(.*))?$/i;

export function parseCommand(raw: string): ParseResult {
  const input = raw.trim();
  if (!input) return { type: "error", message: "Empty command." };
  if (input === "?" || input === ":?" || input === ":help" || input === "help") {
    return { type: "help" };
  }
  const m = COMMAND_RE.exec(input);
  if (!m) return { type: "error", message: `Unrecognized command: ${input}` };
  const name = (m[1] ?? "").toLowerCase();
  const rest = (m[2] ?? "").trim();
  switch (name) {
    case "add":
      return parseAdd(rest);
    case "rm":
    case "remove":
    case "delete":
      return parseRm(rest);
    case "swap":
      return parseSwap(rest);
    case "repo":
      return parseRepo(rest);
    case "clear":
    case "reset":
      if (rest) return { type: "error", message: ":clear takes no arguments." };
      return { type: "clear" };
    case "help":
    case "h":
      return { type: "help" };
    default:
      return { type: "error", message: `Unknown command: :${name}` };
  }
}

function parseAdd(rest: string): ParseResult {
  if (!rest) return { type: "add", left: null, right: null };
  const tokens = splitTokens(rest);
  if (tokens.length > 2) {
    return { type: "error", message: ":add takes at most two side specs." };
  }
  const left = tokens[0] ? parseSide(tokens[0]) : null;
  const right = tokens[1] ? parseSide(tokens[1]) : null;
  if (left && "type" in left) return left;
  if (right && "type" in right) return right;
  return { type: "add", left: left as SideSpec | null, right: right as SideSpec | null };
}

function parseRm(rest: string): ParseResult {
  if (!rest) return { type: "error", message: ":rm requires a cell number." };
  const tokens = splitTokens(rest);
  if (tokens.length !== 1) {
    return { type: "error", message: ":rm takes exactly one cell number." };
  }
  const n = parsePositiveInt(tokens[0]!);
  if (n === null) return { type: "error", message: `Invalid cell number: ${tokens[0]}` };
  return { type: "rm", index: n };
}

function parseSwap(rest: string): ParseResult {
  if (!rest) return { type: "error", message: ":swap requires at least one cell number." };
  const tokens = splitTokens(rest);
  if (tokens.length > 2) {
    return { type: "error", message: ":swap takes one or two cell numbers." };
  }
  const a = parsePositiveInt(tokens[0]!);
  if (a === null) return { type: "error", message: `Invalid cell number: ${tokens[0]}` };
  if (tokens.length === 1) return { type: "swap", index: a, other: null };
  const b = parsePositiveInt(tokens[1]!);
  if (b === null) return { type: "error", message: `Invalid cell number: ${tokens[1]}` };
  if (a === b) return { type: "error", message: ":swap needs two different cell numbers." };
  return { type: "swap", index: a, other: b };
}

function parseRepo(rest: string): ParseResult {
  if (!rest) return { type: "error", message: ":repo requires a box and a directory." };
  const tokens = splitTokens(rest);
  if (tokens.length !== 2) {
    return { type: "error", message: ":repo takes exactly two arguments: <box> <dir>." };
  }
  return { type: "repo", box: tokens[0]!, directory: tokens[1]! };
}

// Split on whitespace but respect simple double-quoting for paths with spaces.
function splitTokens(s: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (const ch of s) {
    if (ch === '"') {
      quoted = !quoted;
      continue;
    }
    if (!quoted && /\s/.test(ch)) {
      if (cur) {
        out.push(cur);
        cur = "";
      }
      continue;
    }
    cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

function parsePositiveInt(s: string): number | null {
  if (!/^\d+$/.test(s)) return null;
  const n = parseInt(s, 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

// A side spec is `box:dir@ref:path`. We split at the FIRST `@` for ref boundary,
// then split the left half on `:` (box, dir) and the right half on `:` (ref, path).
// Any missing segment is the empty string.
export function parseSide(raw: string): SideSpec | ParseError {
  if (!raw) return { type: "error", message: "Empty side spec." };
  const at = raw.indexOf("@");
  let beforeAt: string;
  let afterAt: string;
  if (at === -1) {
    beforeAt = raw;
    afterAt = "";
  } else {
    beforeAt = raw.slice(0, at);
    afterAt = raw.slice(at + 1);
  }
  // beforeAt: box:dir  (dir may itself contain colons? Treat the FIRST colon as the boundary.)
  let box = "";
  let directory = "";
  const firstColon = beforeAt.indexOf(":");
  if (firstColon === -1) {
    box = beforeAt;
  } else {
    box = beforeAt.slice(0, firstColon);
    directory = beforeAt.slice(firstColon + 1);
  }
  // afterAt: ref:path (path may contain colons → split at FIRST colon only).
  let ref = "";
  let path = "";
  const refColon = afterAt.indexOf(":");
  if (refColon === -1) {
    // No colon after @ → treat the whole thing as the ref (path is empty).
    ref = afterAt;
  } else {
    ref = afterAt.slice(0, refColon);
    path = afterAt.slice(refColon + 1);
  }
  return { box, directory, ref, path };
}

export function formatSide(s: SideSpec): string {
  // Used by the help overlay to render examples. Not the inverse of parseSide
  // (we always emit all colons + @) so the output is stable.
  return `${s.box}:${s.directory}@${s.ref}:${s.path}`;
}
