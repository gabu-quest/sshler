# Roadmap: Diff Notebook

A scrollable, multi-cell diff workspace. Each "cell" is one `(left ↔ right)` comparison; cells stack vertically; the whole notebook is driven by a command bar AND mouse, and its state lives in the URL so comparisons are shareable.

## Goals

- Compare arbitrary files across boxes, repos, branches, and commits — without leaving sshler.
- See N diffs at once in a vertical scroll (multi-file review workflow).
- Command-driven (`:add`, `:repo`, `:rm`, `:swap`) for power users; button-driven for everyone else.
- Shareable URLs — a notebook is just a query string.

## Non-Goals (initial)

- Editing files from the diff view (read-only).
- Three-way / merge conflict resolution.
- Saved notebooks server-side (URL is enough; localStorage history is optional polish).

## Core Concepts

- **Cell**: one diff. `left: {box, repo, ref, path}` ↔ `right: {box, repo, ref, path}`. `ref` is a branch / tag / SHA / `HEAD~N`.
- **Repo context**: if every cell uses the same `(box, repo)` on both sides, the command bar accepts a short form (`:add path@alpha path@beta`). If cells mix repos, full form (`:add box:repo:path@ref box:repo:path@ref`) is required. Independent per cell, but with a sensible shorthand when only one repo is in play.
- **Notebook state**: ordered list of cells + an optional default repo context, serialized to the URL.

## Milestones

### M1: Backend diff endpoint + single-cell view ✅
- [x] ~~`POST /api/v1/git/diff` …~~ — **not needed**: existing `GET /api/v1/boxes/{name}/git/show` returns blob content at `ref:path`. Composed two `gitShow` calls instead. A real `git_diff` endpoint (renames, mode changes, binary detection at server level) is deferred to M4 polish.
- [x] ~~`GET /api/v1/git/refs` …~~ — **already existed** as `GET /api/v1/boxes/{name}/git/branches`; reused.
- [ ] `GET /api/v1/git/ls?box=&repo=&ref=` — deferred to M2 path autocomplete; not needed for the minimal cell.
- [x] New route `/app/diff` (`DiffView.vue`), renders ONE cell from URL params (`lb/ld/lr/lp/rb/rd/rr/rp`).
- [x] `<DiffCell>` — header with `left ↔ right` summary, body is the existing CodeMirror `<DiffViewer>` (M4 polish item: try `diff2html` for cheaper multi-cell rendering).
- [x] Pinia store `stores/diff.ts` — single-cell shape designed to extend to `cells: DiffCellState[]` in M2 without redesign. Handles loading / loaded / missing (file absent at ref) / error / binary states.
- [x] Vitest specs: `diff.spec.ts` (10 store tests: happy path, missing-side, error, binary, language detection, URL round-trip, empty-both guard), `DiffCell.spec.ts` (6 component tests: idle/loading/error/ready/binary/missing-tag rendering).
- **Shipped on `feat/diff-notebook` at a2a9c48** (forked from `feat/progress-bars`).

### M2: Multi-cell scroll + command bar ✅
- [x] Notebook is `cells: DiffCellState[]`; `<DiffView>` renders a vertical scroll of `<DiffCell>` with sticky per-cell headers.
- [x] `<CommandBar>` at top — `:add [left] [right]`, `:rm <n>`, `:swap <n> [m]`, `:repo <box> <dir>`, `:clear`, `?`. Parser is a **pure function** at `frontend/src/utils/diffCommandParser.ts`; permissive whitespace, strict syntax errors shown inline.
- [x] Buttons mirror commands — each cell has remove / move-up / move-down / swap-sides buttons; a bottom "+ Add diff" button prefills from the most recent cell.
- [x] ~~Cell-builder modal~~ — replaced with **inline pickers + auto-fetch**. Each cell has its `DiffSidePicker` rows visible at all times; typing a complete config (box + path) auto-loads after 400 ms debounce. No "Compare" button, no modal.
- [x] Keyboard: `c` / `:` focus command bar, `?` toggle help, `j` / `k` next / prev cell. Bound at view level, ignored while typing in any input.
- [x] Tests: 31 parser cases + 19 store cases (add/rm/swap/swap-sides/clear/base64 round-trip/legacy migration/binary/missing/error) + 9 cell render cases + 8 history cases. Total: 171 tests, all green.
- **Additional in M2:** `useDiffHistory` composable (last-10 localStorage), help overlay, SKILL doc at `docs/skills/diff-notebook/SKILL.md`, CLAUDE.md "Diff Notebook Pipeline" section.

### M3: URL state + sharing ✅
- [x] Serialize notebook to URL — `?n=<base64-json>` (versioned envelope; shipped in M2). Server-side persistence in M3 swaps the address bar for `/app/diff/n/<id>` (~11-char slug).
- [x] Router writes URL on every notebook change (debounced 400 ms); reload restores the notebook.
- [x] "Copy link" + "Save & share" buttons. Save & share posts to `POST /api/v1/diff/notebooks`, navigates to `/diff/n/<id>`, copies the short URL.
- [x] Saved-notebook drawer (`<DiffNotebookDrawer>`) — combines server-saved entries + the M2 local recents with Load and Delete actions per row.
- [x] Tests: serialize → deserialize round-trip (M2 store spec), malformed URL → toast (M3 spec + DiffView wiring), full backend test suite at `tests/test_api_diff.py` (12 cases: round-trip, list-meta-only, delete-twice, 404, 422 on bad envelope, 413 on oversized, token gate, default-repo round-trip).
- **Additional in M3:** `DiffNotebook` SQLerModel + 4 REST routes (`sshler/api/diff.py`); store `serverId` field with auto-clear on any mutation (immutability); `/app/diff/n/:id` route; SKILL doc + CLAUDE.md updated with the new contract.

### M4: Polish ⬚
- [ ] Syntax highlighting in diff (`diff2html` + highlight.js per detected language).
- [ ] Collapse unchanged hunks; expand on click.
- [ ] Per-cell "swap sides" button.
- [ ] CLI subcommand `sshler diff <left> <right> [<left> <right>...]` that prints a notebook URL — closes the loop on "scriptable from outside."
- [ ] Mobile layout — cells stack as unified diff on narrow screens (side-by-side is unreadable).

## Hard Rules

- **Read-only.** No write paths to git. Endpoint runs only `git diff`, `git ls-tree`, `git for-each-ref`, `git rev-parse --show-toplevel`.
- **Repo path validation.** Resolve the repo path, then refuse anything outside an allowlist (configurable; defaults to user home). No shell metacharacters in refs/paths — pass as argv, never via shell.
- **Auth.** All new endpoints `Depends(deps.require_token)`, same as the rest of the API.
- **Local box reuses the same endpoint.** No subprocess special-case in the route — the SSH layer's local-box shortcut handles it transparently.

## Open Questions

- Repo discovery: scan filesystem on demand, or maintain a config list of "known repos per box"? Lean toward scan-on-demand with a config-driven shortcut list as fallback.
- Diff size cap — refuse diffs >N MB unified? Probably yes at M1, configurable.
