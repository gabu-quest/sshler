from __future__ import annotations

import asyncio
import logging
import os
import shlex
import socket
from asyncio.subprocess import Process
from pathlib import Path

import asyncssh


class SSHError(Exception):
    """Raised when an SSH connection or command fails.

    English:
        Wrapper exception used throughout sshler so callers can handle errors
        without depending directly on ``asyncssh`` internals.

    日本語:
        ``asyncssh`` の詳細に依存せずに呼び出し側が例外処理できるようにするための
        ラッパー例外です。
    """


async def connect(
    host: str,
    user: str,
    port: int = 22,
    keyfile: str | None = None,
    known_hosts: str | None = None,
    ssh_config_path: str | None = None,
    ssh_alias: str | None = None,
    allow_alias: bool = True,
) -> asyncssh.SSHClientConnection:
    """Establish an SSH connection using asyncssh.

    English:
        Opens a connection to ``host`` with sensible defaults and optional
        alias expansion. Errors are normalised to :class:`SSHError`.

    日本語:
        ``host`` への SSH 接続を確立します。必要に応じてエイリアス解決を行い、
        失敗した場合は :class:`SSHError` として補足します。

    Args:
        host: Target host to reach.
        user: Username used for the SSH session.
        port: SSH port exposed by the host.
        keyfile: Optional explicit private-key path.
        known_hosts: Known-hosts override or ``"ignore"`` to disable checks.
        ssh_config_path: Optional SSH config location.
        ssh_alias: Alias name to expand via ``ssh -G`` when DNS fails.
        allow_alias: Whether alias expansion is permitted.

    Returns:
        asyncssh.SSHClientConnection: Live SSH connection instance.

    Raises:
        SSHError: Propagates connection issues through a project-specific type.
    """

    if known_hosts and isinstance(known_hosts, str) and known_hosts.lower() == "ignore":
        known_hosts_path = None
    else:
        known_hosts_path = known_hosts

    connect_host = host
    connect_user = user
    connect_port = port
    connect_keyfile = keyfile

    if allow_alias and ssh_alias and not _is_resolvable(connect_host):
        alias_data = await _expand_alias(ssh_alias)
        resolved_host = alias_data.get("hostname")
        if resolved_host:
            connect_host = resolved_host
            if alias_data.get("user") and not connect_user:
                connect_user = alias_data["user"]
            try:
                connect_port = int(alias_data.get("port") or connect_port)
            except (TypeError, ValueError):
                pass
            if not connect_keyfile and alias_data.get("identityfile"):
                connect_keyfile = alias_data["identityfile"]
            LOGGER.info(
                "Resolved SSH alias %s -> host=%s port=%s user=%s",
                ssh_alias,
                connect_host,
                connect_port,
                connect_user,
            )
        else:
            LOGGER.warning("Failed to resolve SSH alias %s; falling back to %s", ssh_alias, host)

    try:
        connection = await asyncssh.connect(
            host=connect_host,
            port=connect_port,
            username=connect_user,
            client_keys=[connect_keyfile] if connect_keyfile else None,
            known_hosts=known_hosts_path,
            config=[ssh_config_path] if ssh_config_path else None,
        )
    except (OSError, asyncssh.Error) as exc:
        raise SSHError(str(exc)) from exc
    return connection


async def open_tmux(
    connection: asyncssh.SSHClientConnection,
    working_directory: str,
    session: str,
    terminal_type: str = "xterm-256color",
    columns: int = 120,
    rows: int = 32,
    environment: dict[str, str] | None = None,
) -> asyncssh.SSHClientProcess:
    """Launch or attach to a tmux session on the remote host.

    English:
        Spawns ``tmux new -As`` ensuring the session name is safe and returning
        the running process object.

    日本語:
        ``tmux new -As`` コマンドを発行し、セッション名を安全な形式に整えてプロセス
        オブジェクトを返します。

    Args:
        connection: Active SSH connection.
        working_directory: Working directory for the tmux session.
        session: Desired session name.
        terminal_type: Terminal type to request from tmux.
        columns: Width to request for the pseudo-terminal.
        rows: Height to request for the pseudo-terminal.
        environment: Environment variables forwarded to the remote session.

    Returns:
        asyncssh.SSHClientProcess: Process representing the tmux attachment.
    """

    # sanitize session name minimally
    safe_session = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in session) or "sshler"
    command = f"tmux new -As {shlex.quote(safe_session)} -c {shlex.quote(working_directory)}"
    process = await connection.create_process(
        command=command,
        term_type=terminal_type,
        term_size=(columns, rows),
        encoding=None,  # bytes
        env=environment,
    )
    return process


async def sftp_list_directory(
    connection: asyncssh.SSHClientConnection, path: str
) -> list[dict[str, object]]:
    """Return directory entries for ``path`` via SFTP.

    English:
        Lists children of ``path`` and records whether each entry is a
        directory before sorting directories first.

    日本語:
        指定された ``path`` の子要素を列挙し、ディレクトリかどうかの情報とともに
        取得してディレクトリを先頭に並べ替えます。

    Args:
        connection: Active SSH connection used to start SFTP.
        path: Remote directory to enumerate.

    Returns:
        list[dict[str, object]]: Metadata entries sorted with directories first.
    """

    sftp_client = await connection.start_sftp_client()
    entries: list[dict[str, object]] = []
    try:
        for filename in await sftp_client.listdir(path):
            try:
                stats = await sftp_client.stat(f"{path.rstrip('/')}/{filename}")
                entries.append(
                    {
                        "name": filename,
                        "is_directory": (stats.permissions & 0o40000)
                        == 0o40000,  # check the directory bit (s_ifdir)
                        "size": stats.size,
                    }
                )
            except Exception:
                pass
    finally:
        try:
            await sftp_client.exit()
        except Exception:
            pass
    entries.sort(key=lambda entry: (not entry["is_directory"], entry["name"].lower()))
    return entries


async def sftp_is_directory(connection: asyncssh.SSHClientConnection, path: str) -> bool:
    """Return whether ``path`` resolves to a directory via SFTP.

    English:
        Performs an ``sftp.stat`` call and inspects the directory bit.

    日本語:
        ``sftp.stat`` を実行してディレクトリかどうかを判定します。

    Args:
        connection: Active SSH connection used to start SFTP.
        path: Remote path to probe.

    Returns:
        bool: ``True`` when ``path`` is a directory, otherwise ``False``.
    """

    sftp_client = await connection.start_sftp_client()
    try:
        stats = await sftp_client.stat(path)
        return (stats.permissions & 0o40000) == 0o40000
    finally:
        try:
            await sftp_client.exit()
        except Exception:
            pass


async def sftp_read_file(
    connection: asyncssh.SSHClientConnection,
    path: str,
    max_bytes: int = 65536,
) -> str:
    """Read a text file over SFTP, truncated to ``max_bytes``.

    English:
        Opens the remote file, reads up to ``max_bytes`` bytes, and returns a
        UTF-8 string with replacement for undecodable bytes.

    日本語:
        リモートファイルを開いて最大 ``max_bytes`` バイトまで読み込み、UTF-8 文字列
        として返します（復号できないバイトは置換します）。
    """

    sftp_client = await connection.start_sftp_client()
    try:
        async with await sftp_client.open(path, "r", encoding="utf-8") as remote_file:
            data = await remote_file.read(max_bytes)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return data
    finally:
        try:
            await sftp_client.exit()
        except Exception:
            pass


async def _expand_alias(alias: str) -> dict[str, str]:
    process: Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            _ssh_command(),
            "-G",
            alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:
        return {}

    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return {}

    data: dict[str, str] = {}
    for line in stdout.decode().splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            data[key.strip().lower()] = value.strip()

    return {
        "hostname": data.get("hostname"),
        "user": data.get("user"),
        "port": data.get("port"),
        "identityfile": data.get("identityfile"),
    }


def _is_resolvable(name: str) -> bool:
    """Return whether ``name`` resolves via DNS/system hosts.

    English:
        Lightweight guard to decide if ``ssh -G`` alias expansion is needed.

    日本語:
        DNS や hosts ファイルで名前解決できるかを確認し、エイリアス解決が必要かどうかを
        判定します。
    """

    try:
        socket.getaddrinfo(name, None)
        return True
    except OSError:
        return False


def _ssh_command() -> str:
    """Return the preferred ``ssh`` executable path for the current OS.

    English:
        On Windows the system OpenSSH path is used to avoid PATH hijacking;
        otherwise ``ssh`` from the user's PATH is returned.

    日本語:
        Windows では PATH 乗っ取りを避けるためにシステムの OpenSSH を優先し、
        それ以外ではユーザーの PATH にある ``ssh`` を利用します。
    """

    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", "C:\\Windows")
        candidate = Path(system_root) / "System32" / "OpenSSH" / "ssh.exe"
        if candidate.exists():
            return str(candidate)
    return "ssh"


LOGGER = logging.getLogger(__name__)
