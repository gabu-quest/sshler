
# sshler

**sshler** is a lightweight, local-only web UI which lets you browse remote files over SFTP and jump into tmux sessions in your browser — without installing anything on your server.

- Runs on your Windows 11 laptop (or any OS with Python)
- Uses your existing SSH keys
- Opens `tmux new -As <session> -c <dir>` on the remote host and bridges it to the browser via WebSocket + xterm.js
- HTMX-based file browser with “Open Terminal Here”
- Auto-creates a starter config at first run
- Honors your OpenSSH aliases; if DNS fails it resolves them via `ssh -G` and you can reset overrides with a single click

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

### Security note

By default we use your system `known_hosts`. If you **really** want to disable host key checking, set `known_hosts: ignore` for a host.

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
