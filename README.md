
# sshler

**sshler** is a lightweight, local-only web UI which lets you browse remote files over SFTP and jump into tmux sessions in your browser — without installing anything on your server.

- Runs on your Windows 11 laptop (or any OS with Python)
- Includes a "local" workspace card so you can browse your own filesystem and launch WSL-backed tmux sessions alongside remote hosts
- Uses your existing SSH keys
- Opens `tmux new -As <session> -c <dir>` on the remote host and bridges it to the browser via WebSocket + xterm.js
- HTMX-based file browser with “Open Terminal Here”
- Auto-creates a starter config at first run
- Honors your OpenSSH aliases; if DNS fails it resolves them via `ssh -G` and you can reset overrides with a single click
- One-click file previews: view remote files in a new tab without leaving the browser
- Inline edits for lightweight text files (≤256 KB) with a CodeMirror editor and Save button

## Install (editable)

```bash
uv pip install -e .
# or: pip install -e .
```

## Run

```bash
sshler serve
```

The app will open `http://127.0.0.1:8822` in your default browser.

## Configuration

sshler reads your existing OpenSSH config (`~/.ssh/config`) and shows every concrete `Host` entry automatically. Any favourites, default directories, or custom hosts you add through the UI are stored in a companion YAML file.

A config file is created on first run:

- Windows: `%APPDATA%\sshler\boxes.yaml`
- macOS/Linux: `~/.config/sshler/boxes.yaml`

Example:

```yaml
boxes:
  - name: gabu-server
    host: example.tailnet.ts.net  # literal IP/FQDN or keep as placeholder
    ssh_alias: gabu-server        # optional: resolves via `ssh -G gabu-server`
    user: gabu
    port: 22
    keyfile: "C:/Users/gabu/.ssh/id_ed25519"
    favorites:
      - /home/gabu
      - /home/gabu/projects
      - /srv/codex
    default_dir: /home/gabu
```

> Tip: Set `default_dir` if your home path isn’t `/home/<user>`.
> If you rely on an OpenSSH alias, add `ssh_alias:` and sshler will run `ssh -G` to expand it when DNS fails.

### Resetting overrides

Boxes imported from SSH config show a highlighted border and “Refresh” button. If you change something in `~/.ssh/config`, hit Refresh to drop any stored overrides (host/user/port/key) so the new settings take effect without editing `boxes.yaml`.

### Adding custom boxes

Hit “Add Box” in the UI to define a host that isn’t in your SSH config (for example, a throwaway Docker container). Fields you leave blank fall back to your SSH defaults.

### Security model (important)

- sshler is designed for **single-user localhost** use. By default `sshler serve` binds to `127.0.0.1` and prints a random `X-SSHLER-TOKEN` that every state-changing request must send.
- File uploads are capped at 50 MB (tunable via `--max-upload-mb`). Uploaded content is never executed server-side.
- SSH connections still honour your system `known_hosts`. Only set `known_hosts: ignore` if you fully understand the risk.
- If you expose sshler beyond localhost, opt-in via `--allow-origin` and add `--auth user:pass` (basic auth). Use it only on networks you trust and put TLS in front (nginx, Caddy, etc.).
- There is no telemetry, analytics, or call-home behaviour.

### CLI options

```
sshler serve \
  --bind 127.0.0.1 \
  --port 8822 \
  --max-upload-mb 50 \
  --allow-origin http://workstation:8822 \
  --auth coder:supersecret \
  --no-ssh-alias \
  --log-level info
```

- `--bind` (alias `--host`) keeps the server on localhost by default.
- `--allow-origin` can be repeated to expand CORS; combine it with `--auth` if you expose the UI to the LAN.
- `--max-upload-mb` lets you raise/lower the upload ceiling.
- `--no-ssh-alias` disables the `ssh -G` fallback when DNS fails.
- `--token` lets you supply your own `X-SSHLER-TOKEN` (otherwise a secure random value is generated).
- `--log-level` feeds directly into uvicorn.

The server prints the token (and, if enabled, the basic auth username) on startup so you can copy it into API clients or browser extensions.

### Dependencies & licenses

- FastAPI, uvicorn, asyncssh, platformdirs, yaml (PyPI packages, permissive licenses)
- HTMX (MIT) and xterm.js (MIT) are loaded from unpkg
- CodeMirror (MIT) powers the editor

All assets are used under their respective MIT/BSD-style licenses. sshler itself ships under the MIT license.

## Development

```bash
# install dependencies (project + dev extras)
uv sync --extra dev

# lint & format
uv run ruff check .
uv run ruff format .

# run the test suite
uv run pytest
```

## Why “sshler”?

Because sometimes you want less VS Code, more terminal — but still in a nice browser tab.
