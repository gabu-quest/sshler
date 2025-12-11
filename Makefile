frontend-build:
	cd frontend && pnpm install --frozen-lockfile && pnpm run build
	python scripts/check_frontend_dist.py

.PHONY: frontend-build
