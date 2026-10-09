#!/usr/bin/env bash
# Verify that the installed `sshler` resolves to this dev tree.
#
# Why this exists:
#   `uv tool install <path>` (without --editable) copies the package to
#   ~/.local/share/uv/tools/sshler/.../site-packages/sshler/. That copy
#   includes static/dist/, frozen at install time. Future `vite build`s
#   write to the dev tree but the running server reads from the frozen
#   copy, so the SPA never updates — even after a `just deploy`.
#
# Re-fix: `uv tool install --editable . --force` from the project root.
#
# This check runs before `just build` and aborts if the install is
# non-editable, so we can't silently waste a build cycle.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_INIT="${REPO_ROOT}/sshler/__init__.py"

# Ask the interpreter that backs the `sshler` console-script binary where
# the `sshler` package actually resolves to. Use uv's managed env so we
# match what `sshler serve` will see.
RESOLVED="$(uv run --no-project python -c \
    'import importlib.util, sys; s = importlib.util.find_spec("sshler"); print(s.origin if s else "")' \
    2>/dev/null || true)"

# Fallback: ask the installed `sshler` binary's interpreter directly.
if [[ -z "$RESOLVED" ]]; then
    SSHLER_BIN="$(command -v sshler || true)"
    if [[ -n "$SSHLER_BIN" ]]; then
        # The tool venv shebang points at its own python — use that.
        TOOL_PYTHON="$(head -1 "$SSHLER_BIN" | sed 's|^#!||')"
        if [[ -x "$TOOL_PYTHON" ]]; then
            RESOLVED="$("$TOOL_PYTHON" -c \
                'import importlib.util; s = importlib.util.find_spec("sshler"); print(s.origin if s else "")' \
                2>/dev/null || true)"
        fi
    fi
fi

if [[ -z "$RESOLVED" ]]; then
    echo "check-editable: could not resolve the 'sshler' package — is it installed?" >&2
    echo "  Fix: uv tool install --editable . --force" >&2
    exit 1
fi

if [[ "$RESOLVED" != "$EXPECTED_INIT" ]]; then
    cat >&2 <<EOF
check-editable: STALE INSTALL DETECTED.

    Expected: $EXPECTED_INIT
    Resolved: $RESOLVED

The installed 'sshler' is NOT pointing at this dev tree. Frontend builds
will go to sshler/static/dist/ here, but the running server reads from
the resolved location above. The SPA will appear frozen no matter how
many times you rebuild.

Fix it now with:

    uv tool install --editable . --force

Then re-run 'just build'.
EOF
    exit 1
fi

# Belt-and-suspenders: confirm the static/dist path the server WILL serve
# is the same one vite WILL write to.
RESOLVED_DIST="$(dirname "$RESOLVED")/static/dist"
EXPECTED_DIST="${REPO_ROOT}/sshler/static/dist"
RESOLVED_DIST_REAL="$(readlink -f "$RESOLVED_DIST" 2>/dev/null || echo "$RESOLVED_DIST")"
EXPECTED_DIST_REAL="$(readlink -f "$EXPECTED_DIST" 2>/dev/null || echo "$EXPECTED_DIST")"
if [[ "$RESOLVED_DIST_REAL" != "$EXPECTED_DIST_REAL" ]]; then
    echo "check-editable: dist path mismatch — server reads $RESOLVED_DIST_REAL, vite writes $EXPECTED_DIST_REAL" >&2
    echo "  Fix: uv tool install --editable . --force" >&2
    exit 1
fi
