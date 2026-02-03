# sshler project commands
# Run `just` with no args to see all recipes

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
    pnpm --prefix frontend test -- --run

# Run E2E tests (requires playwright)
test-e2e:
    uv run pytest tests/e2e/ -v

# Run mobile responsive E2E tests
test-mobile:
    uv run pytest tests/e2e/test_mobile_responsive.py -v

# Build frontend
build:
    pnpm --prefix frontend run build

# Type check backend
typecheck-backend:
    uv run mypy sshler/

# Type check frontend
typecheck-frontend:
    pnpm --prefix frontend run type-check

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
    pnpm --prefix frontend install

# Install all dependencies
install: install-frontend
    uv sync

# Lint frontend
lint:
    pnpm --prefix frontend run lint

# Full CI check: build + test + typecheck
ci: build test typecheck
