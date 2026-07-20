# Roadmap: sshler UX Upgrades

## Milestones

### M1: Quick Wins (Low Effort, High Impact) ✅
- [x] Tmux session switcher — sidebar/dropdown listing active sessions per box
- [x] Git branch display — wire up APIGitInfo in file browser breadcrumb
- [x] Per-box terminal theme — color scheme per box (prod=red, staging=green)
- [x] File permissions display/edit — show rwxr-xr-x, chmod modal
- [x] Fix 26 broken frontend tests — i18n initialization in Vitest

### M2: File Operations ✅
- [x] File content search (grep) — search input running grep on remote, clickable results
- [x] Batch file operations — multi-select move/copy/delete with floating action bar
- [x] Archive support — create/extract .tar.gz and .zip from context menu

### M3: Terminal Power Features ✅
- [x] Terminal layout persistence — save multi-pane layouts to localStorage, reconnect on reload
- [x] Snippets/commands library — save/quick-insert frequently used commands
- [x] Port forwarding UI — visual SSH tunnel management per box

### M4: Performance & Scale ✅
- [x] Directory virtualization — NDataTable virtual-scroll enabled for desktop
- [x] Diff viewer — side-by-side CodeMirror merge with language detection
- [x] SSE stats streaming — replaced 30s polling with progressive EventSource
- [x] SSH connection fail cache — 60s negative cache + 10s hard timeout
- [x] Split FilesView — all modal/panel components extracted; column logic stays inline (tightly coupled)

### M6: Diff Notebook 🔄
See [ROADMAP-diff-notebook.md](./ROADMAP-diff-notebook.md) for full plan.
- [x] M1: single-cell `<DiffView>` at `/app/diff` (composes existing `gitShow` + `<DiffViewer>` — no new backend needed)
- [x] M2: Multi-cell scroll, command bar (`:add` / `:rm` / `:swap` / `:repo` / `:clear`), base64 URL state, auto-fetch, history, SKILL doc
- [x] M3: Server-side notebooks + short URLs (`/app/diff/n/<id>`), Save & share, saved-notebook drawer, immutable-fork-on-edit, backend tests + frontend specs
- [ ] M3: URL state + shareable links + localStorage history
- [ ] M4: Polish (syntax highlighting, collapse hunks, CLI subcommand, mobile)

### M5: Commander — Dual-Pane File Manager 🔄
See [ROADMAP-commander.md](./ROADMAP-commander.md) for full plan.
- [ ] M1: Dual-pane browser shell (keyboard-driven MC aesthetic)
- [ ] M2: File comparison (auto-detect matching files, cross-repo diff)
- [ ] M3: Cross-box file transfer (drag-drop + F5/F6)
- [ ] M4: Git integration (log, blame, branch compare, file-at-commit)
- [ ] M5: Polish (bookmarks, fuzzy search, mobile)
