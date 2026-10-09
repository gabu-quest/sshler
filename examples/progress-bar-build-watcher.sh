#!/usr/bin/env bash
# progress-bar-build-watcher.sh
#
# Demo: push a "build" progress bar to a running local sshler instance and
# watch it climb 0 -> 100 in the browser. Demonstrates --label, --color,
# the running -> done transition, and a `trap` that marks the bar failed
# if the script exits abnormally.
#
# Prereqs:
#   - sshler is running on this host (default http://127.0.0.1:8822)
#   - sshler CLI is on PATH (`pip install sshler`)
#
# Token discovery is automatic when sshler is running locally — the CLI
# reads <config_dir>/runtime-token written by `sshler serve`. Set
# SSHLER_TOKEN if you want to push from another host.

set -euo pipefail

BAR_NAME="build"
TOTAL=100
STEPS=10
LABEL="frontend build"
COLOR_RUNNING="blue"
COLOR_DONE="green"
COLOR_FAILED="red"

cleanup_on_failure() {
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    echo "Build failed (exit $rc) — marking bar as failed." >&2
    sshler progress push "$BAR_NAME" "$TOTAL" "$TOTAL" \
      --status failed --color "$COLOR_FAILED" --label "$LABEL" || true
  fi
}
trap cleanup_on_failure EXIT

# Initial push at 0%
sshler progress push "$BAR_NAME" 0 "$TOTAL" \
  --status running --color "$COLOR_RUNNING" --label "$LABEL"

for i in $(seq 1 "$STEPS"); do
  sleep 1
  current=$(( i * TOTAL / STEPS ))
  sshler progress push "$BAR_NAME" "$current" "$TOTAL" \
    --status running --color "$COLOR_RUNNING" --label "$LABEL"
done

# Mark done — auto-dismiss kicks in after 10s in the dock.
# The bar stays visible in /app/progress until you delete it.
sshler progress push "$BAR_NAME" "$TOTAL" "$TOTAL" \
  --status done --color "$COLOR_DONE" --label "$LABEL"

# Disarm the failure trap — we finished cleanly.
trap - EXIT

echo "Done. Open http://127.0.0.1:8822/app/progress and subscribe to '$BAR_NAME' to watch the next run."
