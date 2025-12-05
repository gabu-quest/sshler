# Backend Enhancements for sshler: UX Features + Performance Improvements

## 🎯 Mission
Implement backend features to support advanced UX capabilities and improve overall performance. The frontend UX has been completely overhauled with modern design, search, filters, breadcrumbs, drag & drop, context menus, keyboard shortcuts, and more. Now we need backend support for:

## ✅ What's Already Done (Frontend Only)
- Comprehensive design system with color tokens, typography scale, shadows
- Search & filter for boxes (frontend only - filters existing data)
- Breadcrumb navigation in file browser
- Color-coded file type badges (py, js, ts, html, css, etc.)
- Drag & drop file upload (uses existing upload endpoint)
- Right-click context menus on files
- File search within directories (frontend filtering)
- Keyboard shortcuts (?, /, n, Ctrl+F, Esc)
- Empty states, skeleton loading, toast notifications
- Modal improvements, responsive design
- All in: `style.css`, templates, `base.js`, `file-browser.js`

## 🚀 Backend Features Needed

### 1. **File Metadata Display** (Medium Priority)
**What:** Show file size and last modified date in the file browser table

**Current State:**
- `templates/partials/dir_listing.html` shows files in table
- Only displays name, type badge, and actions
- Backend likely has this data from `asyncssh` stat calls

**Implementation:**
- Add `size` and `modified` fields to file entry data structure
- Format size as human-readable (B, KB, MB, GB)
- Format date as relative ("2 hours ago") or absolute
- Update `dir_listing.html` template to display in new columns
- Add to file badge area or as metadata row

**Files to modify:**
- Backend route that serves directory listings (probably in main app file)
- `templates/partials/dir_listing.html`
- Consider adding to CSS: `.file-meta` class already exists!

---

### 2. **Connection Status Indicators** (High Priority)
**What:** Real-time connection status for each box (🟢 Online, 🔴 Offline, 🟡 Unknown)

**Current State:**
- Boxes displayed on index page with no status info
- No health check or ping functionality

**Implementation:**
- Add async endpoint: `GET /box/{name}/status` → returns `{status: "online"|"offline"|"unknown", latency_ms: number}`
- Quick SSH connection test (or ping if faster)
- Cache results for 30-60 seconds to avoid spam
- Update index page to poll status on load
- Add status badge to box cards (top-right corner)
- Show latency on hover

**Files to modify:**
- Main FastAPI app (add status endpoint)
- `templates/index.html` (add status badge HTML)
- `style.css` (add `.status-badge` with colored dots)
- Add JavaScript to poll status and update badges

---

### 3. **Recent/Pinned Boxes** (High Priority)
**What:** Track recently accessed boxes and allow pinning favorites

**Current State:**
- No session/usage tracking
- All boxes shown equally in grid

**Implementation:**
- Add to `boxes.yaml` schema:
  ```yaml
  boxes:
    - name: gabu-server
      pinned: true  # new field
      last_accessed: 2025-12-05T10:30:00Z  # new field
  ```
- Update box access to timestamp `last_accessed`
- Add pin/unpin button (⭐ icon) on box cards
- Show pinned boxes at top of grid with different styling
- Add "Recent" section showing last 3-5 accessed boxes

**Files to modify:**
- Configuration loading/saving logic
- `templates/index.html` (add sections for Pinned/Recent/All)
- Add endpoint `POST /box/{name}/pin` and `POST /box/{name}/unpin`
- Update box access routes to track timestamp

---

### 4. **Batch File Operations** (Medium Priority)
**What:** Select multiple files and delete/download in batch

**Current State:**
- Individual file operations only
- No multi-select UI

**Implementation:**
- Add checkboxes to file table rows
- Add "Select All" checkbox in table header
- Show floating action bar when files selected: "Delete (3)" "Download (3)"
- Backend endpoints:
  - `POST /box/{name}/batch-delete` → `{paths: [...]}`
  - `GET /box/{name}/batch-download` → ZIP archive stream
- Use `zipfile` library for downloads

**Files to modify:**
- `templates/partials/dir_listing.html` (add checkboxes)
- `file-browser.js` (track selection, show action bar)
- Backend: add batch delete/download endpoints
- Consider using `asyncio.gather` for parallel operations

---

### 5. **Session History** (Medium Priority)
**What:** Track terminal sessions and allow resuming old sessions

**Current State:**
- Sessions created but not tracked persistently
- No history UI

**Implementation:**
- Store session history in `boxes.yaml` or separate JSON file:
  ```yaml
  sessions:
    - box: gabu-server
      session_name: work
      directory: /home/gabu/projects
      created: 2025-12-05T09:00:00Z
      last_active: 2025-12-05T10:30:00Z
  ```
- Show "Recent Sessions" on box detail page
- Add "Resume" button for each session
- Clean up old sessions after 7 days

**Files to modify:**
- Session tracking logic (wherever tmux sessions are created)
- `templates/box.html` (add "Recent Sessions" section)
- Add data structure for session persistence

---

## 💡 Other Backend Improvements

### 6. **Upload Progress** (Low Priority)
**What:** Show progress bar during file uploads

**Implementation:**
- Use chunked upload with progress events
- WebSocket or SSE for real-time progress updates
- Update toast to show progress bar instead of static message

---

### 7. **File Preview Cache** (Low Priority)
**What:** Cache file previews for faster subsequent views

**Implementation:**
- Add LRU cache for file contents (max 50MB in memory)
- Cache key: `{box_name}:{path}:{mtime}`
- Invalidate on file modification

---

### 8. **Bulk Configuration Import** (Low Priority)
**What:** Import multiple boxes from SSH config at once

**Implementation:**
- Add "Sync All from SSH Config" button
- Parse `~/.ssh/config` and add all Host entries
- Skip duplicates, show summary of imported boxes

---

### 9. **Favorite Directories Autocomplete** (Low Priority)
**What:** Suggest directories as you type when adding favorites

**Implementation:**
- Add endpoint: `GET /box/{name}/autocomplete?path=/home/g` → returns matching paths
- Add autocomplete dropdown to favorite input fields
- Use debouncing to avoid spam

---

### 10. **WebSocket-based File Browser** (Future)
**What:** Real-time file updates via WebSocket instead of polling

**Implementation:**
- Establish WebSocket connection on file browser load
- Server watches directory with `watchfiles` or `inotify`
- Push updates to client when files change
- Auto-refresh file list

---

## 📁 Key Files & Architecture

**Frontend:**
- `static/style.css` - All CSS (1,400+ lines, design system complete)
- `static/base.js` - Global JS, keyboard shortcuts, toasts, modals
- `static/file-browser.js` - Drag & drop, context menus, file search
- `templates/index.html` - Box search, filters, empty states
- `templates/box.html` - Box detail, favorites, file browser
- `templates/partials/dir_listing.html` - File table with breadcrumbs, badges

**Backend (assumed structure - verify):**
- Main FastAPI app (find the entry point)
- Box configuration handler (`boxes.yaml` reader/writer)
- SFTP/SSH connection logic (asyncssh)
- Routes for: boxes list, box detail, directory listing, file operations

**Data Storage:**
- `boxes.yaml` - Box configurations, favorites, defaults
- SSH config (`~/.ssh/config`) - imported as read-only boxes

---

## 🎯 Suggested Implementation Order

1. **File Metadata** - Quick win, high value, low complexity
2. **Recent/Pinned Boxes** - High impact on UX, moderate complexity
3. **Connection Status** - Flashy feature, users will love it
4. **Session History** - Nice-to-have for power users
5. **Batch Operations** - Complex but valuable
6. Other improvements as time allows

---

## 🚨 Important Notes

- All UX is already built and polished - just needs data!
- CSS classes for status badges, metadata already exist
- Don't break existing features - backend is production-ready
- Test with real SSH connections, not just localhost
- Consider performance - don't block on slow SSH operations
- Use async/await everywhere for FastAPI
- Add proper error handling and user feedback (toasts)

---

## ✅ Acceptance Criteria

For each feature:
- [ ] Backend endpoint implemented and tested
- [ ] Data structure designed and documented
- [ ] Frontend integrated (HTML + JS if needed)
- [ ] Error handling and loading states
- [ ] Works on both local and remote SSH boxes
- [ ] Responsive on mobile
- [ ] Committed with clear message

---

**Good luck! The UX is stunning - now make it powerful. 🚀**
