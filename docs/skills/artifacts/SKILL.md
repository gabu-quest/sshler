---
name: sshler-artifacts
description: Create, organize, register, inspect, and unregister local HTML artifacts in sshler. Use when an agent produces a standalone HTML file, a small static site, or a directory of HTML reports that the user should be able to revisit from the Artifacts catalog.
---

# sshler HTML Artifacts

Create the requested HTML inside the user's current workspace, then register its path with the running sshler instance. Treat sshler as a catalog and local-only viewer: it stores metadata and serves existing files, but it never owns or deletes source files.

## Workflow

1. Search the catalog before creating anything:

```bash
sshler artifact find "quarterly summary" --project demo --json
```

Reuse or update a matching registration when it represents the same deliverable. Create a new
artifact only when the requested output is materially different.

2. Confirm the intended output location is inside the current workspace.
3. Create or update the HTML and its local assets.
4. Choose a registration mode:
   - `file`: expose exactly one HTML file. Use this by default for a self-contained page.
   - `site`: expose one directory and open a designated entry point, normally `index.html`.
   - `collection`: discover every HTML page below a directory and list them independently.
5. Choose a required top-level project and an optional slash-delimited group such as
   `design/experiments`.
6. Register the completed output:

```bash
sshler artifact add ./report.html --project demo --group reports --mode file
sshler artifact add ./site --project demo --group prototypes --mode site --entry index.html
sshler artifact add ./reports --project demo --group runs --discover
```

Add `--slug quarterly-summary` when a deliberate, stable alias is useful. Add
`--mount published/reports` only when existing HTML uses root-relative links such as
`/published/reports/details.html`; mounts are globally unique.

7. Report the saved path, project/group, catalog URL, alias, and mount returned by the command.

Use `--json` when another program or agent needs a machine-readable result.

## Maintenance

```bash
sshler artifact list
sshler artifact list --project demo
sshler artifact find "layout explorer" --project demo
sshler artifact show <id>
sshler artifact rescan <id>
sshler artifact update <id> --group reports/final --title "Final report"
sshler artifact open <id>
sshler artifact remove <id>
```

`remove` unregisters metadata only. Never delete, move, or rewrite the underlying file unless the user separately requests that filesystem change.

Link between registered artifacts with stable sidecar paths:

- Prefer `/r/<project-slug>/<artifact-slug>/` for new links.
- Use a registered root mount only when preserving existing root-relative URLs.
- Do not link through `/a/<id>/`; IDs remain supported for compatibility but are not authoring
  targets.

## Built SPAs

Register compiled static output, never a framework development server:

```bash
npm run build
sshler artifact add ./dist --project demo --mode site --entry index.html
```

For Vue and similar client-rendered apps:

- Prefer hash routing and a relative asset base when the artifact does not need a dedicated mount.
  For Vue/Vite, use `createWebHashHistory()` and `base: "./"`.
- When the app already uses a fixed root base, register the matching `--mount` and build for that
  base, such as `base: "/my-demo/"`.
- Do not assume history-mode fallback. Direct requests for virtual client routes return 404 because
  the artifact server serves registered files and does not rewrite missing paths to `index.html`.
- Use a multi-page build, hash routing, or real HTML files for every route when direct deep links
  must work.

## Content and safety rules

- Keep asset references relative so sites remain portable.
- Prefer a self-contained file for a one-page result; use a site only when separate assets or routes materially help.
- Do not put secrets, credentials, tokens, private keys, or environment dumps in generated HTML.
- Do not register a directory broader than the generated output. Site and collection modes expose regular files below the registered directory to the loopback-only server.
- Do not depend on cross-origin requests from the HTML to the sshler API. The artifact server intentionally sends no CORS permission.
- Inline JavaScript is supported. Treat generated HTML as trusted local content and keep it scoped to the requested experience.
- If opening is unavailable because the UI is not on a loopback address, registration still succeeds; give the user the catalog path and explain that previewing is local-only.

## Project and group conventions

- Always place a registration under a project. Supply `--project` when the intended project name is known.
- If `--project` is omitted, the CLI asks the server to infer one from the nearest repository or source name.
- Use groups for human navigation, not filesystem mirroring. Keep them concise and stable.
- Prefer one registration per logical deliverable. Re-registering the same source is idempotent when its metadata matches.
