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

### M4: Performance & Scale ⬚
- [ ] Directory virtualization — virtual scrolling for 10K+ file listings
- [ ] Split FilesView — extract FilePreviewModal, FileEditorModal, FileUploadZone, FileBreadcrumb
- [ ] Diff viewer — side-by-side file comparison using CodeMirror merge extension

### M5: Advanced UX ⬚
- [ ] Split pane file manager — dual-pane WinSCP-style with drag-and-drop between boxes
