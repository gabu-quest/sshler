# sshler

[English](README.md)

[![PyPI version](https://img.shields.io/pypi/v/sshler.svg)](https://pypi.org/project/sshler/)
[![Python versions](https://img.shields.io/pypi/pyversions/sshler.svg)](https://pypi.org/project/sshler/)
[![CI](https://github.com/gabu-quest/sshler/actions/workflows/ci.yml/badge.svg)](https://github.com/gabu-quest/sshler/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

sshler はローカル専用の軽量 Web UI で、リモートファイルを SFTP で閲覧したり、ブラウザ上で tmux セッションに接続したりできます。リモート側に追加ソフトをインストールする必要はありません。

## クイックスタート

```bash
# インストール
pip install sshler

# 実行（ブラウザが自動で開きます）
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
- **グローバル検索 (Cmd/Ctrl+Shift+F)** - 全ボックスの全ファイルを横断検索

**強化されたファイル管理**
- **ドラッグ&ドロップアップロード** - ファイルブラウザに直接ファイルをドロップ
- **一括操作** - Shift+Click や Cmd/Ctrl+Click で複数ファイルを選択
- **インライン名前変更 (F2)** - モーダルを開かずにファイル名を変更
- **コンテキストメニュー** - 右クリックでクイックアクション
- **最近使ったファイルとブックマーク** - よく使う場所へのクイックアクセス
- **ファイルプレビュー強化** - ファイルビューアで行番号とワードラップを切り替え

**ターミナル機能**
- **マルチペインレイアウト** - ターミナルを水平、垂直、グリッドに分割
- **セッション永続化** - リロード時にターミナルレイアウトを復元
- **ターミナル通知** - 長時間実行コマンドのデスクトップ通知
- **接続状態表示** - リアルタイムの接続状態インジケーター
- **コマンドスニペット** - よく使うコマンドをボックスごとまたはグローバルに保存してワンクリック入力
- **ポートフォワーディング** - ボックスごとの SSH トンネル管理（ローカル/リモート）を視覚的に操作
- **ボックス別ターミナルテーマ** - 環境ごとにターミナルを色分け（本番=赤、ステージング=緑など）

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

### キーショートカット

- **Cmd/Ctrl+K** - コマンドパレット
- **Alt+F** - ファイルへ移動
- **Alt+T** - ターミナルへ移動
- **Alt+B** - ボックスへ移動
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

SSH 設定から取り込まれたボックスは枠が強調表示され、「Refresh」ボタンで上書き設定を削除できます。`~/.ssh/config` を更新した際はボタンを押すだけで最新状態になります。

### カスタムボックスの追加

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

リロードして有効化:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sshler.service
```

> **重要:** `KillMode=process` がないと、sshler を再起動したときにすべての tmux セッションが終了します。

## 依存関係とライセンス

- FastAPI、uvicorn、asyncssh、platformdirs、yaml（PyPI パッケージ、寛容なライセンス）
- HTMX（MIT）と xterm.js（MIT）は unpkg から読み込まれます
- CodeMirror（MIT）がエディタを駆動

すべてのアセットはそれぞれの MIT/BSD スタイルのライセンスの下で使用されています。sshler 自体は MIT ライセンスで配布されます。

## 名前の由来

VS Code だけに頼らず、ブラウザタブの中で軽快にターミナルを扱いたい──そんな願いからこの名前になりました。
