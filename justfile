# sshler project commands
# Run `just` with no args to see all recipes

pnpm := "npx pnpm"

# Default: list all recipes
default:
    @just --list

# Run all tests (backend + frontend)
test: test-backend test-frontend

# Run backend tests
test-backend:
    uv run pytest

# Run frontend tests
test-frontend:
    {{pnpm}} --prefix frontend test -- --run

# Run E2E tests (requires playwright)
test-e2e:
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

# Build frontend
build: check-editable
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

# Type check frontend
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

# Lint frontend
lint:
    {{pnpm}} --prefix frontend run lint

# Full CI check: build + test + typecheck
ci: build test typecheck
