"""Tests for command injection prevention in subprocess calls."""

from __future__ import annotations

import asyncio
import json

import pytest

from sshler import ssh, webapp
from sshler.validation import PathValidator, ValidationError


class TestSessionNameSanitization:
    """Test that session name sanitization prevents command injection."""

    def test_valid_session_names(self):
        """Test that valid session names are accepted."""
        valid_names = [
            "sshler",
            "my-session",
            "session_123",
            "dev.server",
            "alpha-beta_123.test",
        ]

        for name in valid_names:
            sanitized = PathValidator.sanitize_session_name(name)
            assert sanitized == name, f"Valid name {name} should not be modified"

    def test_sanitize_removes_dangerous_chars(self):
        """Test that dangerous characters are replaced."""
        test_cases = [
            # (input, expected_output, description)
            ("session;ls", "session_ls", "semicolon command separator"),
            ("session|cat", "session_cat", "pipe command"),
            ("session&whoami", "session_whoami", "background command"),
            ("session$(id)", "session__id_", "command substitution"),
            ("session`id`", "session_id_", "backtick command substitution"),
            ("session>file", "session_file", "output redirection"),
            ("session<file", "session_file", "input redirection"),
            ("session\nls", "session_ls", "newline injection"),
            ("session\rls", "session_ls", "carriage return"),
            ("session\\t", "session_t", "backslash (literal, not tab)"),
            ("session'test'", "session_test_", "single quotes"),
            ('session"test"', "session_test_", "double quotes"),
            ("session(test)", "session_test_", "parentheses"),
            ("session[test]", "session_test_", "brackets"),
            ("session{test}", "session_test_", "braces"),
            ("session*glob", "session_glob", "glob wildcard"),
            ("session?glob", "session_glob", "glob single char"),
            ("session~user", "session_user", "tilde expansion"),
            ("session$PATH", "session_PATH", "variable expansion"),
        ]

        for input_name, expected, description in test_cases:
            sanitized = PathValidator.sanitize_session_name(input_name)
            assert sanitized == expected, f"Failed to sanitize {description}: {input_name}"

    def test_empty_session_name_rejected(self):
        """Test that empty session names are rejected."""
        with pytest.raises(ValidationError, match="at least one valid character"):
            PathValidator.sanitize_session_name("")

    def test_only_special_chars_converted_to_underscores(self):
        """Test that session names with only special characters become underscores."""
        # Note: sanitize_session_name() replaces invalid chars with underscores
        # It only raises ValidationError if the result is completely empty
        test_cases = [
            (";;;", "___"),
            ("|||", "___"),
            ("&&&", "___"),
            ("$()", "___"),
            ("``", "__"),
            (">>>", "___"),
        ]

        for input_name, expected in test_cases:
            sanitized = PathValidator.sanitize_session_name(input_name)
            assert sanitized == expected

    def test_preserves_alphanumeric_and_safe_chars(self):
        """Test that alphanumeric and safe characters are preserved."""
        test_cases = [
            "abc123",
            "test-session",
            "my_session",
            "dev.server",
            "alpha-beta_123.test",
            "ABCabc123",
        ]

        for name in test_cases:
            assert PathValidator.sanitize_session_name(name) == name

    def test_real_world_attack_attempts(self):
        """Test real-world command injection attack patterns."""
        attack_attempts = [
            # Command injection attempts
            "session; rm -rf /",
            "session; cat /etc/passwd",
            "session && whoami",
            "session || ls -la",
            # Command substitution
            "session$(cat /etc/shadow)",
            "session`cat /etc/passwd`",
            # Escape attempts
            "session\\; ls",
            'session\\"; ls',
            # Multiple injection techniques
            "session;|&$()`",
            # Newline/carriage return injection
            "session\n\rcat /etc/passwd",
        ]

        for attack in attack_attempts:
            sanitized = PathValidator.sanitize_session_name(attack)
            # Sanitized version should not contain dangerous characters
            dangerous_chars = [";", "|", "&", "$", "`", ">", "<", "\n", "\r", "\\", "'", '"']
            for char in dangerous_chars:
                assert char not in sanitized, (
                    f"Dangerous char {repr(char)} found in sanitized output: {sanitized}"
                )

    def test_unicode_and_special_chars(self):
        """Test handling of Unicode and special characters."""
        test_cases = [
            ("session™", "session_"),  # Trademark symbol
            ("session\u00a9", "session_"),  # Copyright
            ("session✓", "session_"),  # Checkmark
            ("session\x00", "session_"),  # Null byte
            ("session\t", "session_"),  # Tab
        ]

        for input_name, expected in test_cases:
            sanitized = PathValidator.sanitize_session_name(input_name)
            assert sanitized == expected

    def test_length_preservation(self):
        """Test that sanitization doesn't add unexpected length."""
        original = "my-session_123.test"
        sanitized = PathValidator.sanitize_session_name(original)
        # Length should be the same (all chars are valid)
        assert len(sanitized) == len(original)

    def test_mixed_valid_and_invalid_chars(self):
        """Test sessions with mix of valid and invalid characters."""
        # "my;session" should become "my_session"
        assert PathValidator.sanitize_session_name("my;session") == "my_session"
        # "test|123" should become "test_123"
        assert PathValidator.sanitize_session_name("test|123") == "test_123"
        # "prod&server" should become "prod_server"
        assert PathValidator.sanitize_session_name("prod&server") == "prod_server"


HOSTILE_NAME = "x; rm -rf ~ $(id)"
HOSTILE_DIR = "/srv/a b;id"


class _RecordingConnection:
    """Stands in for an asyncssh connection: records every command string it is given."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def create_process(self, command: str, **_kwargs):
        self.commands.append(command)
        return object()

    async def run(self, command: str, check: bool = False):
        self.commands.append(command)


class TestRemoteTmuxCommandStrings:
    """Remote tmux runs through a shell, so every user-controlled value must be quoted."""

    def test_open_tmux_sanitizes_session_and_quotes_directory(self):
        """Kills: dropping `open_tmux`'s own session sanitization, and dropping
        `shlex.quote` around `working_directory`."""
        connection = _RecordingConnection()
        asyncio.run(ssh.open_tmux(connection, working_directory=HOSTILE_DIR, session="evil;id"))
        assert connection.commands == ["tmux new -As evil_id -c '/srv/a b;id'"]

    def test_remote_rename_window_quotes_new_name(self):
        """Kills: dropping `shlex.quote(str(new_name))` in the remote rename branch."""
        connection = _RecordingConnection()
        payload = json.dumps({"op": "rename-window", "target": HOSTILE_NAME})
        asyncio.run(webapp._handle_control_message(payload, object(), connection, "demo", "ssh"))
        assert connection.commands == ["tmux rename-window -t demo 'x; rm -rf ~ $(id)'"]

    def test_remote_select_window_quotes_target(self):
        """Kills: dropping `shlex.quote(str(target))` in the remote select-window branch."""
        connection = _RecordingConnection()
        payload = json.dumps({"op": "select-window", "target": "1;reboot"})
        asyncio.run(webapp._handle_control_message(payload, object(), connection, "demo", "ssh"))
        assert connection.commands == ["tmux select-window -t demo:'1;reboot'"]


class TestLocalTmuxArgv:
    """Local tmux runs without a shell: hostile values must stay one argv element."""

    def test_local_rename_window_passes_name_as_one_argument(self, monkeypatch):
        """Kills: building the local rename as a shell string or splitting the name."""
        calls: list[tuple[str, list[str]]] = []

        async def fake_run(session: str, args: list[str]):
            calls.append((session, args))

        monkeypatch.setattr(webapp, "_run_local_tmux_command", fake_run)
        payload = json.dumps({"op": "rename-window", "target": HOSTILE_NAME})
        asyncio.run(webapp._handle_control_message(payload, object(), None, "demo", "local"))
        assert calls == [("demo", ["rename-window", "-t", "demo", HOSTILE_NAME])]

    def test_open_local_tmux_quotes_every_argument_for_script(self, monkeypatch):
        """`_open_local_tmux` (the `script -c` fallback) hands `script` one shell string.

        Kills: dropping `shlex.quote` from the `cmd_str` join (the directory's `;`
        would end the tmux command inside `script`'s shell).
        """
        captured: list[tuple[str, ...]] = []

        async def fake_exec(*argv, **_kwargs):
            captured.append(argv)
            return object()

        monkeypatch.setattr(webapp, "LOCAL_IS_WINDOWS", False)
        monkeypatch.setattr(webapp.asyncio, "create_subprocess_exec", fake_exec)
        asyncio.run(webapp._open_local_tmux(HOSTILE_DIR, "demo"))
        assert captured == [
            (
                "script",
                "-qefc",
                "tmux -L ts-demo new -As demo -c '/srv/a b;id'",
                "/dev/null",
            )
        ]
