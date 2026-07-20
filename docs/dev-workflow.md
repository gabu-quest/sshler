# Developer workflow (Vue SPA + FastAPI)

- **Python env**: `uv sync --group dev` to install backend + Playwright deps into the local venv. Run backend tests with `.venv/bin/pytest`.
- **Frontend env**: `cd frontend && pnpm install`. Dev server with `pnpm dev -- --host --base /app/` (targets backend at `http://127.0.0.1:8822` when `sshler serve` is running).
- **Build**: `pnpm build` emits to `sshler/static/dist`; `make frontend-build` runs build + dist check.
- **E2E**: `uv run playwright install chromium` once, then `.venv/bin/pytest tests/e2e/test_vue_app.py -q`.
- **UI**: the Vue SPA is served at `/app` whenever the built dist exists; root `/` redirects to `/app/`. (The legacy HTMX UI and the old `--ui` toggle have been removed — the SPA is the only UI.)
- **State**: favorites/pins persist via backend state DB; SPA bootstrap exposes `spa_enabled`, `spa_base`, and token header so clients can decide whether to hit `/app`.
