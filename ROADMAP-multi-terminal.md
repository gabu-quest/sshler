# Roadmap: Multi-Terminal Layout Persistence

## Problem

When navigating away from the multi-terminal view and coming back, the entire layout (which boxes, which sessions, grid arrangement) is lost. Users have to rebuild their multi-pane setup every time.

## Milestones

### M1: Persist Current Layout ✅

Save the multi-terminal layout automatically so navigating away and back restores it.

**Deliverables:**
- [x] Save layout state to localStorage when panels change: box names, session names, directories
- [x] On mount, restore the last layout if one exists
- [x] Auto-save on every layout change (add/remove panel)
- [x] Per-box layouts — N/A, multi-terminal intentionally mixes boxes; layout stores box per terminal

### M2: Named Layouts / Presets ✅

Allow users to save and load named multi-terminal layouts.

**Deliverables:**
- [x] "Save Layout" button with a name input
- [x] "Load Layout" popover listing saved layouts
- [x] Delete saved layouts
- [x] Store in SQLite (Layout model in state.py)
- [x] API: GET/POST/DELETE /api/v1/layouts
