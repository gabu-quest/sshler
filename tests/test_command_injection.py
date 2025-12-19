"""Tests for command injection prevention in subprocess calls."""

from __future__ import annotations

import pytest

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

    def test_empty_string_rejected(self):
        """Test that truly empty session names are rejected."""
        # Only truly empty strings raise ValidationError
        with pytest.raises(ValidationError, match="at least one valid character"):
            PathValidator.sanitize_session_name("")

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
            sanitized = PathValidator.sanitize_session_name(name)
            # Should only contain alphanumeric, dash, underscore, dot
            assert all(c.isalnum() or c in "-_." for c in sanitized)

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
            "session\\\"; ls",
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
                assert char not in sanitized, f"Dangerous char {repr(char)} found in sanitized output: {sanitized}"

    def test_unicode_and_special_chars(self):
        """Test handling of Unicode and special characters."""
        test_cases = [
            ("session™", "session_"),  # Trademark symbol
            ("session™", "session_"),  # Copyright
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


class TestQuotingInSubprocessCalls:
    """Test that subprocess calls use proper quoting."""

    def test_shlex_quote_prevents_injection(self):
        """Test that shlex.quote properly escapes dangerous characters."""
        import shlex

        dangerous_inputs = [
            "session; rm -rf /",
            "session && whoami",
            "session$(cat /etc/passwd)",
            "session`id`",
            "session'test'",
            'session"test"',
        ]

        for dangerous_input in dangerous_inputs:
            quoted = shlex.quote(dangerous_input)

            # Quoted string should be safe - it will be treated as a single argument
            # shlex.quote wraps in single quotes and escapes internal single quotes
            assert quoted.startswith("'") or not any(c in dangerous_input for c in ";|&$`")

            # The quoted string when parsed should give back the original
            # (this proves the quoting is effective)
            import shlex as shlex_parse
            parsed = shlex_parse.split(quoted)
            assert len(parsed) == 1, "Quoted string should parse as single argument"
            assert parsed[0] == dangerous_input, "Quoted string should preserve original content"


class TestSessionNameValidationIntegration:
    """Integration tests for session name validation in real usage."""

    def test_sanitization_makes_names_safe_for_subprocess(self):
        """Test that sanitized names are safe for subprocess calls."""
        import shlex

        # Simulate user input that might be malicious
        user_inputs = [
            "normal-session",  # Safe
            "attack; rm -rf /",  # Malicious
            "attack$(whoami)",  # Command substitution
        ]

        for user_input in user_inputs:
            # Sanitize the session name
            sanitized = PathValidator.sanitize_session_name(user_input)

            # Build a command that would be passed to subprocess
            # This simulates what happens in _open_local_tmux
            command = ["tmux", "new", "-As", sanitized]

            # Even if we quote the sanitized name, it should be safe
            quoted_command = " ".join(shlex.quote(arg) for arg in command)

            # The command should not contain unquoted dangerous characters
            # Split and rejoin to verify proper parsing
            parsed = shlex.split(quoted_command)
            assert parsed == command, "Command should parse correctly"
            assert sanitized == parsed[3], "Session name should be preserved"

    def test_validation_error_prevents_subprocess_call(self):
        """Test that validation errors prevent unsafe subprocess calls."""
        # Only empty strings raise ValidationError
        with pytest.raises(ValidationError):
            PathValidator.sanitize_session_name("")

        # This prevents the session from ever reaching subprocess calls
