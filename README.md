# sshler

sshler is a lightweight, local-only web UI that lets you browse remote files over SFTP and jump into tmux sessions in your browser — without installing anything on the remote host.

## Features

- **Cross-platform**: Runs on Windows 11, macOS, and Linux (anywhere with Python 3.12+)
- **Local workspace**: Browse your own filesystem and launch native tmux sessions alongside remote hosts (uses WSL tmux on Windows, native tmux on Linux/macOS)
- **SSH integration**: Uses your existing SSH keys and honors OpenSSH aliases
- **Terminal in browser**: Opens `tmux new -As <session> -c <dir>` on the remote host and bridges it via WebSocket + xterm.js
- **File management**: HTMX-based file browser with preview, edit, delete, and "Open Terminal Here"
- **Auto-configuration**: Creates starter config on first run
- **Alias resolution**: Falls back to `ssh -G` when DNS fails; reset overrides with one click
- **File operations**: Preview, edit (≤256 KB), and delete files with CodeMirror editor
- **Bilingual UI**: Full English and Japanese language support

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

> Tip: Set `default_dir` if your home path isn't `/home/<user>`.
> If you rely on an OpenSSH alias, add `ssh_alias:` and sshler will run `ssh -G` to expand it when DNS fails.

### Resetting overrides

Boxes imported from SSH config show a highlighted border and "Refresh" button. If you change something in `~/.ssh/config`, hit Refresh to drop any stored overrides (host/user/port/key) so the new settings take effect without editing `boxes.yaml`.

### Adding custom boxes

Hit "Add Box" in the UI to define a host that isn't in your SSH config (for example, a throwaway Docker container). Fields you leave blank fall back to your SSH defaults.

### Security model (important)

- sshler is designed for **single-user localhost** use. By default `sshler serve` binds to `127.0.0.1` and prints a random `X-SSHLER-TOKEN` that every state-changing request must send.
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
  --auth coder:supersecret \
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

[Install]
WantedBy=default.target
```

Reload and enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

## Dependencies & licenses

- FastAPI, uvicorn, asyncssh, platformdirs, yaml (PyPI packages, permissive licenses)
- HTMX (MIT) and xterm.js (MIT) are loaded from unpkg
- CodeMirror (MIT) powers the editor

All assets are used under their respective MIT/BSD-style licenses. sshler itself ships under the MIT license.

## Why "sshler"?

Because sometimes you want less VS Code, more terminal — but still in a nice browser tab.

---

# 日本語ドキュメント

sshler はローカル専用の軽量 Web UI で、リモートファイルを SFTP で閲覧したり、ブラウザ上で tmux セッションに接続したりできます。リモート側に追加ソフトをインストールする必要はありません。

## 特徴

- **クロスプラットフォーム**: Windows 11、macOS、Linux で動作（Python 3.12+ が必要）
- **ローカルワークスペース**: ローカルファイルシステムを閲覧し、リモートホストと並べてネイティブの tmux セッションを起動（Windows では WSL tmux、Linux/macOS ではネイティブ tmux を使用）
- **SSH 統合**: 既存の SSH 鍵を使用し、OpenSSH エイリアスに対応
- **ブラウザ内ターミナル**: リモートホストで `tmux new -As <session> -c <dir>` を開き、WebSocket + xterm.js 経由で接続
- **ファイル管理**: プレビュー、編集、削除、「ここでターミナルを開く」機能を備えた HTMX ベースのファイルブラウザ
- **自動設定**: 初回起動時にスターター設定を作成
- **エイリアス解決**: DNS 失敗時は `ssh -G` にフォールバック。ワンクリックで上書きをリセット
- **ファイル操作**: CodeMirror エディタでファイルのプレビュー、編集（256 KB 以下）、削除が可能
- **バイリンガル UI**: 英語と日本語の完全サポート

## インストール

### PyPI（推奨）

```bash
pip install sshler

# 設定ファイルと systemd/サービスアセットを作成するため一度起動
sshler serve
```

Python **3.12+** が必要です。

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

## 実行

```bash
sshler serve
```

デフォルトブラウザで `http://127.0.0.1:8822` が開きます。

## 設定

sshler は既存の OpenSSH 設定（`~/.ssh/config`）を読み取り、すべての具体的な `Host` エントリを自動的に表示します。UI を通じて追加したお気に入り、デフォルトディレクトリ、カスタムホストは、付属の YAML ファイルに保存されます。

設定ファイルは初回実行時に作成されます：

- Windows: `%APPDATA%\sshler\boxes.yaml`
- macOS/Linux: `~/.config/sshler/boxes.yaml`

例：

```yaml
boxes:
  - name: gabu-server
    host: example.tailnet.ts.net
    ssh_alias: gabu-server
    user: gabu
    port: 22
    keyfile: "C:/Users/gabu/.ssh/id_ed25519"
    favorites:
      - /home/gabu
      - /home/gabu/projects
      - /srv/codex
    default_dir: /home/gabu
```

> ヒント: ホームパスが `/home/<user>` でない場合は `default_dir` を設定してください。OpenSSH エイリアスを使用する場合は `ssh_alias:` を追加すると、DNS 失敗時に `ssh -G` で解決します。

### 上書き設定のリセット

SSH 設定から取り込まれたボックスは枠が強調表示され、「Refresh」ボタンで上書き設定を削除できます。`~/.ssh/config` を更新した際はボタンを押すだけで最新状態になります。

### カスタムボックスの追加

UI の "Add Box" から SSH 設定に存在しないホストも追加できます（例: 一時的な Docker コンテナ）。未入力の項目は SSH のデフォルト値が使われます。

### セキュリティモデル（重要）

- sshler は **シングルユーザー・ローカルホスト専用** に設計されています。デフォルトでは `sshler serve` は `127.0.0.1` にバインドし、すべての状態変更リクエストに必要なランダムな `X-SSHLER-TOKEN` を出力します。
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
  --auth coder:supersecret \
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

[Install]
WantedBy=default.target
```

リロードして有効化:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

## 依存関係とライセンス

- FastAPI、uvicorn、asyncssh、platformdirs、yaml（PyPI パッケージ、寛容なライセンス）
- HTMX（MIT）と xterm.js（MIT）は unpkg から読み込まれます
- CodeMirror（MIT）がエディタを駆動

すべてのアセットはそれぞれの MIT/BSD スタイルのライセンスの下で使用されています。sshler 自体は MIT ライセンスで配布されます。

## 名前の由来

VS Code だけに頼らず、ブラウザタブの中で軽快にターミナルを扱いたい──そんな願いからこの名前になりました。
