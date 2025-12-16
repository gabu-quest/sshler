# sshler SPA (Vue 3 + Vite)

This is the Vue 3 SPA that replaces the legacy HTMX UI. It is built once and shipped with the Python package (no Node at runtime).

## Dev

```bash
pnpm install
pnpm dev -- --host --base /app/   # proxies /api and /ws to 127.0.0.1:8822
```

## Build (bundled into Python wheel)

```bash
pnpm run build        # emits to ../sshler/static/dist
pnpm run test         # vitest
```

`make frontend-build` in repo root runs install+build and checks the dist exists.
