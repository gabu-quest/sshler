---
name: sshler-diff-notebook
description: Reference for sshler's Diff Notebook — multi-cell diff workspace at /app/diff, command bar, base64 URL state, and the reuse-existing-endpoints architecture. Load when working on stores/diff.ts, DiffView.vue, the command parser, or extending the cell shape.
---

# sshler — Diff Notebook

A scrollable, multi-cell diff workspace at `/app/diff`. Each "cell" is one `(left ↔ right)` file comparison; the page stacks N cells vertically. Each cell's two sides are fully independent — different boxes, directories, refs, paths are all fine. The page is driven by an always-visible command bar AND mouse, and its state lives entirely in the URL so notebooks are shareable.

## Architecture in one paragraph

The diff notebook reuses sshler's **existing** git endpoints — there is no new backend code. Each cell side is fetched via `GET /api/v1/boxes/{name}/git/show` (blob content at `ref:path`). Branch autocomplete uses `GET /api/v1/boxes/{name}/git/branches`. The frontend store composes one `gitShow` call per side, then hands both blobs to the existing `<DiffViewer>` (CodeMirror MergeView). **If you find yourself adding a `git_diff` route — stop. M1 explicitly considered and rejected that path.** A real server-side `git diff` endpoint (renames, mode changes, binary detection) is only worth it when M4 polish lands.

## Pipeline

```
URL  /app/diff/n/<id>                  ─┐
     /app/diff?n=<base64-json>          ├──→  diffStore.hydrateForRoute       (frontend/src/views/DiffView.vue)
     /app/diff?lb=…&rb=…  (legacy M1)   ─┘
  → cells[].setSide / addCell / …      (frontend/src/stores/diff.ts)
  → debounced fetchCell(i)
  → gitShow(box, dir, path, ref)        (frontend/src/api/http.ts → /api/v1/boxes/:name/git/show)
  → cell.{left,right}.content
  → <DiffViewer> per cell               (frontend/src/components/DiffViewer.vue)
```

URL writes are debounced 400 ms; per-cell fetches are debounced 400 ms (no manual "Compare" button — typing a complete config triggers a fetch automatically).

## Cell shape

```ts
interface DiffCellState {
  id: string;           // stable, client-side, for v-for keys
  left: SideState;
  right: SideState;
  status: "idle" | "loading" | "ready" | "error" | "binary";
  error: string | null;
}

interface SideState {
  config: { box, directory, ref, path };
  content: string;
  status: "idle" | "loading" | "loaded" | "missing" | "error";
  error: string | null;
  truncated: boolean;
}
```

Key states:

- **`missing`** — `git_show` returned 404 (the file doesn't exist at that ref). Surfaced as a tag in the cell header; the side is treated as empty content so the other side still renders. This is how adds and deletes display correctly.
- **`error`** — anything else from the API. Cell flips to error if BOTH sides fail or if only ONE side is configured AND it failed.
- **`binary`** — detected client-side via a null-byte sniff on the first 8 KB of either side's content. CodeMirror would garble binary; we show a warning instead.
- **`truncated`** — server-side `git_show` caps at `MAX_SHOW_BYTES` (2 MB). When content length hits the cap we show a "truncated" badge. Don't trust this for correctness — it's a UI hint.

## Command grammar

The parser is a **pure function** at `frontend/src/utils/diffCommandParser.ts`. No Vue, no side effects — easy to unit-test. Returns a tagged union `Command | ParseError`. The view layer dispatches the action.

| Command | Effect |
|---|---|
| `:add` | Append a cell. Both sides prefill from the most recent cell's box+dir+path (refs cleared). |
| `:add <left>` | Same, but seed the left side from the spec (overrides prefill). |
| `:add <left> <right>` | Seed both sides from specs. |
| `:rm <n>` | Remove the n-th cell (1-indexed; matches header label). |
| `:swap <n>` | Flip left/right of cell n. |
| `:swap <n> <m>` | Swap cells n and m. |
| `:repo <box> <dir>` | Set a default repo applied to future `:add` prefills when the corresponding side field is empty. |
| `:clear` | Reset to a single empty cell. |
| `:help` / `?` | Open the help overlay. |

Side syntax: `box:directory@ref:path`. Any segment may be omitted; the structural colons stay so the parser knows which slot each value belongs to. Quoted segments support spaces. Colons inside `path` are preserved (path is the tail after the FIRST `:` after `@`).

Aliases the parser also accepts: `:remove`, `:delete` for `:rm`; `:reset` for `:clear`; `:h`, `:help`, `?`, `help`, `clear`, `rm` (no leading colon) all work.

## Server-side notebooks (M3)

For "real" sharing (clean URLs in Slack/Teams), notebooks can be persisted server-side. The URL bar becomes `/app/diff/n/<id>` (an ~11-char `secrets.token_urlsafe(8)` slug) instead of `/app/diff?n=<4KB-base64>`.

### REST contract (`sshler/api/diff.py`)

| Method | Path | Body / Response |
|---|---|---|
| POST | `/api/v1/diff/notebooks` | `{envelope: NotebookEnvelope, label?: str}` → `DiffNotebookFull` |
| GET | `/api/v1/diff/notebooks` | `{notebooks: DiffNotebookMeta[]}` (list endpoint — **no envelope**, just metadata) |
| GET | `/api/v1/diff/notebooks/{id}` | `DiffNotebookFull` (full envelope) |
| DELETE | `/api/v1/diff/notebooks/{id}` | `{ok, removed}` |

ID regex: `^[A-Za-z0-9_-]{8,32}$`. Envelope validation: `v == 1` and `cells` is a list — the rest is opaque to the server (`envelope_json` is stored as a JSON string in SQLite). Hard cap: 1 MB per envelope. All routes gated by `Depends(deps.require_token)` — no per-author binding; any token-bearer reads/writes/deletes any notebook.

### Persistence (`sshler/state.py`)

```python
class DiffNotebook(SQLerModel):
    id: str                # secrets.token_urlsafe(8) — ~11 chars
    label: str
    envelope_json: str     # opaque to the server
    cell_count: int        # denormalized hint for the list endpoint
    created_at: float
    updated_at: float
```

Helpers: `save_diff_notebook_async`, `get_diff_notebook_async`, `list_diff_notebooks_async`, `delete_diff_notebook_async`.

### Immutability rule (HARD STOP)

**Saves are immutable.** Every POST creates a NEW id, even if the envelope is identical to an existing one. There is no PUT/upsert route — adding one would break shared links. To "edit" a server-loaded notebook the client forks: any mutation (setSide / addCell / removeCell / swapCells / swapSides / setDefaultRepo / clearAll) automatically clears `serverId` in the store. The URL drops back to `/app/diff?n=<base64>`. Hitting "Save & share" again issues a new id.

Do not add a PUT route. Do not add an upsert mode. If you find yourself reaching for one, the cost of "shared links can never break" wins — the user can save a new version and delete the old one.

### Frontend store integration

`stores/diff.ts` adds `serverId: string | null`. Routes that produce it:
- `hydrateFromEnvelope(env, sourceServerId)` — called by `DiffView` when the route is `/app/diff/n/:id`.
- `markServerSaved(id)` — called after `createDiffNotebook` returns.

Every mutating action clears `serverId`. `toQuery()` returns `{}` when `serverId` is set, so the URL stays `/diff/n/<id>` instead of echoing `?n=`.

### Saved-notebook drawer

`<DiffNotebookDrawer>` (component) combines two sources:
- **Server section** — `listDiffNotebooks(token)` results, newest-first. Each row has a Load button (emits `load-saved` with the id; view navigates to `/diff/n/<id>`) and a Delete button (NPopconfirm → `deleteDiffNotebook(id, token)`).
- **Recent on this device** — `useDiffHistory().list()` from M2 localStorage. Each row has Load (emits `load-recent` with the b64; view navigates to `/diff?n=<b64>`) and Delete.

Empty state fires when BOTH sections are empty.

### URL precedence

`route.params.id` (path) > `route.query.n` (base64) > legacy flat keys (`?lb=&rb=`).

If `/diff/n/:id` is requested and the server returns 404, the view toasts and replaces the URL with `/diff` (empty state). If `?n=<base64>` is malformed (M2 silently swallowed this), the view now toasts `diff.toast.url_decode_failed` and clears the bad query.



```
?n=<base64-url(JSON.stringify({v: 1, cells: [{l, r}, …], def?: {b, d}}))>
```

- `v` is the envelope version. Bump if the shape changes (M3+ should migrate the v=1 form, not break it).
- `cells[].l` and `cells[].r` are full `SideSpec` objects (`box, directory, ref, path`).
- `def` is optional (`{box, directory}` = default repo).
- Base64 is base64-**url** (`-_` instead of `+/`, padding stripped) so the value is URL-safe without further encoding.
- Round-trip helpers: `notebookToBase64(cells, def)` and `tryDecodeNotebook(b64)` in `stores/diff.ts`.

**Legacy compatibility:** M1 share-links used flat keys (`?lb=local&ld=/r&lr=main&lp=a.ts&rb=…&…`). `hydrateFromQuery` checks `?n=` first; if absent, falls back to flat keys and produces a one-cell notebook. Existing M1 links stay alive.

## Keyboard

Bound at the view level; **ignored when an `<input>`/`<textarea>` is focused** (so typing in the path field doesn't open the help overlay).

| Key | Action |
|---|---|
| `c` or `:` | Focus the command bar. |
| `?` | Toggle help overlay. |
| `j` / `k` | Scroll to next / previous cell. Picks the nearest cell to viewport center as the anchor. |
| `Esc` (in command bar) | Blur + clear current input. |

## Per-cell auto-fetch

`scheduleFetch(i)` debounces 400 ms. A cell only loads when BOTH `box` AND `path` are non-empty on at least one side. Otherwise the cell stays `idle` (no spinner, no 404). Typing a partial path doesn't trigger a fetch storm.

## History

`useDiffHistory()` at `frontend/src/composables/useDiffHistory.ts` keeps the last 10 distinct notebooks in `localStorage["sshler:diff:history"]` (versioned shape `{v:1, notebooks: [{b64, label, savedAt}, ...]}`). Records dedupe by `b64` and move re-records to the front. Storage failures are silent — this is a UX nicety, never a correctness path.

## When extending

- **New command:** add a parser case in `diffCommandParser.ts` AND a parse-spec case AND a dispatch case in `DiffView.vue#applyCommand`. The help overlay reads its command list from a static array in `DiffHelpOverlay.vue` — update that too.
- **New cell state:** extend `CellStatus` in `stores/diff.ts`, add a render branch in `DiffCell.vue`, add an i18n key in both `en.ts` and `ja.ts`, add a Vitest case.
- **New URL field:** bump `NOTEBOOK_VERSION`, add a `tryDecodeLegacy` for v=1 → v=2 migration, write a round-trip test.
- **Real `git_diff` backend endpoint (M4):** add `POST /api/v1/boxes/{name}/git/diff` taking `{directory, left:{ref,path}, right:{ref,path}}`, return unified diff. Keep `gitShow`-based blob composition as the default — the unified-diff path is only for cases the blob path can't handle (renames, mode changes, submodules).
- **Path autocomplete (M4):** add `GET /api/v1/boxes/{name}/git/ls?directory=&ref=` returning `git ls-tree -r --name-only`. Wire into `DiffSidePicker.vue` as an `<NAutoComplete>` on the path input.

## Surfaces

- **`/app/diff`** — `frontend/src/views/DiffView.vue`. The whole page: command bar + N cells + Add Diff button + Save&Share button + Saved drawer trigger + help overlay.
- **`/app/diff/n/:id`** — same view component, hydrates from `getDiffNotebook(id)` on mount. The view branches on `route.params.id` vs `route.query.n` vs nothing.
- **`<DiffCell>`** — `frontend/src/components/diff/DiffCell.vue`. One cell: sticky header, embedded pickers, body switches on `status`.
- **`<DiffSidePicker>`** — `frontend/src/components/diff/DiffSidePicker.vue`. Four inputs (box, directory, ref, path). Ref input is `<NAutoComplete>` fed by `gitBranches`.
- **`<CommandBar>`** — `frontend/src/components/diff/CommandBar.vue`. Always visible. Owns the input buffer; delegates parsing to `diffCommandParser`.
- **`<DiffHelpOverlay>`** — modal with command + shortcut tables.
- **`<DiffNotebookDrawer>`** — right-side `<NDrawer>`. Lists server saves + local recents. Emits `load-saved <id>` and `load-recent <b64>` to the view, which navigates accordingly.

## i18n

Flat dot-notation keys under `diff.*` (UI), `diff.command.*` (bar), `diff.help.*` (overlay), `diff.history.*` (history dropdown), `nav.diff`. Mirrored between `frontend/src/locales/en.ts` and `frontend/src/locales/ja.ts`.

## Testing patterns to reuse

- `frontend/src/utils/diffCommandParser.spec.ts` — pure function, no Vue. Table-driven happy + error cases.
- `frontend/src/stores/diff.spec.ts` — Pinia store with mocked `gitShow`. Covers cell ops, fetch, URL round-trip, legacy migration.
- `frontend/src/components/diff/DiffCell.spec.ts` — stubs naive-ui + phosphor + `<DiffViewer>` + `<DiffSidePicker>`; asserts per-state surfaces.
- `frontend/src/composables/useDiffHistory.spec.ts` — uses real `localStorage`; covers dedupe, cap, malformed-storage recovery.

## Gotchas

- **Cell ids are client-only.** Don't try to serialize `id` into the URL — generate a new one on hydrate. The user expects URL pastes to produce a stable view; cell ids are an implementation detail for `v-for` keys.
- **Auto-fetch + typing.** If the path field is being typed quickly, the 400 ms debounce keeps us from spamming the backend. But the FIRST keystroke into a previously-empty field instantly invalidates the cell's `ready` status (resets to `idle`) — this is intentional, so the user doesn't see a stale diff with the new path label.
- **`isComplete(side)` requires box AND path.** Ref is optional (defaults to `HEAD` server-side). Directory is optional (defaults to `/`). Adjust both halves if you change this rule.
- **CodeMirror MergeView is heavy.** With 10+ cells, the page can get sluggish. M4 plans to lazy-mount diffs via IntersectionObserver. If you hit this before then, that's where to start.
