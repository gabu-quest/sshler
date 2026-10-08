/**
 * Maps a filename to a CodeMirror-facing "language" identifier.
 *
 * IMPORTANT — highlighter reality check (verified against
 * frontend/node_modules/@codemirror/* and frontend/src/components/CodeEditor.vue):
 *
 * CodeEditor.vue wraps CodeMirror 6 and only has these language packages
 * installed/wired: @codemirror/lang-javascript, lang-python, lang-html,
 * lang-css, lang-json, lang-markdown, lang-xml. Its internal
 * `getLanguageExtension()` switch matches on these literal (lowercased)
 * strings only:
 *
 *   javascript | js | jsx | ts | tsx   -> javascript()
 *   python | py                        -> python()
 *   html | htm                         -> html()
 *   css | scss | sass                  -> css()
 *   json                               -> json()
 *   markdown | md                      -> markdown()
 *   xml | svg                          -> xml()
 *   (anything else)                    -> [] (plain text, no highlighting)
 *
 * There is no @codemirror/lang-typescript package (typescript is handled by
 * lang-javascript's own parser) and no lang-data package for go/rust/yaml/
 * toml/sql/etc. This module intentionally returns semantically-correct,
 * CodeMirror-language-data-style ids (e.g. "typescript", "go", "yaml") for
 * every extension below, matching the ids that
 * @codemirror/language-data / individual @codemirror/lang-* packages use.
 *
 * Consequence callers must know: any id below that is NOT in the literal
 * list above (in particular "typescript" for .ts/.tsx, and every newly
 * added language such as go/rust/shell/yaml/toml/sql/...) will currently
 * render as plain text in CodeEditor.vue, because the matching CodeMirror
 * language package isn't installed there yet. That is an existing gap in
 * CodeEditor.vue (not owned by this module) — wiring it up requires adding
 * `@codemirror/lang-*` packages (or `@codemirror/language-data` +
 * `@codemirror/legacy-modes` for languages without a dedicated lang
 * package) and extending `getLanguageExtension()`'s switch, e.g. adding
 * `case 'typescript':` alongside the existing `case 'ts':`. That is
 * out of scope here.
 */

const FALLBACK_LANGUAGE = "text";

/** Extension (lowercase, without leading dot) -> language id. */
const EXTENSION_LANGUAGE_MAP: Record<string, string> = {
  // JavaScript / TypeScript family
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",

  // Python
  py: "python",
  pyw: "python",

  // Web
  html: "html",
  htm: "html",
  vue: "vue",
  css: "css",
  scss: "css",
  sass: "css",

  // Data / config
  json: "json",
  jsonc: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  conf: "ini",
  xml: "xml",
  svg: "xml",

  // Docs
  md: "markdown",
  markdown: "markdown",

  // Shell
  sh: "shell",
  bash: "shell",
  zsh: "shell",

  // Systems / compiled languages
  go: "go",
  rs: "rust",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  hxx: "cpp",
  cs: "csharp",
  java: "java",
  kt: "kotlin",
  kts: "kotlin",
  swift: "swift",

  // Scripting / other languages
  rb: "ruby",
  php: "php",
  lua: "lua",
  pl: "perl",
  pm: "perl",
  r: "r",

  // Database
  sql: "sql",
};

/** Basename (lowercase, no path) -> language id, for extensionless files. */
const BASENAME_LANGUAGE_MAP: Record<string, string> = {
  dockerfile: "dockerfile",
  makefile: "makefile",
  gnumakefile: "makefile",
  justfile: "makefile",
};

/**
 * Resolves the language id for a given filename (or full path — only the
 * basename is considered). Falls back to "text" when the extension is
 * unknown/absent.
 */
export function getLanguageForFilename(filename: string): string {
  const basename = (filename.split("/").pop() || filename).toLowerCase();

  const byBasename = BASENAME_LANGUAGE_MAP[basename];
  if (byBasename) return byBasename;

  const dotIndex = basename.lastIndexOf(".");
  if (dotIndex <= 0) return FALLBACK_LANGUAGE; // no extension, or dotfile like ".gitignore"

  const ext = basename.slice(dotIndex + 1);
  return EXTENSION_LANGUAGE_MAP[ext] || FALLBACK_LANGUAGE;
}
