# ROADMAP: Commander — Dual-Pane File Manager

## Vision

A Midnight Commander-inspired dual-pane file manager at `/app/commander`. Dense, keyboard-driven, monospace. Two independently-navigable panes that can browse different boxes, different repos, different git commits. Compare files across repos, transfer between boxes, dive into git history. The power tool sshler has been building toward.

## Design Language

**Midnight Commander / ranger aesthetic:**
- Monospace everything — file names, sizes, dates, paths
- Dense info panels, no wasted whitespace
- Dark chrome with accent highlights (purple for selections, yellow for matches, green for git clean, red for git dirty)
- Bottom hotkey bar: `F1 Help | F3 View | F4 Edit | F5 Copy | F6 Move | F7 Mkdir | F8 Delete | F10 Quit`
- Tab switches focus between panes, arrow keys navigate, Enter enters/opens
- Active pane has bright border, inactive pane is dimmed
- Path bar with breadcrumb at top of each pane
- Box selector + git branch/commit in pane header

---

## Milestones

### M1: The Shell — Dual-Pane Browser

The foundation. Two file listing panes side by side with keyboard navigation.

**Deliverables:**
- New route `/app/commander` + `CommanderView.vue`
- Two `CommanderPane` components, each with:
  - Box selector dropdown (reuse `boxOptions` pattern from TerminalView)
  - Path bar (editable, with breadcrumb segments)
  - File listing table (monospace, columns: name, size, date, perms)
  - Git badge (branch + commit) in pane header
- Keyboard navigation:
  - `Tab` — switch active pane
  - `↑/↓` — navigate files
  - `Enter` — open directory / preview file
  - `Backspace` — go up one directory
  - `Home` — go to ~ (home dir)
- MC-style bottom hotkey bar (visual only in M1 — wired in later milestones)
- Pane resize handle (draggable splitter between panes)
- Remember last pane config in localStorage

**Backend:** No new endpoints — reuses existing `/api/v1/boxes/{name}/files?directory=...`

**New files:**
- `frontend/src/views/CommanderView.vue` — main view, layout, keyboard handler
- `frontend/src/components/CommanderPane.vue` — single pane (box + path + file list)
- `frontend/src/components/CommanderHotbar.vue` — bottom F-key bar

---

### M2: File Comparison

Compare files between panes — manual selection and auto-detection of matching names.

**Deliverables:**
- Select file in left pane + file in right pane → `F3` or `Enter` on "Compare" → DiffViewer opens as overlay
- Auto-detect files with matching names between panes:
  - Exact match: same filename in both directories → highlighted with yellow badge
  - Fuzzy match: similar names (Levenshtein distance ≤ 2, or same stem different extension) → subtle indicator
- "Compare matched" mode: step through matching files one by one with `←/→` arrows
- Cross-repo comparison: left pane in `~/example/example-repo-a/`, right pane in `~/example/example-repo-b/` → all matching files highlighted
- Reuse existing `DiffViewer.vue` (CodeMirror merge, side-by-side)

**Backend:** No new endpoints — reuses `fetchFilePreview` for both files

**New files:**
- `frontend/src/components/CommanderDiffOverlay.vue` — full-screen diff overlay with prev/next matched file navigation

---

### M3: Cross-Box File Transfer

Drag-and-drop and keyboard (F5/F6) file transfer between panes, including across different boxes.

**Deliverables:**
- `F5` = Copy selected file(s) from active pane to other pane's current directory
- `F6` = Move (copy + delete source)
- `F7` = Create directory in active pane
- `F8` = Delete selected file(s) in active pane
- Drag file row(s) from one pane → drop on other pane → transfer
- Multi-select: Shift+click range, Ctrl+click toggle, `*` selects all
- Progress indicator for transfers (reuse XMLHttpRequest progress pattern from upload)
- Same-box transfer: use existing `copyFile`/`moveFile` API
- Cross-box transfer: new backend endpoint that streams via SFTP

**Backend:**
- `POST /api/v1/transfer` — streams file from source box to dest box server-side
  - Body: `{ source_box, source_path, dest_box, dest_path }`
  - For local→remote or remote→local: direct SFTP
  - For remote→remote: proxy through server (SFTP read → SFTP write)
  - Streaming for large files, not buffered in memory

**New files:**
- `sshler/api/transfer.py` — cross-box transfer endpoint
- `frontend/src/components/CommanderTransferProgress.vue` — transfer progress overlay

---

### M4: Git Integration (Lightweight GitLens)

Full git history navigation within each pane.

**Deliverables:**
- **Git log:** Commit history picker in pane header → shows last 50 commits
- **File at commit:** Select a commit → pane shows files as they were at that commit (`git show <commit>:<path>`)
- **Git blame:** Toggle blame view on any file → shows per-line author + commit + date
- **Branch picker:** Switch pane to a different branch → files reflect that branch
- **Branch compare:** Left pane on branch A, right pane on branch B → auto-shows changed files with diff indicators
- **Commit diff:** Select a commit → shows all files changed in that commit with inline diff

**Backend (new endpoints in `sshler/api/git.py`):**
- `GET /api/v1/boxes/{name}/git/log?directory=...&limit=50` → `[{hash, short_hash, message, author, date}]`
- `GET /api/v1/boxes/{name}/git/show?directory=...&path=...&ref=...` → file content at ref
- `GET /api/v1/boxes/{name}/git/blame?directory=...&path=...` → `[{line, content, commit, author, date}]`
- `GET /api/v1/boxes/{name}/git/branches?directory=...` → `[{name, is_current, last_commit}]`
- `GET /api/v1/boxes/{name}/git/diff-files?directory=...&ref_a=...&ref_b=...` → `[{path, status}]` (changed files between refs)

**New files:**
- `sshler/api/git.py` — all git API endpoints
- `frontend/src/components/CommanderGitLog.vue` — commit list panel
- `frontend/src/components/CommanderBlame.vue` — blame overlay

---

### M5: Polish & Power Features

**Deliverables:**
- Keyboard shortcut help overlay (`F1`)
- Bookmarked path pairs (save "left=X, right=Y" for common comparisons)
- Fuzzy file search within pane (`Ctrl+F` or `/`)
- Quick-open file in terminal (`F4` → opens in $EDITOR via tmux)
- File size comparison between panes (show diff in size column)
- Animated transitions between directory navigations
- Mobile-responsive: stack panes vertically on narrow screens

---

## Architecture Notes

### Component hierarchy
```
CommanderView.vue (layout, keyboard handler, splitter)
├── CommanderPane.vue × 2 (box selector, path, file list, git header)
│   ├── File rows (monospace table, selectable, draggable)
│   └── Git badge + branch/commit picker
├── CommanderHotbar.vue (F1-F10 bar)
├── CommanderDiffOverlay.vue (full-screen diff with navigation)
├── CommanderGitLog.vue (commit history panel)
├── CommanderBlame.vue (blame overlay)
└── CommanderTransferProgress.vue (transfer progress)
```

### Keyboard map
| Key | Action |
|-----|--------|
| `Tab` | Switch active pane |
| `↑/↓` | Navigate files |
| `Enter` | Open dir / preview file |
| `Backspace` | Go up |
| `Home` | Go to ~ |
| `Space` | Toggle select file |
| `*` | Select all / deselect all |
| `F1` | Help |
| `F3` | View / Compare |
| `F4` | Edit |
| `F5` | Copy to other pane |
| `F6` | Move to other pane |
| `F7` | Create directory |
| `F8` | Delete |
| `F10` | Close / back to overview |
| `/` or `Ctrl+F` | Search in pane |
| `Ctrl+G` | Git log |
| `Ctrl+B` | Git blame |

### Reusable components
- `DiffViewer.vue` — CodeMirror merge (as-is)
- `CodeEditor.vue` — inline editing (as-is)
- `GitBadge.vue` — branch + commit display (as-is)
- `fetchFilePreview()` — file content fetching (as-is)
- `gitInfo()` — branch/commit/dirty status (as-is)
- `copyFile()` / `moveFile()` / `batchCopy()` — within-box transfers (as-is)
