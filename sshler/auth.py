"""Authentication and password security for sshler.

This module provides password validation, hashing, and authentication
using Argon2id for secure password storage and verification.
"""

from __future__ import annotations

import base64
import re
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError


# Top 100 most common passwords (subset for validation)
COMMON_PASSWORDS = {
    "password", "123456", "123456789", "12345678", "12345", "1234567",
    "password1", "1234567890", "qwerty", "abc123", "111111", "123123",
    "admin", "letmein", "welcome", "monkey", "password123", "dragon",
    "master", "sunshine", "princess", "football", "shadow", "superman",
    "qazwsx", "trustno1", "passw0rd", "admin123", "guest", "test",
    "root", "changeme", "default", "user", "demo", "sample",
}


@dataclass
class PasswordPolicy:
    """Password complexity requirements."""

    min_length: int = 12
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digits: bool = True
    require_special: bool = True
    special_chars: str = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    check_common: bool = True
    min_entropy_bits: float = 50.0


class PasswordValidator:
    """Validates password strength and complexity."""

    @staticmethod
    def validate(
        password: str, username: str | None = None, policy: PasswordPolicy | None = None
    ) -> tuple[bool, list[str]]:
        """Validate a password against policy requirements.

        Args:
            password: Password to validate
            username: Optional username (password must not contain it)
            policy: Password policy (uses default if None)

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        if policy is None:
            policy = PasswordPolicy()

        errors = []

        # Length check
        if len(password) < policy.min_length:
            errors.append(f"Must be at least {policy.min_length} characters long")

        # Uppercase check
        if policy.require_uppercase and not re.search(r"[A-Z]", password):
            errors.append("Must contain at least 1 uppercase letter (A-Z)")

        # Lowercase check
        if policy.require_lowercase and not re.search(r"[a-z]", password):
            errors.append("Must contain at least 1 lowercase letter (a-z)")

        # Digit check
        if policy.require_digits and not re.search(r"[0-9]", password):
            errors.append("Must contain at least 1 digit (0-9)")

        # Special character check
        if policy.require_special:
            special_pattern = f"[{re.escape(policy.special_chars)}]"
            if not re.search(special_pattern, password):
                errors.append(
                    f"Must contain at least 1 special character ({policy.special_chars})"
                )

        # Common password check
        if policy.check_common and PasswordValidator.is_common_password(password):
            errors.append("Cannot be a commonly used password")

        # Username in password check
        if username and username.lower() in password.lower():
            errors.append("Cannot contain your username")

        # Entropy check
        entropy = PasswordValidator.calculate_entropy(password)
        if entropy < policy.min_entropy_bits:
            errors.append(
                f"Password is too predictable (entropy: {entropy:.1f} bits, "
                f"minimum: {policy.min_entropy_bits:.1f} bits)"
            )

        return len(errors) == 0, errors

    @staticmethod
    def is_common_password(password: str) -> bool:
        """Check if password is in the common passwords list.

        Args:
            password: Password to check

        Returns:
            True if password is common, False otherwise
        """
        return password.lower() in COMMON_PASSWORDS

    @staticmethod
    def calculate_entropy(password: str) -> float:
        """Calculate password entropy in bits.

        Args:
            password: Password to analyze

        Returns:
            Estimated entropy in bits
        """
        import math

        # Determine character set size
        char_set_size = 0
        if re.search(r"[a-z]", password):
            char_set_size += 26
        if re.search(r"[A-Z]", password):
            char_set_size += 26
        if re.search(r"[0-9]", password):
            char_set_size += 10
        if re.search(r"[^a-zA-Z0-9]", password):
            char_set_size += 32  # Estimate for special chars

        if char_set_size == 0:
            return 0.0

        # Entropy = log2(charset_size^length)
        return len(password) * math.log2(char_set_size)


class PasswordHasher:
    """Secure password hashing using Argon2id."""

    def __init__(self):
        """Initialize the Argon2 password hasher with secure defaults."""
        self._hasher = Argon2PasswordHasher(
            time_cost=2,  # Number of iterations
            memory_cost=102400,  # 100 MB
            parallelism=8,  # Number of parallel threads
            hash_len=32,  # Length of hash in bytes
            salt_len=16,  # Length of salt in bytes
        )

    def hash(self, password: str) -> str:
        """Hash a password using Argon2id.

        Args:
            password: Plain text password

        Returns:
            Argon2 hash string
        """
        return self._hasher.hash(password)

    def verify(self, password: str, hash: str) -> bool:
        """Verify a password against its hash.

        Args:
            password: Plain text password
            hash: Argon2 hash to verify against

        Returns:
            True if password matches hash, False otherwise
        """
        try:
            self._hasher.verify(hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False

    def needs_rehash(self, hash: str) -> bool:
        """Check if a hash needs to be updated with current parameters.

        Args:
            hash: Argon2 hash to check

        Returns:
            True if hash should be regenerated
        """
        return self._hasher.check_needs_rehash(hash)


class AuthManager:
    """Manages authentication for sshler."""

    def __init__(self, username: str, password_hash: str):
        """Initialize auth manager with credentials.

        Args:
            username: Username for authentication
            password_hash: Argon2 hash of the password
        """
        self.username = username
        self.password_hash = password_hash
        self._hasher = PasswordHasher()

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate a username/password combination.

        Uses constant-time comparison for username and Argon2's
        built-in constant-time verification for password.

        Args:
            username: Username to verify
            password: Password to verify

        Returns:
            True if credentials are valid, False otherwise
        """
        # Constant-time username comparison
        username_match = secrets.compare_digest(username, self.username)

        # Argon2 verification (constant-time)
        password_match = self._hasher.verify(password, self.password_hash)

        return username_match and password_match

    @staticmethod
    def generate_basic_auth_header(username: str, password: str) -> str:
        """Generate HTTP Basic Auth header value.

        Args:
            username: Username
            password: Password

        Returns:
            Basic auth header value (e.g., "Basic dXNlcjpwYXNz")
        """
        credentials = f"{username}:{password}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    def verify_basic_auth_header(self, auth_header: str) -> bool:
        """Verify an HTTP Basic Auth header.

        Args:
            auth_header: Full Authorization header value

        Returns:
            True if credentials are valid, False otherwise
        """
        if not auth_header or not auth_header.startswith("Basic "):
            return False

        try:
            # Decode base64 credentials
            encoded = auth_header[6:]  # Remove "Basic " prefix
            decoded = base64.b64decode(encoded).decode("utf-8")

            # Split username:password
            if ":" not in decoded:
                return False

            username, password = decoded.split(":", 1)

            # Authenticate
            return self.authenticate(username, password)
        except (ValueError, UnicodeDecodeError):
            return False
