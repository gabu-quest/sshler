# Roadmap: Tmux Session Recovery

## Problem

When WSL/laptop crashes, tmux sessions die. Users lose all window layouts and have no idea what was running. sshler should periodically snapshot tmux state and offer recovery after crashes.

## Milestones

### M1: Snapshot Capture ✅

Backend-only. Periodic 30s background task captures per-window state (name, command, path) for all active local sessions. Stored in session metadata_json.

### M2: Startup Reconciliation ✅

Detects lost vs recovered sessions at startup. Also detects dead sessions live (OOM, kill) without restart. API: GET /api/v1/recovery, POST /recovery/{id}/recreate, POST /recovery/{id}/dismiss.

### M3: Recovery UI ✅

Modal on startup (and live via 15s polling) showing lost sessions with window details. Recreate navigates to terminal. Skip permanently dismisses (marks inactive in DB).

### M4: Polish ✅

**Deliverables:**
- [x] Snapshot cleanup: purge snapshots older than 7 days with no matching session
- [x] Settings: toggle snapshot capture on/off, configure interval (SettingsView card)
- [x] Visual indicator in session list showing "recovered" sessions (↺ badge in SessionSwitcher)
