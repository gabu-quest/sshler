from __future__ import annotations

import shlex

import asyncssh


class SSHError(Exception):
    """Raised when an SSH connection or command fails."""


async def connect(
    host: str,
    user: str,
    port: int = 22,
    keyfile: str | None = None,
    known_hosts: str | None = None,
) -> asyncssh.SSHClientConnection:
    """Establish an SSH connection using asyncssh.

    Args:
        host: Target host to reach.
        user: Username used for the SSH session.
        port: SSH port exposed by the host.
        keyfile: Optional explicit private-key path.
        known_hosts: Known-hosts override or ``"ignore"`` to disable checks.

    Returns:
        asyncssh.SSHClientConnection: Live SSH connection instance.

    Raises:
        SSHError: Propagates connection issues through a project-specific type.
    """

    if known_hosts and isinstance(known_hosts, str) and known_hosts.lower() == "ignore":
        known_hosts_path = None
    else:
        known_hosts_path = known_hosts

    try:
        connection = await asyncssh.connect(
            host=host,
            port=port,
            username=user,
            client_keys=[keyfile] if keyfile else None,
            known_hosts=known_hosts_path,
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
