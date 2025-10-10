
# sshler / エスエスエイチラー

**English:** sshler is a lightweight, local-only web UI that lets you browse remote
files over SFTP and jump into tmux sessions in your browser — without installing
anything on the remote host.

**日本語:** sshler はローカル専用の軽量 Web UI で、リモートファイルを SFTP で閲覧したり、
ブラウザ上で tmux セッションに接続したりできます。リモート側に追加ソフトを
インストールする必要はありません。

- Runs on your Windows 11 laptop (or any OS with Python)
  - **日本語:** Windows 11 を含む Python が動く環境ならどこでも動作します。
- Includes a "local" workspace card so you can browse your own filesystem and launch WSL-backed tmux sessions alongside remote hosts
  - **日本語:** ローカルワークスペースカードを備え、ローカル/WSL のファイルや tmux も同じ UI で扱えます。
- Uses your existing SSH keys
  - **日本語:** 既存の SSH 鍵をそのまま利用します。
- Opens `tmux new -As <session> -c <dir>` on the remote host and bridges it to the browser via WebSocket + xterm.js
  - **日本語:** リモートで `tmux new -As <session> -c <dir>` を実行し、WebSocket と xterm.js でブラウザに橋渡しします。
- HTMX-based file browser with “Open Terminal Here”
  - **日本語:** HTMX 製のファイルブラウザから「このディレクトリで端末を開く」がワンクリックで可能です。
- Auto-creates a starter config at first run
  - **日本語:** 初回起動時に自動で設定ファイルを生成します。
- Honors your OpenSSH aliases; if DNS fails it resolves them via `ssh -G` and you can reset overrides with a single click
  - **日本語:** OpenSSH のエイリアスを解釈し、DNS 解決に失敗した場合でも `ssh -G` で補完します。ワンクリックで上書きをリセットできます。
- One-click file previews: view remote files in a new tab without leaving the browser
  - **日本語:** ワンクリックでプレビュー表示。ブラウザを離れずに内容確認できます。
- Inline edits for lightweight text files (≤256 KB) with a CodeMirror editor and Save button
  - **日本語:** 256 KB 以下のテキストならブラウザ内で編集し、その場で保存できます。

## Install / インストール

### PyPI (recommended) / PyPI からのインストール

```bash
pip install sshler

# launch once to create the config + systemd/service assets
sshler serve
```

- **日本語:** `pip install sshler` で最新の安定版を取得し、`sshler serve` を実行すると初期設定ファイルが生成されます。

Requires Python **3.12+**.

### Development / 開発インストール

```bash
uv pip install -e .
# or: pip install -e .
```

- **日本語:** 開発時は editable install (`-e`) を利用してください。

After cloning the repository, install the dev extras and run the usual tooling:

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
```

- **日本語:** リポジトリを取得したら `uv sync --group dev` で依存関係をそろえ、`uv run ruff check .` や `uv run pytest` で動作を確認します。

## Run / 実行

```bash
sshler serve
```

The app will open `http://127.0.0.1:8822` in your default browser.

- **日本語:** 上記を実行するとデフォルトブラウザで `http://127.0.0.1:8822` が開きます。

## Configuration / 設定

sshler reads your existing OpenSSH config (`~/.ssh/config`) and shows every concrete `Host` entry automatically. Any favourites, default directories, or custom hosts you add through the UI are stored in a companion YAML file.

- **日本語:** OpenSSH の設定 (`~/.ssh/config`) を読み取り、すべての `Host` が自動的に一覧に表示されます。お気に入りやデフォルトディレクトリ、カスタムホストは付属の YAML に保存されます。

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

- **日本語のヒント:** ホームディレクトリが `/home/<user>` 以外なら `default_dir` を設定してください。OpenSSH のエイリアスを利用する場合は `ssh_alias:` を追加すると、DNS 失敗時に `ssh -G` で解決します。

### Resetting overrides / 上書き設定のリセット

Boxes imported from SSH config show a highlighted border and “Refresh” button. If you change something in `~/.ssh/config`, hit Refresh to drop any stored overrides (host/user/port/key) so the new settings take effect without editing `boxes.yaml`.

- **日本語:** SSH 設定から取り込まれたボックスは枠が強調表示され、「Refresh」ボタンで上書き設定を削除できます。`~/.ssh/config` を更新した際はボタンを押すだけで最新状態になります。

### Adding custom boxes / カスタムボックスの追加

Hit “Add Box” in the UI to define a host that isn’t in your SSH config (for example, a throwaway Docker container). Fields you leave blank fall back to your SSH defaults.

- **日本語:** UI の “Add Box” から SSH 設定に存在しないホストも追加できます（例: 一時的な Docker コンテナ）。未入力の項目は SSH のデフォルト値が使われます。

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

- **日本語:** サーバー起動時にトークン（および Basic 認証を有効にした場合はユーザー名）を表示するので、API クライアントやブラウザ拡張に貼り付けて利用できます。

## Autostart / 自動起動

### Windows (Task Scheduler)

1. Run `where sshler` to locate the installed executable (for example, `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\sshler.exe`).
2. Open **Task Scheduler → Create Task…**.
3. Under **Triggers**, add “At log on”.
4. Under **Actions**, choose “Start a program” and point to the `sshler.exe` path. Add arguments such as `serve --no-browser` and set **Start in** to a writable directory.
5. Tick “Run with highest privileges” if you need WSL access, then save. sshler will now launch automatically every time you sign in.

- **日本語:** Task Scheduler で「ログオン時」をトリガーにし、`sshler.exe serve --no-browser` を実行するタスクを作成すると、サインイン時に自動起動します。

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

[Install]
WantedBy=default.target
```

Reload and enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

- **日本語:** 上記の systemd ユーザーサービスを作成し、`systemctl --user enable --now sshler.service` を実行するとサインイン時に自動起動します。

### Dependencies & licenses

- FastAPI, uvicorn, asyncssh, platformdirs, yaml (PyPI packages, permissive licenses)
- HTMX (MIT) and xterm.js (MIT) are loaded from unpkg
- CodeMirror (MIT) powers the editor

All assets are used under their respective MIT/BSD-style licenses. sshler itself ships under the MIT license.

- **日本語:** 依存ライブラリはいずれも寛容なライセンス (MIT/BSD) で提供されています。sshler 本体も MIT ライセンスで配布されます。

## Why “sshler”? / 名前の由来

**English:** Because sometimes you want less VS Code, more terminal — but still in a nice browser tab.

**日本語:** VS Code だけに頼らず、ブラウザタブの中で軽快にターミナルを扱いたい──そんな願いからこの名前になりました。
