# sshler Modernization TODO

Implementation roadmap for UX improvements and modernization features.

## Phase 1: Foundation & Core UX (High Impact, Low-Medium Effort)

### 1. Light/Dark Theme Toggle + System Preference Detection ✅
- [x] Add light theme CSS variables to style.css
- [x] Implement system preference detection with `prefers-color-scheme`
- [x] Add theme toggle button in header
- [x] Persist theme choice in localStorage
- [ ] Update CodeMirror theme integration
- [ ] Test contrast ratios for accessibility

**Why:** Foundational feature that affects all UI. Broadens appeal to users who prefer light mode.
**Files:** `static/style.css`, `templates/base.html`, `static/base.js`

### 2. Accessibility Improvements ✅ (Mostly Complete)
- [x] Add `aria-live` regions for toast notifications
- [x] Implement `prefers-reduced-motion` support
- [ ] Add keyboard navigation in file browser (arrow keys) - *Deferred to later*
- [x] Add visible focus indicators to all interactive elements
- [x] Add skip navigation links
- [x] Add ARIA labels to all buttons and interactive elements
- [x] Add `role` attributes to custom components (toasts)
- [ ] Verify color contrast ratios (WCAG AA compliance) - *Needs testing*

**Why:** Makes sshler accessible to more users, improves keyboard navigation for power users.
**Files:** `static/style.css`, `static/file-browser.js`, `templates/base.html`

### 3. PWA Support (Progressive Web App) ✅
- [x] Create `manifest.json` with app metadata
- [ ] Generate app icons (192x192, 512x512) - *Using SVG for now*
- [x] Create service worker for offline support
- [x] Cache static assets (CSS, JS, images)
- [x] Implement offline shell
- [x] Add meta tags for installation support
- [x] Service worker registration and update handling
- [ ] Test installation on desktop and mobile - *Ready for testing*

**Why:** Makes sshler installable as a native-feeling app on desktop and mobile.
**Files:** `manifest.json` (new), `static/sw.js` (new), `templates/base.html`

### 4. Terminal Enhancements
- [ ] Add terminal search (Ctrl+F in terminal)
- [ ] Implement font size controls (+/- buttons)
- [ ] Add terminal theme selector (Solarized, Dracula, Nord, etc.)
- [ ] Add export terminal output to file button
- [ ] Display copy/paste keyboard hints in terminal
- [ ] Add font family selector
- [ ] Persist terminal preferences in localStorage

**Why:** Essential features for power users who spend lots of time in terminals.
**Files:** `static/term.js`, `templates/term.html`

## Phase 2: File Management & Navigation (High Value Features)

### 5. Enhanced File Operations
- [ ] Implement inline rename (double-click filename)
- [ ] Add file copy operation
- [ ] Add file move operation (drag-drop between dirs)
- [ ] Add download multiple files as ZIP
- [ ] Add file permissions viewer
- [ ] Add file permissions editor (chmod)
- [ ] Show symlinks with special indicator
- [ ] Add create symlink option

**Why:** Basic operations that users expect in a file manager. Currently missing.
**Files:** `static/file-browser.js`, `webapp.py`, `templates/partials/dir_listing.html`

### 6. Upload Progress Indicators
- [ ] Add progress bar component
- [ ] Show progress for files >1MB
- [ ] Display upload speed and time remaining
- [ ] Add cancel/retry buttons
- [ ] Show queue for multiple uploads
- [ ] Add completion notifications
- [ ] Handle upload errors gracefully

**Why:** Large uploads feel broken without visual feedback.
**Files:** `static/file-browser.js`, `templates/partials/dir_listing.html`

### 7. Recent Files & Bookmarks
- [ ] Track last 10 accessed files in localStorage
- [ ] Add "Recent Files" dropdown in header
- [ ] Add star/pin icon for bookmarking files
- [ ] Create bookmarks panel
- [ ] Add clear history option
- [ ] Show file path and last accessed time
- [ ] Quick open from recent list

**Why:** Quick access to frequently used files improves productivity.
**Files:** `static/base.js`, `templates/base.html`

### 8. Command Palette (Cmd/Ctrl+K)
- [ ] Create command palette modal component
- [ ] Implement fuzzy search for commands
- [ ] Add keyboard shortcut registration system
- [ ] Register file operations (new, upload, download, etc.)
- [ ] Register session operations (switch, new, close)
- [ ] Register navigation commands (go to box, search)
- [ ] Register theme toggle, settings
- [ ] Add recently used commands
- [ ] Show keyboard shortcuts in palette
- [ ] Implement command execution

**Why:** Power user feature that drastically improves productivity. Modern UX pattern.
**Files:** `static/base.js`, `templates/base.html`, `static/command-palette.js` (new)

## Phase 3: Advanced Features & Polish

### 9. Global Search
- [ ] Add global search bar in header
- [ ] Search across box names
- [ ] Search across file names in all boxes
- [ ] Implement file content search (backend grep)
- [ ] Add search filters (file type, date, size)
- [ ] Show search results in modal
- [ ] Add recent searches
- [ ] Highlight search terms in results
- [ ] Keyboard navigation in results

**Why:** Find anything across all boxes quickly.
**Files:** `static/base.js`, `webapp.py`, `templates/base.html`

### 10. Session Persistence
- [ ] Save current pane layout to localStorage
- [ ] Save active sessions in each pane
- [ ] Save terminal scroll position
- [ ] Restore layout on page load
- [ ] Add "Restore previous session" prompt
- [ ] Save working directory per session
- [ ] Add session snapshots (save/load named layouts)

**Why:** Don't lose your workspace on browser refresh.
**Files:** `static/multi-session.js`, `static/term.js`

### 11. Connection Status Indicators
- [ ] Add connection status indicator in header
- [ ] Show number of active sessions
- [ ] Display WebSocket connection state
- [ ] Add reconnecting indicator with countdown
- [ ] Show error state with retry button
- [ ] Add connection health metrics
- [ ] Notify on connection loss

**Why:** Immediate feedback when connection issues occur.
**Files:** `static/term.js`, `templates/base.html`

### 12. Comprehensive Keyboard Shortcuts Overlay
- [ ] Design keyboard shortcuts modal
- [ ] Organize shortcuts into categories
- [ ] Show current context shortcuts (file browser vs terminal)
- [ ] Add searchable shortcuts
- [ ] Show custom user shortcuts
- [ ] Add print shortcuts option
- [ ] Improve existing `?` shortcut modal

**Why:** Discoverability of keyboard shortcuts improves power user adoption.
**Files:** `static/base.js`, `templates/base.html`

### 13. File Preview Improvements
- [ ] Add PDF viewer integration (PDF.js)
- [ ] Add video player for common formats
- [ ] Add audio player
- [ ] Show archive contents (ZIP, tar, gz)
- [ ] Add diff viewer for file comparison
- [ ] Add more syntax highlighting languages
- [ ] Add line numbers toggle
- [ ] Add word wrap toggle

**Why:** More file types viewable without downloading.
**Files:** `templates/file_view.html`, `static/file-view.js`, `webapp.py`

## Phase 4: Mobile & Touch Optimization

### 14. Touch-Friendly Improvements
- [ ] Increase button sizes on mobile (min 44x44px)
- [ ] Implement swipe gestures for navigation
- [ ] Add bottom sheet for mobile actions
- [ ] Long-press for context menu on mobile
- [ ] Optimize file selection for touch
- [ ] Mobile-optimized terminal toolbar
- [ ] Add pull-to-refresh
- [ ] Test on iOS and Android devices

**Why:** Better mobile experience for managing servers on the go.
**Files:** `static/style.css`, `static/file-browser.js`, `templates/term.html`

---

## Implementation Status

**Total Items:** 14 major features (80+ individual tasks)
**Completed:** 3 (Light/Dark Theme, Accessibility, PWA Support)
**In Progress:** 0
**Remaining:** 11

### Recently Completed:
- ✅ Feature #1: Light/Dark Theme Toggle (commit: 69cfe96)
- ✅ Feature #2: Accessibility Improvements (commit: 26d1ce6)
- ✅ Feature #3: PWA Support (commit: 6c3d257)

---

## Notes

- Each feature should be implemented, tested, and committed before moving to the next
- Run existing tests after each feature to ensure no regressions
- Update this file with checkboxes as tasks are completed
- Some features may require backend changes in `webapp.py`
- Consider creating feature flags for gradual rollout

---

## Future Vision (Not Prioritized)

- Session recording/playback
- Real-time collaboration
- AI command suggestions
- Performance dashboard
- Bandwidth monitoring
- Advanced tmux integrations
