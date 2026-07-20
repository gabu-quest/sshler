# Diff Notebook — user guide

A scrollable workspace for reviewing N file diffs at once across boxes, repos, refs, and commits. Lives at `http://localhost:8822/app/diff`.

## The core idea

Each "diff" in the notebook is one `(left ↔ right)` file comparison. The two sides are fully independent — you can compare:

- The same file at two different branches/commits.
- Two different files at the same ref.
- Files on two different boxes (e.g., prod vs staging).

Stack as many diffs as you want; the page scrolls. Each diff has its own pickers right in the cell — no modals, no extra clicks.

## Fastest path to a working diff

1. Open `/app/diff`. There's one empty diff at the top.
2. On the **left** side, set:
   - **Box**: `local` (or any configured remote)
   - **Directory**: the path to your git repo (e.g., `/home/you/projects/sshler`)
   - **Ref**: a branch / tag / SHA (autocompletes from `git branches`)
   - **Path**: the file inside the repo (e.g., `README.md`)
3. Do the same on the **right** side, usually with a different ref.
4. The diff auto-loads as soon as both `box` and `path` are filled on at least one side. No "Compare" button.

That's it. To add a second diff, click **+ Add diff** at the bottom — it prefills box/dir/path from the most recent diff (refs cleared, so you can quickly compare the next file at the same two branches).

## Side syntax (for the command bar)

When you type commands into the bar at the top, each "side" is one token shaped like:

```
box:directory@ref:path
```

Any segment may be empty — the structural colons stay so the parser knows which slot each value belongs to. Examples:

```
local:/srv/app@main:src/main.ts          # full
local:/srv/app@feat/login:src/main.ts    # different ref
:@v2:README.md                           # no box/dir; ref + path only
local:/srv/app@:src/main.ts              # no ref (defaults to HEAD)
```

Quoted paths support spaces: `"local:/srv/app@main:src/file with space.ts"`.

## Command reference

The bar at the top is always there. Type and press Enter.

| Command | What it does |
|---|---|
| `:add` | Append a new diff, prefilled from the most recent one (refs cleared) |
| `:add <left>` | Append a diff with the left side seeded from your spec |
| `:add <left> <right>` | Append a diff with both sides seeded |
| `:rm <n>` | Remove the n-th diff (1-indexed; matches the header label) |
| `:swap <n>` | Flip left and right sides of the n-th diff |
| `:swap <n> <m>` | Swap diffs n and m (reorder) |
| `:repo <box> <dir>` | Set a default repo applied to future `:add` prefills when the corresponding side is empty |
| `:clear` | Reset to a single empty diff |
| `:help` or `?` | Show the help overlay |

Aliases: `:remove` / `:delete` for `:rm`, `:reset` for `:clear`. The leading colon is optional — `rm 2` works the same as `:rm 2`.

## Keyboard shortcuts

Bound at the page level — but **ignored while you're typing in any input** (so they don't fight with your path field):

| Key | Action |
|---|---|
| `c` or `:` | Focus the command bar |
| `?` | Toggle the help overlay |
| `j` / `k` | Scroll to the next / previous diff |
| `Esc` | Inside the command bar: clear input and unfocus |

## Saving & sharing

Two ways the notebook persists:

### 1. URL state (automatic, every change)

The URL updates as you edit. Two forms:

- **`/app/diff?n=<base64>`** — the entire notebook encoded into the URL. Self-contained, no server save. Just reloading restores everything. Good for quick share-by-paste.
- **`/app/diff/n/<id>`** — the short URL after you hit "Save & share". ~11-char slug. Much nicer to share than a 4KB base64 blob.

Both forms reload identically. Bookmark either.

### 2. Server save ("Save & share" button)

Click **Save & share** in the page header. It:

1. POSTs the current notebook to the server (SQLite-backed, lives across restarts).
2. Navigates to `/app/diff/n/<new-id>`.
3. Copies that short URL to your clipboard.

**Saves are immutable.** Each "Save & share" creates a new id. Editing a notebook you opened from `/app/diff/n/<id>` automatically **forks** — the URL drops back to `?n=<base64>` and your `serverId` clears. The original `/diff/n/<id>` keeps the original version forever. To "save your edits", hit Save & share again — you get a new id. The old link doesn't change; the new link is its own thing.

This means: every shared link you've ever sent to a colleague stays alive. You never accidentally mutate a published notebook.

To clean up: open the **Saved** drawer (header button) and delete entries you no longer want.

## The Saved drawer

Click **Saved** in the page header. Two sections:

- **Saved on the server** — Cross-machine, persistent. Click Load to navigate to the short URL. Delete is permanent.
- **Recent on this device** — Local-only, automatic. Every notebook you view through `?n=<base64>` is recorded here (last 10, deduped). Local delete only.

The drawer hides empty sections; if both are empty you'll see one overall empty state with a hint.

## "Missing" vs "Error" — what they mean

Each cell side has a small status tag:

- **(no tag)** — fetched and rendered fine.
- **`left missing at ref`** / **`right missing at ref`** — `git show <ref>:<path>` returned 404. The file genuinely doesn't exist at that ref. **This is not an error.** Adds and deletes naturally render with one side empty, and that's what these tags say. If you see this on a path you expected to exist, double-check the ref or the path.
- **Failed to load diff** (red alert) — a real error (bad ref name, permission denied, connection refused). The cell header shows the message.
- **Binary file** (yellow alert) — content has a null byte; CodeMirror would garble it, so the diff is hidden. Compare in a real diff tool if you need it.
- **truncated** — the file hit the server-side 2 MB cap. The diff renders but is incomplete.

## Common workflows

### Reviewing a feature branch

Quick three-cell pattern:
1. Set up the first diff: `local:/path/to/repo@main:src/main.ts` ↔ `local:/path/to/repo@feat/X:src/main.ts`
2. Hit **+ Add diff** for each additional file you want to review. The new cell inherits everything except the refs, so you only type the new path.
3. Or use the bar: `:add :@feat/X:src/utils.ts` adds a diff with no left side (using prefill from above) and just the right path/ref set.

### Comparing prod vs staging

Set left to `prod` box, right to `staging` box, same path on both. Each cell can target a different file — useful for spotting config drift.

### "What changed between two tags?"

For each file you care about: one cell, left = `@v1.2.0:<path>`, right = `@v1.3.0:<path>`. Add cells with `+ Add diff` — they inherit the path, you only adjust if a different file.

## Troubleshooting

**Branch autocomplete is empty.** The autocomplete only triggers once `box` AND `directory` are filled, and the directory must be inside a git repo. If you typed a non-git directory, branches won't populate — paths still work, refs just won't suggest.

**"That URL couldn't be decoded" toast.** You pasted a `?n=<garbage>` URL or someone gave you a corrupted one. The UI clears the URL and gives you a fresh notebook.

**"Notebook X not found" toast.** Someone shared a `/diff/n/<id>` URL that was later deleted from the server. Same recovery: fall back to a clean notebook.

**Cell is stuck showing the wrong content after I edited the path.** The fetch is debounced 400 ms. Wait, or press the swap-sides button (the back-and-forth arrow in the cell header) which forces a refresh.

**Performance feels sluggish at 10+ diffs.** CodeMirror is heavy. Future work (M4) will lazy-mount diff bodies when scrolled into view. For now, split big reviews into two notebooks and save them both.

## CLI (planned, M4)

A `sshler diff <left> <right> [<left> <right>...]` subcommand is on the M4 roadmap. It'll print a `/app/diff/n/<id>` URL given pairs on the command line — so you can write `sshler diff …` in a shell script that builds a review for a colleague.

Not built yet. Use the URL bar in the meantime.

## Where the code lives

For developers / contributors:

- Frontend route: `/app/diff` and `/app/diff/n/:id` → `frontend/src/views/DiffView.vue`
- Store: `frontend/src/stores/diff.ts`
- Command parser: `frontend/src/utils/diffCommandParser.ts`
- Backend persistence: `sshler/api/diff.py` + `DiffNotebook` model in `sshler/state.py`
- Reference for Claude / future maintainers: `docs/skills/diff-notebook/SKILL.md`
