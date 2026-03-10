# sshler

[日本語](README.ja.md)

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

**🖥️ Terminal Features**
- **Multi-pane Layouts** - Split terminal horizontally, vertically, or in a grid
- **Session Persistence** - Restore your terminal layout on reload
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

### Key Shortcuts

- **Cmd/Ctrl+K** - Command palette
- **Alt+F** - Go to Files
- **Alt+T** - Go to Terminal
- **Alt+B** - Go to Boxes
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

## Dependencies & licenses

- FastAPI, uvicorn, asyncssh, platformdirs, pyyaml, pydantic (PyPI packages, permissive licenses)
- Vue 3 + Pinia (MIT) powers the frontend SPA
- xterm.js (MIT) provides the browser terminal
- CodeMirror (MIT) powers the file editor

All assets are used under their respective MIT/BSD-style licenses. sshler itself ships under the MIT license.

## Why "sshler"?

Because sometimes you want less VS Code, more terminal — but still in a nice browser tab.
