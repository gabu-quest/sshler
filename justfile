# sshler project commands
# Run `just` with no args to see all recipes

pnpm := "npx pnpm"

# Default: list all recipes
default:
    @just --list

# Run every suite (backend unit, frontend, E2E); a failing suite does not stop
# the others. Prints one line per suite and exits non-zero if any failed.
# Extra args go to the backend suite, e.g. `just test tests/test_basic.py`.
test *backend_args:
    #!/usr/bin/env bash
    set -u
    fail=0
    just test-backend {{backend_args}}; backend=$?
    just test-frontend; frontend=$?
    just test-e2e; e2e=$?
    echo
    for s in backend frontend e2e; do
        rc=${!s}
        if [ "$rc" -eq 0 ]; then echo "$s: pass"; else echo "$s: FAIL (exit $rc)"; fail=1; fi
    done
    exit $fail

# Run backend unit tests (everything except E2E)
test-backend *args:
    uv run pytest -m "not e2e" {{args}}

# Run frontend tests
test-frontend:
    {{pnpm}} --prefix frontend test -- --run

# Run E2E tests (requires playwright)
test-e2e:
    uv run playwright install chromium
    uv run pytest tests/e2e/ -v

# Run mobile responsive E2E tests
test-mobile:
    uv run pytest tests/e2e/test_mobile_responsive.py -v

# Verify `sshler` is an editable install pointing at this dev tree.
# A non-editable `uv tool install` ships a frozen copy of sshler/static/dist
# that vite builds will never update — the running server then serves stale
# assets regardless of how many times you rebuild. (This happened in May 2026.)
check-editable:
    @./scripts/check-editable-install.sh

# Build frontend (type-checks first; a type error stops the build)
build: check-editable typecheck-frontend
    {{pnpm}} --prefix frontend run build

# Build frontend and restart sshler.
# NOTE: systemd is currently disabled in WSL (/etc/wsl.conf systemd=false). The
# boot script /etc/wsl-boot.sh launches sshler with logs to /tmp/sshler.log;
# we append to the same file so restarts don't blackhole logs. Once systemd is
# enabled, this recipe should become `systemctl --user restart sshler` and the
# boot-script line should be removed.
deploy: build
    @pkill -x sshler 2>/dev/null; sleep 1; nohup sshler serve >> /tmp/sshler.log 2>&1 & disown
    @sleep 2 && pgrep -x sshler >/dev/null && echo "sshler restarted (pid $(pgrep -x sshler)) — tail logs: tail -f /tmp/sshler.log" || (echo "sshler failed to start" >&2; exit 1)

# Type check backend
typecheck-backend:
    uv run mypy sshler/

# Type check frontend: vue-tsc over the app, the specs and the vite/vitest configs
typecheck-frontend:
    {{pnpm}} --prefix frontend run type-check

# Type check everything
typecheck: typecheck-backend typecheck-frontend

# Start dev server (backend + frontend with HMR)
dev:
    uv run sshler serve --dev

# Start backend only
server:
    uv run sshler serve --log-level debug

# Install frontend dependencies
install-frontend:
    {{pnpm}} --prefix frontend install

# Install all dependencies
install: install-frontend
    uv sync

# Lint: ruff over sshler/ and tests/, then the frontend type-check (the frontend
# has no ESLint). Both always run; exits non-zero if either failed.
lint:
    #!/usr/bin/env bash
    set -u
    uv run ruff check sshler tests; ruff=$?
    just typecheck-frontend; frontend=$?
    echo
    for s in ruff frontend; do
        rc=${!s}
        if [ "$rc" -eq 0 ]; then echo "$s: pass"; else echo "$s: FAIL (exit $rc)"; fi
    done
    [ "$ruff" -eq 0 ] && [ "$frontend" -eq 0 ]

# Full CI check: build + test + typecheck
ci: build test typecheck
