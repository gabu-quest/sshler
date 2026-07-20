# sshler

> 🌐 **日本語の README はこのページの末尾にあります。** → [日本語版へジャンプ](#japanese) / *Japanese README is at the bottom of this page.*

[![PyPI version](https://img.shields.io/pypi/v/sshler.svg)](https://pypi.org/project/sshler/)
[![Python versions](https://img.shields.io/pypi/pyversions/sshler.svg)](https://pypi.org/project/sshler/)
[![CI](https://github.com/gabu-quest/sshler/actions/workflows/ci.yml/badge.svg)](https://github.com/gabu-quest/sshler/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

sshler is a lightweight, local-only web UI that lets you browse remote files over SFTP and jump into tmux sessions in your browser — without installing anything on the remote host.

## Quick Start

```bash
# Install
pip install sshler

# Run (opens browser automatically)
sshler serve

# Or with development setup
uv sync --group dev
cd frontend && pnpm install && pnpm build && cd ..
sshler serve
```

The app opens at `http://127.0.0.1:8822` and redirects to the Vue SPA at `/app/`.

## Features

### Core Features
- **Cross-platform**: Runs on Windows 11, macOS, and Linux (anywhere with Python 3.12+)
- **Local workspace**: Browse your own filesystem and launch native tmux sessions alongside remote hosts (uses WSL tmux on Windows, native tmux on Linux/macOS)
- **SSH integration**: Uses your existing SSH keys and honors OpenSSH aliases
- **Terminal in browser**: Opens `tmux new -As <session> -c <dir>` on the remote host and bridges it via WebSocket + xterm.js
- **File management**: Vue-based file browser with preview, edit, delete, and "Open Terminal Here"
- **Auto-configuration**: Creates starter config on first run
- **Alias resolution**: Falls back to `ssh -G` when DNS fails; reset overrides with one click
- **File operations**: Preview, edit (≤256 KB), and delete files with CodeMirror editor

### Modern UI Features

**🎨 Theme & Appearance**
- **Dark/Light Theme Toggle** - Seamless theme switching with system preference detection
- **PWA Support** - Install as a standalone app with offline capabilities and app icons

**⌨️ Keyboard & Navigation**
- **Command Palette (Cmd/Ctrl+K)** - Quick access to all features with fuzzy search
- **Keyboard Shortcuts** - Press `?` to see all available shortcuts
- **Global Search (Cmd/Ctrl+Shift+F)** - Search across all files in all boxes

**📁 Enhanced File Management**
- **Drag & Drop Upload** - Drop files directly into the file browser
- **Bulk Operations** - Select multiple files with Shift+Click and Cmd/Ctrl+Click
- **Inline Rename (F2)** - Rename files without opening a modal
- **Context Menus** - Right-click for quick actions
- **Recent Files & Bookmarks** - Quick access to frequently used locations
- **File Preview Enhancements** - Toggle line numbers and word wrap in file viewer
- **Directory Download** - Download any directory as a .zip with size warnings for large dirs

**🔀 Diff Notebook**
- **`/app/diff` — multi-cell diff workspace** - Stack N file diffs vertically and review them all at once. Each diff is one `(left ↔ right)` file pair; sides are fully independent (different boxes, repos, refs). Auto-loads as you type; no Compare button.
- **Command bar** - `:add`, `:rm 2`, `:swap 1 3`, `:repo local /path`, `:clear`. Side syntax: `box:dir@ref:path`. Press `c` to focus from anywhere, `?` for help, `j`/`k` to scroll between diffs.
- **Shareable URLs** - `?n=<base64>` for self-contained share-by-paste, or hit **Save & share** for a short `/app/diff/n/<id>` URL backed by SQLite. Saves are immutable — editing a shared notebook forks. Open the **Saved** drawer to manage server saves + local recents.
- See [`docs/diff-notebook.md`](docs/diff-notebook.md) for the full user guide.

**🤖 Claude Session Dashboard**
- **`/app/claude` — resume Claude Code sessions** - Lists every resumable [Claude Code](https://claude.ai/code) conversation on this machine (read from `~/.claude/projects/*/*.jsonl`) and one-click resumes any of them into a browser terminal. Titles mirror Claude Code's own `/resume` picker: your `/rename` title first, then the AI-generated title, then the first prompt.
- **Grouped by git repo, collapsible** - Sessions are grouped by repo root (a conversation that ran in `repo/subdir` shows under `repo`, matching `/resume`), each in a collapsible section tagged with the same emoji the rest of the app uses for that directory. Sections start collapsed; a filter auto-expands matches, and Expand-all / Collapse-all buttons are one click away.
- **Timeline with metadata** - Each repo's sessions sit on a timeline with dots colored by recency (green <1h, blue <1d, amber <1wk, grey older), plus per-session tags for the subdirectory, git branch, transcript size, and Claude Code version.
- **Resume in place or jump** - **Resume** opens the conversation in the background — in the repo's top-level tmux session — and keeps you on the list; the ↗ button opens it *and* switches to the terminal. Re-resuming a session that's already open just focuses its tab, never restarting a running Claude.
- **Configurable resume command** - Defaults to `claude --resume {id}`; override it globally or per-session (inject your own launcher/flags) — `{id}` is substituted with the validated session id. Saved in your browser.
- **Shared tmux session naming** - Local sessions are named after the directory basename (the same convention the `ts` tmux-session helper uses), so if sshler is down you can attach the exact same session from a plain terminal.

**📝 Markdown Preview & PDF Export**
- **One-Click PDF Download** *(optional)* - With the `[pdf]` extra installed, every text file gets a PDF button. Works from the preview modal, files-list right-click, and the multi-select toolbar (renders selection one file at a time). Backend uses headless Chromium via Playwright, so output matches the print preview pixel-for-pixel — mermaid SVGs stay vector, code blocks stay crisp. See [Install](#install) below to enable.
- **Live Markdown Render** - Toggle source/rendered view on any `.md` file via the eyeball button
- **Mermaid Diagrams** - Flowcharts, sequence diagrams, state diagrams, timelines, class diagrams etc. render inline from ` ```mermaid ` fenced blocks. Mermaid is lazy-loaded — no bundle cost unless you actually view a markdown file with diagrams.
- **Embedded Images** - `![alt](./diagram.png)` and `![alt](../assets/foo.jpg)` resolve against the file's directory and are fetched via the preview API, then rewritten to `data:` URLs. Works for any image format the backend recognises (png/jpg/gif/webp/svg/etc.). Absolute remote paths (`/srv/docs/foo.png`) also work. Inlined images render in the modal AND survive being cloned into the print window — printed PDFs include them. Oversized images (above the preview byte limit) stay broken, same as opening them directly. `http(s)://` URLs are left alone — they load in the modal but may not appear in print depending on the print window's network access.
- **HTML Entity Decode** - Source authors can write `&lt;` / `&ge;` / `&amp;` in mermaid blocks (common dodge for markdown's `<` handling); they're decoded before mermaid sees them so the lexer doesn't choke.
- **Print to PDF** - Printer-icon button in Render mode opens a styled print window; pick "Save as PDF" in the browser print dialog. Mermaid SVGs print as vectors, stay crisp at any zoom.
- **Smart Print Layout** - A4 portrait with 18mm margins. Tall diagrams shrink to fit one page (max-height 230mm). Wide diagrams (aspect ratio > 1.4:1) auto-rotate to landscape on their own page. Print-mode CSS forces white background + black text regardless of app theme.
- **Page-Break Awareness** - `page-break-inside: avoid` on code blocks, blockquotes, mermaid diagrams, and tables keeps them from splitting across pages.
- **Inline HTML in Markdown** - Authored `<div>`/`<span>` blocks with inline styles (banners, callouts, badges) render in preview AND print. `print-color-adjust: exact` is forced so colored backgrounds aren't silently dropped by the browser's default toner-saving behavior.
- **Theme-Independent Print** - Print output always uses a light palette regardless of whether you're viewing in dark mode — avoids muddy grey-on-grey headings and table headers.
- **Print Any Text File** - The Print button in the preview modal works on any non-image file. Markdown goes through the rendered-with-diagrams path; code/text files print as monospace source with the file path in a header.
- **One-Click Print from File List** - Right-click any file → **Print**. Opens the preview modal and auto-fires the print dialog once content loads — no manual Render → Print step.

**🖥️ Terminal Features**
- **Multi-pane Layouts** - Split terminal horizontally, vertically, or in a grid
- **Session Persistence** - Restore your terminal layout on reload
- **Crash Recovery** - Periodic snapshots of tmux window state (every 30s); if WSL/system crashes, a recovery modal offers to recreate your sessions with the same window layout and working directories
- **Live Dead-Session Detection** - Detects OOM-killed or crashed tmux sessions without needing a restart; recovery modal appears automatically
- **Snapshot Freshness Indicator** - Blue dot next to the connection indicator pulses when snapshots are active, fades to grey as they age
- **Clickable File Paths** - File paths and `file://` URLs in terminal output are clickable links; file:// URLs copy to clipboard with a toast
- **Terminal Notifications** - Desktop notifications for long-running commands
- **Connection Status** - Real-time connection health indicators
- **Command Snippets** - Save and quick-insert frequently used commands per box or globally
- **Port Forwarding** - Visual SSH tunnel management (local/remote) per box
- **Per-Box Terminal Themes** - Color-code terminals by environment (prod=red, staging=green, etc.)
- **Per-Box Emoji Icons** - Deterministic emoji assigned per box for quick visual identification
- **Active Box Context** - Navigation links remember your current box when switching between views

**📱 Mobile & Touch Support**
- **Touch-Optimized** - 44px minimum touch targets for easy tapping
- **Swipe Gestures** - Swipe right to navigate back in file browser
- **Long-Press Context Menu** - Long-press files for quick actions (500ms)
- **Pull-to-Refresh** - Pull down to reload the current directory
- **Responsive Design** - Optimized layouts for tablets and phones
- **Virtual Keyboard Support** - Terminal automatically adjusts when mobile keyboard appears
- **Orientation Change** - Smooth terminal resize when rotating device
- **iOS Input Optimization** - 16px font size prevents auto-zoom on focus
- **Passive Touch Events** - Smooth scrolling with no jank
- **Mobile Fullscreen** - Minimal UI in fullscreen for maximum typing space

**📱 Mobile Terminal Input Bar**
- **Quick Keys** - Phosphor icon buttons for keys hard to type on mobile
- **Arrow Navigation** - ▲▼◀▶ for menu navigation (Claude Code, vim, etc.)
- **Enter/Tab** - Confirm selections and autocomplete
- **Escape/Stop** - Interrupt Claude Code turns or cancel operations (yellow)
- **Ctrl+C** - Kill processes (red - danger indicator)
- **Tmux Scroll Mode** - 📜 enters copy mode, ⏫⏬ for page up/down (orange group)
- **Ctrl+D** - Graceful exit/EOF (teal)
- **Help Legend** - Tap `?` to see what each button does
- **Color-Coded** - Visual grouping by function (blue=confirm, red=danger, orange=scroll)

**📱 Ultra-Thin Mobile Header**
- **14px Height** - Maximum terminal real estate (JuiceSSH-inspired)
- **Live Stats** - CPU/MEM percentages with color indicators (green/orange/red)
- **Minimal Chrome** - Just logo and stats, no buttons

**📊 Global Progress Bars**
- **Push from any script** - `sshler progress push <name> <current> <total>` from a build/CI/deploy script, the bar appears in the browser instantly. No setup, no project to configure — uses your running sshler instance.
- **Watch from anywhere** - A thin strip under the header shows the current box's bars on every page. Files, Terminal, Settings, doesn't matter — the bars are always in view.
- **Per-box subscriptions** - Bars exist server-side (global pool); each box keeps its own view of them. Subscribe `deploy` while you're on the `sshler` box, switch to `maintenance`, and the strip clears — switch back and it returns. Hit the `+` on the strip to open a picker and toggle bars for the current box. The `/app/progress` management page lists every bar with per-row subscribe toggles (scoped to the active box), delete buttons, and stale-row highlighting.
- **Bars stay until you dismiss them** - Subscribed bars persist on the strip through every status, including `done` (the green check is the payoff of subscribing). Clear one with the picker, by unsubscribing, or by deleting it from the management page. A bar flashes white once the moment it finishes.
- **Rich tooltips + metadata** - Hover any bar for a tooltip with its full label, status, count, timestamp, and any custom metadata a script attached (`--meta key=value`). Metadata is fault-tolerant — if a script sends garbage, the bar keeps advancing and just shows a small error note instead of breaking.
- **Honest percentages** - The displayed percent is rounded **down**: a 3300-step build at 3299/3300 reads 99%, never a misleading 100% until it's actually done.
- **Live updates** - One WebSocket (`/ws/progress`) fans out every upsert/delete to every connected browser tab. Multi-machine? Open `/app/progress` on your phone too.

**♿ Accessibility**
- **WCAG 2.1 AA Compliant** - Semantic HTML, ARIA labels, keyboard navigation
- **Screen Reader Support** - Proper focus management and announcements
- **Reduced Motion** - Respects `prefers-reduced-motion` system setting
- **High Contrast** - Clear visual hierarchy and color contrast

## Install

### PyPI (recommended)

```bash
pip install sshler

# Launch once to create the config + systemd/service assets
sshler serve
```

Requires Python **3.12+**.

#### Optional: PDF export

The PDF download buttons appear only when Playwright + headless Chromium are available. To enable:

```bash
pip install "sshler[pdf]"
playwright install chromium
# (one-time, on Linux you may also need: playwright install-deps chromium)
```

The base install works fine without this — sshler runs normally, the PDF buttons just stay hidden.

### Development

```bash
uv pip install -e .
# or: pip install -e .
```

After cloning the repository, install the dev extras and run the usual tooling:

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

E2E smoke test (Playwright):

```bash
uv run playwright install chromium   # one-time browser download
uv run pytest tests/e2e
# or reuse the project venv: .venv/bin/pytest tests/e2e/test_vue_app.py
```

## Run

```bash
sshler serve
```

The app will open `http://127.0.0.1:8822` in your default browser and redirect to `/app/`.

### Building the Frontend

The Vue SPA must be built before running (pre-built in PyPI releases):

```bash
cd frontend && pnpm install && pnpm build
# or use the CLI:
sshler build
```

### Development Mode

For hot-reload development:

```bash
# Terminal 1: Backend
sshler serve --no-browser

# Terminal 2: Frontend dev server  
cd frontend && pnpm dev -- --host --base /app/
# Visit http://localhost:5173/app/
```

Or use the combined dev command:

```bash
sshler dev  # Runs both servers with hot-reload
```

### Pushing progress bars

Any script on the same host can push a progress bar that shows up live in the browser:

```bash
sshler progress push build 0 100 --label "frontend build" --color blue
# ... your work ...
sshler progress push build 50 100 --color blue --meta stage=compile
sshler progress push build 100 100 --status done --color green

sshler progress list
sshler progress delete build
```

Available flags: `--label TEXT`, `--color NAME|#hex`, `--status running|done|failed|cancelled`, `--url URL`, `--token TOKEN`.

**Metadata** — attach arbitrary fields shown in the bar's hover tooltip: `--meta KEY=VALUE` (repeatable), or `--meta-json '{"warnings":3}'`. By default each push **replaces** the metadata bag; use `--merge` to add to it, `--clear-meta` to empty it. A push with no metadata flag leaves existing metadata untouched, so a tight progress loop won't wipe it. Malformed metadata never blocks the push — the bar still advances and the tooltip shows the error.

Names must match `^[A-Za-z0-9._:-]{1,64}$` (push to the same name = update the same bar). Color names recognised: `blue green red yellow orange purple pink teal`; any CSS color (`#3b82f6`, `rgb(...)`) also works.

**Token discovery** — When sshler starts, it writes the active CSRF token to `<config_dir>/runtime-token` (`~/.config/sshler/runtime-token` on Linux, mode 0600). The CLI auto-reads it, so local pushes need no setup. Remote / cross-machine pushers can override via `--token` or `$SSHLER_TOKEN`. URL discovery similarly defaults to `http://127.0.0.1:8822`, overridable via `--url` / `$SSHLER_PROGRESS_URL`.

Subscriptions are **per box**: open a box (Files/Terminal), then hit the `+` on the header strip — or use `/app/progress` — to subscribe to the bars you want. Each box remembers its own set, so `sshler` and `maintenance` can show different bars. The thin strip under the header renders the active box's subscribed bars on every page.

See `examples/progress-bar-build-watcher.sh` for a copy-pasteable demo loop including `trap` → `--status failed` on error.

### Key Shortcuts

- **Cmd/Ctrl+K** - Command palette
- **Alt+F** - Go to Files
- **Alt+T** - Go to Terminal
- **Alt+B** - Go to Boxes
- **Alt+L** - Go to Claude Sessions
- **?** - Show all keyboard shortcuts

## Configuration

sshler reads your existing OpenSSH config (`~/.ssh/config`) and shows every concrete `Host` entry automatically. Any favourites, default directories, or custom hosts you add through the UI are stored in a companion YAML file.

A config file is created on first run:

- Windows: `%APPDATA%\sshler\boxes.yaml`
- macOS/Linux: `~/.config/sshler/boxes.yaml`

Example:

```yaml
boxes:
  - name: my-server
    host: server.example.com      # literal IP/FQDN
    ssh_alias: my-server          # optional: resolves via `ssh -G my-server`
    user: alice
    port: 22
    keyfile: ~/.ssh/id_ed25519
    favorites:
      - /home/alice
      - /home/alice/projects
      - /var/www
    default_dir: /home/alice
```

> Tip: Set `default_dir` if your home path isn't `/home/<user>`.
> If you rely on an OpenSSH alias, add `ssh_alias:` and sshler will run `ssh -G` to expand it when DNS fails.

### Resetting overrides

Boxes imported from SSH config show a highlighted border and "Refresh" button. If you change something in `~/.ssh/config`, hit Refresh to drop any stored overrides (host/user/port/key) so the new settings take effect without editing `boxes.yaml`.

### Adding custom boxes

Hit "Add Box" in the UI to define a host that isn't in your SSH config (for example, a throwaway Docker container). Fields you leave blank fall back to your SSH defaults.

### Security model (important)

**Localhost (127.0.0.1):** No password required. sshler binds to localhost by default and uses a random `X-SSHLER-TOKEN` for CSRF protection.

**Non-localhost:** Password REQUIRED. If you bind to `0.0.0.0` or any non-localhost address, you MUST configure authentication:

```bash
# Set up password (recommended - creates hash in .env)
sshler hash-password

# Or use environment variables directly
export SSHLER_USERNAME=admin
export SSHLER_PASSWORD_HASH='$argon2id$...'  # Use sshler hash-password to generate

# Or use CLI flag (not recommended - visible in process list)
sshler serve --host 0.0.0.0 --auth myuser:mypassword
```

**Additional security notes:**
- **Environment variables**: Never commit your `.env` file to version control. Use `.env.example` as a template. The `.env` file may contain sensitive credentials like password hashes.
- File uploads are capped at 50 MB (tunable via `--max-upload-mb`). Uploaded content is never executed server-side.
- SSH connections still honour your system `known_hosts`. Only set `known_hosts: ignore` if you fully understand the risk.
- If you expose sshler beyond localhost, opt-in via `--allow-origin` and add `--auth user:pass` (basic auth). Use it only on networks you trust and put TLS in front (nginx, Caddy, etc.).
- There is no telemetry, analytics, or call-home behaviour.

### CLI options

```bash
sshler serve \
  --host 127.0.0.1 \
  --port 8822 \
  --max-upload-mb 50 \
  --allow-origin http://workstation:8822 \
  --auth myuser:mypassword \
  --no-ssh-alias \
  --log-level info
```

- `--host` (alias `--bind`) sets the bind address (default: `127.0.0.1` for localhost-only). Use `0.0.0.0` to expose on all interfaces, but **only on trusted networks with `--auth` and TLS**.
- `--port` sets the port number (default: `8822`).
- `--allow-origin` can be repeated to expand CORS; combine it with `--auth` if you expose the UI beyond localhost.
- `--auth user:pass` enables HTTP basic authentication (recommended if binding to `0.0.0.0`).
- `--max-upload-mb` sets the upload size limit (default: 50 MB).
- `--no-ssh-alias` disables the `ssh -G` fallback when DNS fails.
- `--token` lets you supply your own `X-SSHLER-TOKEN` (otherwise a secure random value is generated).
- `--log-level` feeds directly into uvicorn (options: `critical`, `error`, `warning`, `info`, `debug`, `trace`).

The server prints the token (and, if enabled, the basic auth username) on startup so you can copy it into API clients or browser extensions.

### Terminal notifications

- Send a bell (`printf '\a'`) from tmux or your shell to flash the browser title and raise a desktop notification whenever the sshler tab is hidden.
- For richer messages use OSC 777: `printf '\033]777;notify=Codex%20done|Check%20the%20output\a'`. The text before the `|` becomes the title; the second part is the body.
- JSON payloads are also supported: `printf '\033]777;notify={"title":"Codex","message":"All tasks finished"}\a'`.
- The first notification prompts the browser for permission. Denying it still leaves the in-app toast and title badge when you return to the tab.

## TLS/HTTPS Deployment

### Why HTTPS Matters

sshler uses secure **httpOnly session cookies** for authentication. While these cookies provide strong security, browsers require the `Secure` flag to be set on cookies when serving over HTTPS. This ensures cookies are only transmitted over encrypted connections.

**For production deployments, HTTPS is strongly recommended.**

### Deployment Options

#### 1. Localhost Development (HTTP)

For local development on `localhost` or `127.0.0.1`, you can disable the Secure cookie flag:

```bash
# .env
SSHLER_HOST=127.0.0.1
SSHLER_PORT=8822
SSHLER_PUBLIC_URL=http://localhost:8822
SSHLER_COOKIE_SECURE=false  # Only for localhost dev!
```

**⚠️ Never use `COOKIE_SECURE=false` in production or on network-accessible interfaces.**

#### 2. Production with Caddy Reverse Proxy (Recommended)

[Caddy](https://caddyserver.com/) is the easiest way to add HTTPS to sshler. It automatically obtains and renews Let's Encrypt certificates.

**Basic Setup:**

1. Install Caddy:
   ```bash
   # Ubuntu/Debian
   sudo apt install caddy

   # macOS
   brew install caddy
   ```

2. Create a Caddyfile:
   ```caddyfile
   # /etc/caddy/Caddyfile or ~/Caddyfile

   sshler.company.internal {
       reverse_proxy localhost:8822
   }
   ```

3. Configure sshler for HTTPS:
   ```bash
   # .env
   SSHLER_HOST=127.0.0.1
   SSHLER_PORT=8822
   SSHLER_PUBLIC_URL=https://sshler.company.internal
   SSHLER_COOKIE_SECURE=true  # Required for HTTPS
   ```

4. Start Caddy:
   ```bash
   # System service
   sudo systemctl start caddy

   # Or run directly
   caddy run --config /etc/caddy/Caddyfile
   ```

5. Access sshler at `https://sshler.company.internal`

**For LAN Deployments (Self-Signed Certs):**

If you're deploying on a local network without a public domain, use Caddy with a self-signed certificate:

```caddyfile
sshler.local {
    tls internal  # Use Caddy's internal CA
    reverse_proxy localhost:8822
}
```

Then configure your browser to trust Caddy's local CA certificate (usually at `~/.local/share/caddy/pki/authorities/local/root.crt`).

**Advanced Caddy Configuration:**

```caddyfile
sshler.company.internal {
    # Automatic HTTPS with Let's Encrypt

    # Optional: Rate limiting for API endpoints
    @api {
        path /api/v1/*
    }
    rate_limit @api 100r/m

    # Stricter rate limiting for login endpoint (recommended)
    @login {
        path /api/v1/auth/login
    }
    rate_limit @login 5r/m

    # Proxy to sshler
    reverse_proxy localhost:8822 {
        # Preserve client IP
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    # Optional: Add security headers
    header {
        Strict-Transport-Security "max-age=31536000;"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
    }
}
```

#### 3. Tailscale Deployment

If you're using [Tailscale](https://tailscale.com/), you can access sshler over your Tailscale network. Tailscale automatically provides HTTPS with MagicDNS.

1. Configure sshler to listen on your Tailscale IP:
   ```bash
   # .env
   SSHLER_HOST=100.64.0.1  # Your Tailscale IP
   SSHLER_PORT=8822
   SSHLER_PUBLIC_URL=https://yourhost.tail-scale.ts.net
   SSHLER_COOKIE_SECURE=true
   ```

2. Enable Tailscale Serve (optional, for HTTPS):
   ```bash
   tailscale serve https / http://localhost:8822
   ```

3. Access sshler at `https://yourhost.tail-scale.ts.net`

**Note:** Tailscale provides network-level encryption, but using HTTPS ensures secure cookies work properly.

#### 4. Other Reverse Proxies

**Nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name sshler.company.internal;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8822;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

**Traefik:**

```yaml
http:
  routers:
    sshler:
      rule: "Host(`sshler.company.internal`)"
      service: sshler
      tls:
        certResolver: letsencrypt

  services:
    sshler:
      loadBalancer:
        servers:
          - url: "http://localhost:8822"
```

### Multi-Instance Deployments

⚠️ **IMPORTANT**: The current session store is **in-memory** and **not suitable for multi-instance deployments** (e.g., behind a load balancer with multiple sshler processes).

**Why this matters:**
- Sessions are stored in process memory
- Each instance has its own independent session store
- Users will lose their session if requests are routed to a different instance
- Session cookies will appear invalid when load-balanced across instances

**For single-instance deployments** (most common):
- ✅ One sshler process behind a reverse proxy (Caddy, Nginx)
- ✅ Systemd service running one instance
- ✅ Docker container (single instance)

**For multi-instance/load-balanced deployments**, you must implement a shared session backend:

**Option 1: Redis (Recommended for Production)**
```python
# Replace SessionStore with Redis-backed implementation
# See sshler/session.py for the interface to implement
```

**Option 2: Database (PostgreSQL, MySQL)**
```python
# Implement SessionStore backed by a database table
# Ensure all instances connect to the same database
```

**Option 3: Sticky Sessions (Not Recommended)**
- Configure load balancer for session affinity based on cookie
- Still requires graceful handling of instance failures
- Not as robust as shared session storage

If you need multi-instance support, please open an issue or submit a PR implementing a shared session backend.

### Security Checklist

When deploying sshler in production:

- ✅ **Use HTTPS** with a valid certificate (Let's Encrypt recommended)
- ✅ **Set `SSHLER_COOKIE_SECURE=true`** in your `.env` file
- ✅ **Set `SSHLER_PUBLIC_URL`** to your actual HTTPS URL
- ✅ **Use strong passwords** (generate with `sshler hash-password`)
- ✅ **Keep `SSHLER_REQUIRE_AUTH=true`** (never disable auth in production)
- ✅ **Bind to localhost** (`SSHLER_HOST=127.0.0.1`) when behind a reverse proxy
- ✅ **Enable firewall rules** to restrict access to trusted networks
- ✅ **Keep sshler updated** to receive security patches

### Network Security Layers

sshler security works in layers:

1. **Transport Security (HTTPS)** - Encrypts all traffic, protects session cookies
2. **Application Auth (Session Cookies)** - Verifies user identity with httpOnly cookies
3. **CSRF Protection** - Origin header validation on state-changing requests
4. **Network Isolation** (Optional) - Tailscale, VPN, or firewall rules

**Recommendation:** Use HTTPS + session auth for most deployments. Add network isolation (Tailscale/VPN) for extra security when accessing over the internet.

### Why Cookie Sessions Instead of JWTs?

**TL;DR**: JWTs solve distributed stateless auth. We don't have that problem. Cookie sessions are simpler, more secure, and revocable.

**Decision rationale:**

1. **Immediate Revocation**
   - Sessions can be invalidated server-side instantly (logout, security breach, admin action)
   - JWTs cannot be revoked without complex deny-lists (which defeats "stateless")
   - Critical for admin tools where you need emergency access control

2. **Simpler Security Model**
   - No key rotation complexity
   - No JWT claims validation edge cases
   - No "where do we store the JWT" bikeshedding (localStorage = XSS vulnerable, cookies = use sessions instead)

3. **Correct Use Case**
   - **JWTs are for**: Service-to-service auth, distributed microservices, mobile apps without cookie support
   - **Sessions are for**: Browser-based apps talking to a single backend (sshler's architecture)

4. **Security Benefits**
   - httpOnly cookies prevent XSS token theft (JavaScript can't access them)
   - SameSite=Lax prevents CSRF attacks
   - Shorter attack window (8-hour default TTL vs typical JWT refresh token patterns)

**When to use JWTs:**
- Microservices passing tokens between services
- Mobile apps that can't use cookies reliably
- Truly stateless APIs serving thousands of independent clients
- Cross-domain authentication (e.g., SSO provider)

**When to use sessions (our case):**
- Browser-based admin tools
- Single backend (or shared session store)
- Need immediate revocation
- Same-origin or tightly controlled CORS deployment

**Bottom line**: We chose the boring, correct solution for browser authentication. If you need JWTs, you need a different architecture first (distributed services, mobile clients, etc.). For a browser-based SSH manager, cookie sessions are the right tool.

## Autostart

### Windows (Task Scheduler)

1. Run `where sshler` to locate the installed executable (for example, `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\sshler.exe`).
2. Open **Task Scheduler → Create Task…**.
3. Under **Triggers**, add "At log on".
4. Under **Actions**, choose "Start a program" and point to the `sshler.exe` path. Add arguments such as `serve --no-browser` and set **Start in** to a writable directory.
5. Tick "Run with highest privileges" if you need WSL access, then save. sshler will now launch automatically every time you sign in.

### Linux / macOS (systemd user service)

Create `~/.config/systemd/user/sshler.service`:

```ini
[Unit]
Description=sshler – local tmux bridge
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/sshler serve --bind 127.0.0.1 --no-browser
Restart=on-failure
KillMode=process

[Install]
WantedBy=default.target
```

> **Important:** `KillMode=process` prevents systemd from killing tmux sessions when restarting the service.

Reload and enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

## Claude Code skills

If you use [Claude Code](https://claude.ai/code) to develop sshler, this repo ships several skill docs that teach Claude the non-obvious parts of the codebase:

- `docs/skills/markdown-preview/SKILL.md` — what renders, what doesn't, and quirks of the print-to-PDF pipeline.
- `docs/skills/progress-bars/SKILL.md` — push protocol, WebSocket fan-out, per-box subscription model.
- `docs/skills/diff-notebook/SKILL.md` — the multi-cell diff workspace: command parser, base64 URL state, server-side persistence, the "no new backend" architecture decision.

Claude won't auto-discover these — they live in the repo but aren't registered with the Claude Code runtime. A common pattern is to **symlink them into your global skills directory** (typically `~/.claude/skills/`) so any Claude Code session on your machine can load them by name:

```bash
# One-time setup per machine
for skill in markdown-preview progress-bars diff-notebook; do
  mkdir -p ~/.claude/skills/sshler-$skill
  ln -sf "$PWD/docs/skills/$skill/SKILL.md" ~/.claude/skills/sshler-$skill/SKILL.md
done
```

After that, Claude sessions see skills named `sshler`, `sshler-progress-bars`, and `sshler-diff-notebook`. Restart Claude Code once (skills load at startup) and they show up alongside the built-in ones. This is the same idea as plugins/marketplaces — just hand-rolled and machine-local. The docs themselves stay versioned in the repo, so the source of truth is always `git`; the symlinks are just registration.

## Dependencies & licenses

- FastAPI, uvicorn, asyncssh, platformdirs, pyyaml, pydantic (PyPI packages, permissive licenses)
- Vue 3 + Pinia (MIT) powers the frontend SPA
- xterm.js (MIT) provides the browser terminal
- CodeMirror (MIT) powers the file editor

All assets are used under their respective MIT/BSD-style licenses. sshler itself ships under the MIT license.

## Why "sshler"?

Because sometimes you want less VS Code, more terminal — but still in a nice browser tab.

---

<a id="japanese"></a>

# 日本語 (Japanese README)

> 🌐 **English README is at the top of this page.** → [英語版へ戻る](#sshler)

sshler はローカル専用の軽量 Web UI で、リモートファイルを SFTP で閲覧したり、ブラウザ上で tmux セッションに接続したりできます。リモート側に追加ソフトをインストールする必要はありません。

## クイックスタート

```bash
# インストール
pip install sshler

# 実行（ブラウザが自動で開きます）
sshler serve

# 開発環境でのセットアップ
uv sync --group dev
cd frontend && pnpm install && pnpm build && cd ..
sshler serve
```

アプリは `http://127.0.0.1:8822` で開き、Vue SPA の `/app/` にリダイレクトします。

## 特徴

### コア機能
- **クロスプラットフォーム**: Windows 11、macOS、Linux で動作（Python 3.12+ が必要）
- **ローカルワークスペース**: ローカルファイルシステムを閲覧し、リモートホストと並べてネイティブの tmux セッションを起動（Windows では WSL tmux、Linux/macOS ではネイティブ tmux を使用）
- **SSH 統合**: 既存の SSH 鍵を使用し、OpenSSH エイリアスに対応
- **ブラウザ内ターミナル**: リモートホストで `tmux new -As <session> -c <dir>` を開き、WebSocket + xterm.js 経由で接続
- **ファイル管理**: プレビュー、編集、削除、「ここでターミナルを開く」機能を備えた Vue ベースのファイルブラウザ
- **自動設定**: 初回起動時にスターター設定を作成
- **エイリアス解決**: DNS 失敗時は `ssh -G` にフォールバック。ワンクリックで上書きをリセット
- **ファイル操作**: CodeMirror エディタでファイルのプレビュー、編集（256 KB 以下）、削除が可能

### モダン UI 機能

**テーマと外観**
- **ダーク/ライトテーマ切替** - システム設定を検出してシームレスにテーマを切り替え
- **PWA サポート** - オフライン機能とアプリアイコンでスタンドアロンアプリとしてインストール可能

**キーボードとナビゲーション**
- **コマンドパレット (Cmd/Ctrl+K)** - あいまい検索で全機能に素早くアクセス
- **キーボードショートカット** - `?` を押すと利用可能なショートカットを表示
- **グローバル検索 (Cmd/Ctrl+Shift+F)** - 全ホストの全ファイルを横断検索

**強化されたファイル管理**
- **ドラッグ&ドロップアップロード** - ファイルブラウザに直接ファイルをドロップ
- **一括操作** - Shift+Click や Cmd/Ctrl+Click で複数ファイルを選択
- **インライン名前変更 (F2)** - モーダルを開かずにファイル名を変更
- **コンテキストメニュー** - 右クリックでクイックアクション
- **最近使ったファイルとブックマーク** - よく使う場所へのクイックアクセス
- **ファイルプレビュー強化** - ファイルビューアで行番号とワードラップを切り替え
- **ディレクトリダウンロード** - ディレクトリを .zip としてダウンロード（大容量時にサイズ警告付き）
- **PDF エクスポート（任意）** - `[pdf]` エクストラをインストールすると、プレビュー、ファイルリスト右クリック、複数選択ツールバーから PDF を 1 クリックでダウンロード可能。バックエンドが Playwright + ヘッドレス Chromium で生成するため、Mermaid 図はベクター、コードブロックは鮮明なまま。詳細は [インストール](#インストール) 参照。

**ターミナル機能**
- **マルチペインレイアウト** - ターミナルを水平、垂直、グリッドに分割
- **セッション永続化** - リロード時にターミナルレイアウトを復元
- **クラッシュリカバリ** - tmux ウィンドウ状態を30秒ごとにスナップショット。WSL/システムがクラッシュした場合、リカバリモーダルで同じウィンドウレイアウトと作業ディレクトリでセッションを再作成可能
- **ライブ検出** - OOM やクラッシュで終了した tmux セッションを再起動なしで検出、リカバリモーダルが自動表示
- **スナップショット鮮度インジケーター** - 接続インジケーターの横にある青いドットがスナップショット状態を表示
- **クリック可能なファイルパス** - ターミナル出力のファイルパスと file:// URL がクリック可能。file:// URL はクリップボードにコピー
- **ターミナル通知** - 長時間実行コマンドのデスクトップ通知
- **接続状態表示** - リアルタイムの接続状態インジケーター
- **コマンドスニペット** - よく使うコマンドをホストごとまたはグローバルに保存してワンクリック入力
- **ポートフォワーディング** - ホストごとの SSH トンネル管理（ローカル/リモート）を視覚的に操作
- **ホスト別ターミナルテーマ** - 環境ごとにターミナルを色分け（本番=赤、ステージング=緑など）
- **ホスト別絵文字アイコン** - ホストごとに固有の絵文字を割り当て、視覚的にすばやく識別
- **アクティブホストコンテキスト** - ビュー切り替え時にナビゲーションリンクが現在のホストを記憶

**モバイル & タッチサポート**
- **タッチ最適化** - 簡単にタップできる 44px 以上のタッチターゲット
- **スワイプジェスチャー** - ファイルブラウザで右スワイプして戻る
- **ロングプレスコンテキストメニュー** - ファイルを長押し（500ms）でクイックアクション
- **プルトゥリフレッシュ** - 下に引っ張って現在のディレクトリをリロード
- **レスポンシブデザイン** - タブレットやスマートフォン向けに最適化されたレイアウト
- **仮想キーボード対応** - モバイルキーボード表示時にターミナルが自動調整
- **画面回転対応** - デバイス回転時にターミナルがスムーズにリサイズ
- **iOS 入力最適化** - 16px フォントサイズでフォーカス時の自動ズームを防止
- **パッシブタッチイベント** - スクロールがスムーズでジャンクなし
- **モバイルフルスクリーン** - フルスクリーン時は最小限の UI で最大のタイピングスペース

**モバイルターミナル入力バー**
- **クイックキー** - モバイルで入力しにくいキー用の Phosphor アイコンボタン
- **矢印ナビゲーション** - メニューナビゲーション用（Claude Code、vim など）
- **Enter/Tab** - 選択確定とオートコンプリート
- **Escape/Stop** - Claude Code のターン中断や操作キャンセル（黄色）
- **Ctrl+C** - プロセス強制終了（赤 - 危険表示）
- **tmux スクロールモード** - コピーモード開始とページアップ/ダウン（オレンジ）
- **Ctrl+D** - 優雅な終了/EOF（ティール）
- **ヘルプ凡例** - `?` をタップして各ボタンの説明を表示
- **色分け** - 機能別にグループ化（青=確定、赤=危険、オレンジ=スクロール）

**超薄型モバイルヘッダー**
- **14px の高さ** - ターミナル領域を最大化（JuiceSSH にインスパイア）
- **ライブ統計** - CPU/MEM パーセンテージと色インジケーター（緑/オレンジ/赤）
- **最小限のクローム** - ロゴと統計のみ、ボタンなし

**グローバル進捗バー**
- **どんなスクリプトからでもプッシュ** - `sshler progress push <名前> <現在> <合計>` をビルド／CI／デプロイスクリプトから実行すれば、ブラウザに即座にバーが現れます。プロジェクト設定不要、起動中の sshler インスタンスを使うだけ。
- **どこからでも監視** - ヘッダー下の細いストリップが現在のホストのバーを全ページで表示します。ファイル／ターミナル／設定どこを見ていてもバーが視界に入ります。
- **ホストごとの購読** - バーはサーバー側に保存されます（グローバルプール）が、購読はホストごとに独立しています。`sshler` ホストで `deploy` を購読し `maintenance` に切り替えるとストリップは空になり、戻すと再び表示されます。ストリップの `+` ボタンでピッカーを開き、現在のホストのバーをトグルできます。`/app/progress` 管理ページでは全バーの一覧・購読トグル（アクティブなホストにスコープ）・削除・古いバーのハイライトができます。
- **消すまで残る** - 購読したバーは `done` を含むすべてのステータスでストリップに残り続けます（完了を示す緑のチェックこそ購読の目的）。ピッカー、購読解除、または管理ページからの削除でクリアできます。完了した瞬間、バーは一度だけ白く点滅します。
- **リッチなツールチップ＋メタデータ** - バーにホバーすると、ラベル・ステータス・カウント・タイムスタンプ、そしてスクリプトが付加した任意のメタデータ（`--meta key=value`）を表示するツールチップが出ます。メタデータは耐障害性があり、スクリプトが不正な値を送ってもバーは進み続け、小さなエラー表示が出るだけで壊れません。
- **正直なパーセンテージ** - 表示パーセントは**切り捨て**です。3300 ステップのビルドで 3299/3300 なら 99% と表示され、実際に完了するまで誤解を招く 100% にはなりません。
- **ライブ更新** - WebSocket (`/ws/progress`) が接続中の全タブに upsert/delete イベントをファンアウト。複数マシン？スマホでも `/app/progress` を開けます。

**🤖 Claude セッションダッシュボード**
- **`/app/claude` — Claude Code セッションの再開** - このマシン上で再開可能な [Claude Code](https://claude.ai/code) の会話（`~/.claude/projects/*/*.jsonl` から読み取り）を一覧表示し、ワンクリックでブラウザ内ターミナルに再開します。タイトルは Claude Code の `/resume` ピッカーと同じ規則です（`/rename` で付けた名前 → AI 生成タイトル → 最初のプロンプトの順）。
- **Git リポジトリ単位でグループ化・折りたたみ可能** - セッションはリポジトリのルート単位でグループ化されます（`repo/subdir` で実行した会話も `/resume` と同様に `repo` の下に表示）。各グループは折りたたみ可能で、アプリの他の場所と同じディレクトリ絵文字が付きます。初期状態は折りたたみで、フィルタ入力で該当グループが自動展開され、「すべて展開／すべて折りたたむ」ボタンもあります。
- **メタデータ付きタイムライン** - 各リポジトリのセッションはタイムライン上に並び、点の色で最終更新の新しさを示します（緑 <1時間、青 <1日、橙 <1週間、それ以降は灰色）。サブディレクトリ・git ブランチ・履歴サイズ・Claude Code バージョンのタグも表示されます。
- **その場で再開／ターミナルへ移動** - **再開**ボタンは会話をバックグラウンドで（リポジトリのトップレベル tmux セッション内に）開き、一覧に留まります。↗ ボタンは開いた上でターミナルに切り替えます。すでに開いているセッションを再度再開してもそのタブにフォーカスするだけで、実行中の Claude を再起動しません。
- **再開コマンドの設定** - 既定は `claude --resume {id}`。グローバルまたはセッション単位で上書きでき（独自のランチャーやフラグを注入可能）、`{id}` は検証済みのセッション ID に置換されます。設定はブラウザに保存されます。
- **tmux セッション名の共有** - ローカルセッションはディレクトリ名（`ts` tmux セッションヘルパーと同じ規則）で命名されるため、sshler が停止していても通常のターミナルから同じセッションにアタッチできます。

**アクセシビリティ**
- **WCAG 2.1 AA 準拠** - セマンティック HTML、ARIA ラベル、キーボードナビゲーション
- **スクリーンリーダー対応** - 適切なフォーカス管理とアナウンス
- **モーション軽減** - システムの `prefers-reduced-motion` 設定を尊重
- **高コントラスト** - 明確な視覚的階層と色のコントラスト

## インストール

### PyPI（推奨）

```bash
pip install sshler

# 設定ファイルと systemd/サービスアセットを作成するため一度起動
sshler serve
```

Python **3.12+** が必要です。

#### オプション：PDF エクスポート

PDF ダウンロードボタンは Playwright + ヘッドレス Chromium が利用可能な場合のみ表示されます。有効にするには：

```bash
pip install "sshler[pdf]"
playwright install chromium
# （初回のみ。Linux では追加で必要な場合あり: playwright install-deps chromium）
```

ベースインストールはこれなしでも問題なく動作します — sshler は通常通り起動し、PDF ボタンが非表示になるだけです。

### 開発用

```bash
uv pip install -e .
# または: pip install -e .
```

リポジトリをクローンした後、dev extras をインストールして通常のツールを実行：

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

E2E スモークテスト（Playwright）：

```bash
uv run playwright install chromium   # 初回のみブラウザをダウンロード
uv run pytest tests/e2e
```

## 実行

```bash
sshler serve
```

デフォルトブラウザで `http://127.0.0.1:8822` が開き、`/app/` にリダイレクトします。

### フロントエンドのビルド

Vue SPA は実行前にビルドが必要です（PyPI リリースではプリビルド済み）：

```bash
cd frontend && pnpm install && pnpm build
# または CLI を使用：
sshler build
```

### 開発モード

ホットリロード開発の場合：

```bash
# ターミナル 1: バックエンド
sshler serve --no-browser

# ターミナル 2: フロントエンド開発サーバー
cd frontend && pnpm dev -- --host --base /app/
# http://localhost:5173/app/ にアクセス
```

または、結合された開発コマンドを使用：

```bash
sshler dev  # 両方のサーバーをホットリロードで実行
```

### 進捗バーをプッシュする

同じホスト上の任意のスクリプトから、ブラウザにライブ表示される進捗バーをプッシュできます：

```bash
sshler progress push build 0 100 --label "frontend build" --color blue
# ... 作業中 ...
sshler progress push build 50 100 --color blue --meta stage=compile
sshler progress push build 100 100 --status done --color green

sshler progress list
sshler progress delete build
```

利用可能なフラグ：`--label TEXT`、`--color NAME|#hex`、`--status running|done|failed|cancelled`、`--url URL`、`--token TOKEN`。

**メタデータ** — バーのホバーツールチップに表示される任意のフィールドを付加できます：`--meta KEY=VALUE`（繰り返し可）、または `--meta-json '{"warnings":3}'`。デフォルトでは各プッシュがメタデータを**置き換え**ます。`--merge` で追記、`--clear-meta` で空にします。メタデータフラグなしのプッシュは既存のメタデータをそのまま残すので、細かい進捗ループで消えることはありません。不正なメタデータがプッシュを止めることはなく、バーは進み続け、ツールチップにエラーが表示されます。

名前は `^[A-Za-z0-9._:-]{1,64}$` にマッチする必要があります（同じ名前にプッシュ＝同じバーの更新）。認識される色名：`blue green red yellow orange purple pink teal`。`#3b82f6` / `rgb(...)` のような CSS 色も使えます。

**トークンの自動検出** — sshler 起動時、有効な CSRF トークンが `<config_dir>/runtime-token`（Linux では `~/.config/sshler/runtime-token`、パーミッション 0600）に書き込まれます。CLI はこれを自動で読み取るため、ローカルからのプッシュは設定不要です。リモート／他マシンからプッシュする場合は `--token` または `$SSHLER_TOKEN` で上書き可能。URL も同様に `http://127.0.0.1:8822` がデフォルトで、`--url` / `$SSHLER_PROGRESS_URL` で上書きできます。

購読は**ホストごと**です。ホスト（ファイル／ターミナル）を開いてから、ヘッダーストリップの `+` ボタン、または `/app/progress` で監視したいバーを購読してください。ホストごとに購読セットが記憶されるので、`sshler` と `maintenance` で異なるバーを表示できます。ヘッダー下の細いストリップが、アクティブなホストの購読済みバーを全ページで表示します。

エラー時に `trap` → `--status failed` するデモループは `examples/progress-bar-build-watcher.sh` を参照。

### キーショートカット

- **Cmd/Ctrl+K** - コマンドパレット
- **Alt+F** - ファイルへ移動
- **Alt+T** - ターミナルへ移動
- **Alt+B** - ホストへ移動
- **?** - 全キーボードショートカットを表示

## 設定

sshler は既存の OpenSSH 設定（`~/.ssh/config`）を読み取り、すべての具体的な `Host` エントリを自動的に表示します。UI を通じて追加したお気に入り、デフォルトディレクトリ、カスタムホストは、付属の YAML ファイルに保存されます。

設定ファイルは初回実行時に作成されます：

- Windows: `%APPDATA%\sshler\boxes.yaml`
- macOS/Linux: `~/.config/sshler/boxes.yaml`

例：

```yaml
boxes:
  - name: my-server
    host: server.example.com      # IP または FQDN
    ssh_alias: my-server          # オプション: `ssh -G my-server` で解決
    user: alice
    port: 22
    keyfile: ~/.ssh/id_ed25519
    favorites:
      - /home/alice
      - /home/alice/projects
      - /var/www
    default_dir: /home/alice
```

> ヒント: ホームパスが `/home/<user>` でない場合は `default_dir` を設定してください。OpenSSH エイリアスを使用する場合は `ssh_alias:` を追加すると、DNS 失敗時に `ssh -G` で解決します。

### 上書き設定のリセット

SSH 設定から取り込まれたホストは枠が強調表示され、「Refresh」ボタンで上書き設定を削除できます。`~/.ssh/config` を更新した際はボタンを押すだけで最新状態になります。

### カスタムホストの追加

UI の "Add Box" から SSH 設定に存在しないホストも追加できます（例: 一時的な Docker コンテナ）。未入力の項目は SSH のデフォルト値が使われます。

### セキュリティモデル（重要）

**Localhost (127.0.0.1):** パスワード不要。sshler はデフォルトで localhost にバインドし、CSRF 保護のためにランダムな `X-SSHLER-TOKEN` を使用します。

**非 localhost:** パスワード必須。`0.0.0.0` またはその他の非 localhost アドレスにバインドする場合、認証の設定が必須です：

```bash
# パスワードを設定（推奨 - .env にハッシュを作成）
sshler hash-password

# または環境変数を直接使用
export SSHLER_USERNAME=admin
export SSHLER_PASSWORD_HASH='$argon2id$...'  # sshler hash-password で生成

# または CLI フラグを使用（非推奨 - プロセスリストに表示される）
sshler serve --host 0.0.0.0 --auth myuser:mypassword
```

**追加のセキュリティに関する注意:**
- **環境変数**: `.env` ファイルをバージョン管理にコミットしないでください。`.env.example` をテンプレートとして使用してください。`.env` ファイルにはパスワードハッシュなどの機密情報が含まれる可能性があります。
- ファイルアップロードは 50 MB まで（`--max-upload-mb` で調整可能）。アップロードされたコンテンツはサーバー側で実行されません。
- SSH 接続はシステムの `known_hosts` を尊重します。リスクを完全に理解している場合のみ `known_hosts: ignore` を設定してください。
- ローカルホスト以外に公開する場合は、`--allow-origin` でオプトインし、`--auth user:pass`（Basic 認証）を追加してください。信頼できるネットワークでのみ使用し、TLS（nginx、Caddy など）を前段に配置してください。
- テレメトリ、アナリティクス、コールホーム機能は一切ありません。

### CLI オプション

```bash
sshler serve \
  --host 127.0.0.1 \
  --port 8822 \
  --max-upload-mb 50 \
  --allow-origin http://workstation:8822 \
  --auth myuser:mypassword \
  --no-ssh-alias \
  --log-level info
```

- `--host`（別名 `--bind`）: バインドアドレスを設定（デフォルト: `127.0.0.1` でローカルホストのみ）。すべてのインターフェースに公開するには `0.0.0.0` を使用しますが、**信頼できるネットワーク上でのみ `--auth` と TLS を併用してください**。
- `--port`: ポート番号を設定（デフォルト: `8822`）。
- `--allow-origin`: CORS を拡張するために繰り返し使用可能。ローカルホスト以外に UI を公開する場合は `--auth` と組み合わせてください。
- `--auth user:pass`: HTTP Basic 認証を有効化（`0.0.0.0` にバインドする場合は推奨）。
- `--max-upload-mb`: アップロードサイズ制限を設定（デフォルト: 50 MB）。
- `--no-ssh-alias`: DNS 失敗時の `ssh -G` フォールバックを無効化。
- `--token`: 独自の `X-SSHLER-TOKEN` を指定（指定しない場合は安全なランダム値が生成されます）。
- `--log-level`: uvicorn に直接渡されます（オプション: `critical`、`error`、`warning`、`info`、`debug`、`trace`）。

サーバーは起動時にトークン（および有効にした場合は Basic 認証のユーザー名）を出力するので、API クライアントやブラウザ拡張機能にコピーできます。

### ターミナル通知

- tmux またはシェルからベル（`printf '\a'`）を送信すると、sshler タブが非表示のときにブラウザタイトルが点滅し、デスクトップ通知が表示されます。
- より豊富なメッセージには OSC 777 を使用: `printf '\033]777;notify=Codex%20done|Check%20the%20output\a'`。`|` の前のテキストがタイトルになり、後半が本文になります。
- JSON ペイロードもサポート: `printf '\033]777;notify={"title":"Codex","message":"All tasks finished"}\a'`。
- 初回の通知はブラウザの許可を求めます。拒否した場合でも、タブに戻ったときにアプリ内トーストとタイトルバッジが表示されます。

## TLS/HTTPS デプロイ

### HTTPS が重要な理由

sshler は認証にセキュアな **httpOnly セッション Cookie** を使用します。ブラウザは HTTPS 経由で配信する際に Cookie に `Secure` フラグが設定されていることを要求します。これにより、Cookie は暗号化された接続でのみ送信されます。

**本番環境では HTTPS を強く推奨します。**

### デプロイオプション

#### 1. ローカル開発（HTTP）

`localhost` または `127.0.0.1` でのローカル開発では、Secure Cookie フラグを無効化できます：

```bash
# .env
SSHLER_HOST=127.0.0.1
SSHLER_PORT=8822
SSHLER_PUBLIC_URL=http://localhost:8822
SSHLER_COOKIE_SECURE=false  # localhost 開発専用！
```

**本番環境やネットワークアクセス可能なインターフェースでは絶対に `COOKIE_SECURE=false` を使用しないでください。**

#### 2. Caddy リバースプロキシによる本番運用（推奨）

[Caddy](https://caddyserver.com/) は sshler に HTTPS を追加する最も簡単な方法です。Let's Encrypt 証明書を自動的に取得・更新します。

**基本セットアップ：**

1. Caddy をインストール：
   ```bash
   # Ubuntu/Debian
   sudo apt install caddy

   # macOS
   brew install caddy
   ```

2. Caddyfile を作成：
   ```caddyfile
   # /etc/caddy/Caddyfile または ~/Caddyfile

   sshler.company.internal {
       reverse_proxy localhost:8822
   }
   ```

3. sshler を HTTPS 用に設定：
   ```bash
   # .env
   SSHLER_HOST=127.0.0.1
   SSHLER_PORT=8822
   SSHLER_PUBLIC_URL=https://sshler.company.internal
   SSHLER_COOKIE_SECURE=true  # HTTPS 必須
   ```

4. Caddy を起動：
   ```bash
   # システムサービス
   sudo systemctl start caddy

   # または直接実行
   caddy run --config /etc/caddy/Caddyfile
   ```

5. `https://sshler.company.internal` で sshler にアクセス

**LAN デプロイ（自己署名証明書）：**

パブリックドメインのないローカルネットワークにデプロイする場合は、Caddy で自己署名証明書を使用：

```caddyfile
sshler.local {
    tls internal  # Caddy の内部 CA を使用
    reverse_proxy localhost:8822
}
```

ブラウザで Caddy のローカル CA 証明書を信頼するように設定してください（通常 `~/.local/share/caddy/pki/authorities/local/root.crt` にあります）。

**高度な Caddy 設定：**

```caddyfile
sshler.company.internal {
    # Let's Encrypt による自動 HTTPS

    # オプション: API エンドポイントのレート制限
    @api {
        path /api/v1/*
    }
    rate_limit @api 100r/m

    # ログインエンドポイントのより厳格なレート制限（推奨）
    @login {
        path /api/v1/auth/login
    }
    rate_limit @login 5r/m

    # sshler へのプロキシ
    reverse_proxy localhost:8822 {
        # クライアント IP を保持
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    # オプション: セキュリティヘッダーを追加
    header {
        Strict-Transport-Security "max-age=31536000;"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
    }
}
```

#### 3. Tailscale デプロイ

[Tailscale](https://tailscale.com/) を使用している場合、Tailscale ネットワーク経由で sshler にアクセスできます。Tailscale は MagicDNS で自動的に HTTPS を提供します。

1. Tailscale IP でリッスンするように sshler を設定：
   ```bash
   # .env
   SSHLER_HOST=100.64.0.1  # あなたの Tailscale IP
   SSHLER_PORT=8822
   SSHLER_PUBLIC_URL=https://yourhost.tail-scale.ts.net
   SSHLER_COOKIE_SECURE=true
   ```

2. Tailscale Serve を有効化（オプション、HTTPS 用）：
   ```bash
   tailscale serve https / http://localhost:8822
   ```

3. `https://yourhost.tail-scale.ts.net` で sshler にアクセス

**注意:** Tailscale はネットワークレベルの暗号化を提供しますが、HTTPS を使用することでセキュア Cookie が正しく動作します。

#### 4. その他のリバースプロキシ

**Nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name sshler.company.internal;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8822;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }
}
```

**Traefik:**

```yaml
http:
  routers:
    sshler:
      rule: "Host(`sshler.company.internal`)"
      service: sshler
      tls:
        certResolver: letsencrypt

  services:
    sshler:
      loadBalancer:
        servers:
          - url: "http://localhost:8822"
```

### マルチインスタンスデプロイ

**重要**: 現在のセッションストアは**インメモリ**であり、**マルチインスタンスデプロイには適していません**（例: ロードバランサー配下で複数の sshler プロセスを実行する場合）。

**影響：**
- セッションはプロセスメモリに保存されます
- 各インスタンスは独立したセッションストアを持ちます
- リクエストが異なるインスタンスにルーティングされるとセッションが失われます
- ロードバランシング時にセッション Cookie が無効として扱われます

**シングルインスタンスデプロイ**（最も一般的）：
- リバースプロキシ（Caddy、Nginx）の背後で sshler プロセスを 1 つ実行
- systemd サービスで 1 インスタンスを実行
- Docker コンテナ（シングルインスタンス）

マルチインスタンスサポートが必要な場合は、Issue を作成するか、共有セッションバックエンドを実装する PR を送信してください。

### セキュリティチェックリスト

sshler を本番環境にデプロイする際：

- **HTTPS を使用** - 有効な証明書で（Let's Encrypt 推奨）
- **`SSHLER_COOKIE_SECURE=true` を設定** - `.env` ファイルで
- **`SSHLER_PUBLIC_URL` を設定** - 実際の HTTPS URL に
- **強力なパスワードを使用** - `sshler hash-password` で生成
- **`SSHLER_REQUIRE_AUTH=true` を維持** - 本番環境では認証を無効化しない
- **localhost にバインド** - リバースプロキシの背後では `SSHLER_HOST=127.0.0.1`
- **ファイアウォールルールを有効化** - 信頼できるネットワークにアクセスを制限
- **sshler を最新に保つ** - セキュリティパッチを受け取るため

### ネットワークセキュリティレイヤー

sshler のセキュリティは多層構造です：

1. **トランスポートセキュリティ（HTTPS）** - すべてのトラフィックを暗号化し、セッション Cookie を保護
2. **アプリケーション認証（セッション Cookie）** - httpOnly Cookie でユーザー ID を検証
3. **CSRF 保護** - 状態変更リクエストに対する Origin ヘッダー検証
4. **ネットワーク分離**（オプション） - Tailscale、VPN、またはファイアウォールルール

**推奨:** ほとんどのデプロイでは HTTPS + セッション認証を使用してください。インターネット経由でアクセスする場合は、ネットワーク分離（Tailscale/VPN）を追加してセキュリティを強化してください。

### Cookie セッションを選んだ理由（JWT ではなく）

**要約**: JWT は分散ステートレス認証の問題を解決します。sshler にはその問題がありません。Cookie セッションの方がシンプルで安全、かつ失効が可能です。

**判断の根拠：**

1. **即時失効**
   - セッションはサーバー側で即座に無効化可能（ログアウト、セキュリティ侵害、管理者操作）
   - JWT は複雑な拒否リストなしでは失効不可能（それでは「ステートレス」の意味がない）
   - 緊急のアクセス制御が必要な管理ツールでは重要

2. **シンプルなセキュリティモデル**
   - 鍵ローテーションの複雑さなし
   - JWT クレーム検証のエッジケースなし
   - 「JWT をどこに保存するか」の議論なし（localStorage = XSS 脆弱、Cookie = それならセッションを使うべき）

3. **適切なユースケース**
   - **JWT が向いている場面**: サービス間認証、分散マイクロサービス、Cookie をサポートしないモバイルアプリ
   - **セッションが向いている場面**: 単一バックエンドと通信するブラウザベースアプリ（sshler のアーキテクチャ）

4. **セキュリティ上の利点**
   - httpOnly Cookie は XSS によるトークン窃取を防止（JavaScript からアクセス不可）
   - SameSite=Lax は CSRF 攻撃を防止
   - 短い攻撃ウィンドウ（デフォルト 8 時間 TTL vs 一般的な JWT リフレッシュトークンパターン）

**結論**: ブラウザ認証のための退屈だけど正しいソリューションを選びました。JWT が必要な場合は、まず異なるアーキテクチャが必要です（分散サービス、モバイルクライアントなど）。ブラウザベースの SSH マネージャーには、Cookie セッションが適切なツールです。

## 自動起動

### Windows（タスク スケジューラ）

1. `where sshler` を実行してインストールされた実行可能ファイルを見つけます（例: `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\sshler.exe`）。
2. **タスク スケジューラ → タスクの作成…** を開きます。
3. **トリガー** で「ログオン時」を追加。
4. **操作** で「プログラムの開始」を選択し、`sshler.exe` のパスを指定。引数に `serve --no-browser` を追加し、**開始** を書き込み可能なディレクトリに設定。
5. WSL アクセスが必要な場合は「最上位の特権で実行する」にチェックを入れて保存。サインインするたびに sshler が自動起動します。

### Linux / macOS（systemd ユーザーサービス）

`~/.config/systemd/user/sshler.service` を作成:

```ini
[Unit]
Description=sshler – local tmux bridge
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/sshler serve --bind 127.0.0.1 --no-browser
Restart=on-failure
KillMode=process

[Install]
WantedBy=default.target
```

> **重要:** `KillMode=process` がないと、sshler を再起動したときにすべての tmux セッションが終了します。

リロードして有効化:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

## Claude Code スキル

[Claude Code](https://claude.ai/code) で sshler を開発する場合、このリポジトリにはコードベースの分かりにくい部分を Claude に教えるスキルドキュメントがいくつか同梱されています：

- `docs/skills/markdown-preview/SKILL.md` — 何がレンダリングされ、何がされないか、そして印刷／PDF パイプラインの癖。
- `docs/skills/progress-bars/SKILL.md` — プッシュプロトコル、WebSocket ファンアウト、ホストごとの購読モデル。
- `docs/skills/diff-notebook/SKILL.md` — マルチセルの差分ワークスペース：コマンドパーサー、base64 URL 状態、サーバー側の永続化、「新しいバックエンドを作らない」という設計判断。

Claude はこれらを自動では検出しません。リポジトリ内には存在しますが、Claude Code ランタイムには登録されていないためです。よく使われる方法は、**グローバルスキルディレクトリ（通常は `~/.claude/skills/`）へシンボリックリンクを張る**ことです。こうすると、このマシン上のどの Claude Code セッションからでも名前で読み込めるようになります：

```bash
# マシンごとに一度だけセットアップ
for skill in markdown-preview progress-bars diff-notebook; do
  mkdir -p ~/.claude/skills/sshler-$skill
  ln -sf "$PWD/docs/skills/$skill/SKILL.md" ~/.claude/skills/sshler-$skill/SKILL.md
done
```

これで Claude セッションから `sshler`、`sshler-progress-bars`、`sshler-diff-notebook` という名前のスキルが見えるようになります。Claude Code を一度再起動すると（スキルは起動時に読み込まれます）、組み込みのものと並んで表示されます。これはプラグインやマーケットプレイスと同じ考え方を、手作業かつマシンローカルで実現したものです。ドキュメント自体はリポジトリでバージョン管理され続けるので、真実の情報源は常に `git` にあり、シンボリックリンクは登録のためだけのものです。

## 依存関係とライセンス

- FastAPI、uvicorn、asyncssh、platformdirs、pyyaml、pydantic（PyPI パッケージ、寛容なライセンス）
- Vue 3 + Pinia（MIT）がフロントエンド SPA を駆動
- xterm.js（MIT）がブラウザターミナルを提供
- CodeMirror（MIT）がファイルエディタを駆動

すべてのアセットはそれぞれの MIT/BSD スタイルのライセンスの下で使用されています。sshler 自体は MIT ライセンスで配布されます。

## 名前の由来

VS Code だけに頼らず、ブラウザタブの中で軽快にターミナルを扱いたい──そんな願いからこの名前になりました。
