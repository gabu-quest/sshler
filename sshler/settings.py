"""Application settings and configuration.

Loads configuration from environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SshlerSettings(BaseSettings):
    """sshler application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="127.0.0.1", description="Host to bind to")
    port: int = Field(default=8822, description="Port to bind to")
    public_url: str = Field(
        default="http://localhost:8822",
        description="Public URL where sshler is accessible (used for CORS/origin checks)",
    )

    # Session/Cookie settings
    cookie_name: str = Field(default="sshler_session", description="Session cookie name")
    cookie_secure: bool = Field(
        default=True,
        description="Set Secure flag on cookie (require HTTPS). Set to false for localhost dev.",
    )
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description="SameSite cookie attribute",
    )
    session_ttl_seconds: int = Field(
        default=28800,  # 8 hours
        description="Session time-to-live in seconds",
    )
    session_idle_timeout_seconds: int = Field(
        default=0,  # 0 = disabled
        description="Session idle timeout in seconds (0 to disable)",
    )

    # CORS settings
    allowed_origins: str = Field(
        default="",
        description="Comma-separated list of allowed CORS origins (empty = CORS disabled)",
    )
    origin_check_enabled: bool = Field(
        default=True,
        description="Enable Origin header validation on state-changing requests",
    )

    # Auth settings
    require_auth: bool = Field(
        default=True,
        description="Require authentication (should always be True in production)",
    )
    username: str | None = Field(
        default=None,
        alias="sshler_username",
        description="Username for authentication",
    )
    password_hash: str | None = Field(
        default=None,
        alias="sshler_password_hash",
        description="Argon2 hash of password",
    )
    password: str | None = Field(
        default=None,
        alias="sshler_password",
        description="Plain password (NOT recommended - use password_hash instead)",
    )

    # Rate limiting
    auth_failed_lockout: int = Field(
        default=300,
        description="Seconds to lock out after failed auth attempts",
    )

    # Data/storage settings
    config_dir: str | None = Field(
        default=None,
        alias="sshler_config_dir",
        description="Custom sshler config directory",
    )
    ssh_config: str | None = Field(
        default=None,
        alias="sshler_ssh_config",
        description="Path to custom SSH config file",
    )

    # Connection pool settings
    pool_idle_timeout: int | str = Field(
        default=1800,
        description="Idle timeout for SSH connections (seconds or 'forever')",
    )
    pool_max_lifetime: int | str = Field(
        default=3600,
        description="Max lifetime for SSH connections (seconds or 'forever')",
    )
    pool_max_connections: int = Field(
        default=3,
        description="Max pooled connections per box",
    )

    # Upload settings
    max_upload_mb: int = Field(
        default=50,
        description="Maximum upload size in MB",
    )

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse allowed_origins into a list."""
        if not self.allowed_origins:
            return []
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def cors_enabled(self) -> bool:
        """Check if CORS should be enabled."""
        return len(self.allowed_origins_list) > 0


# Global settings instance
_settings: SshlerSettings | None = None


def get_settings() -> SshlerSettings:
    """Get or create global settings instance."""
    global _settings
    if _settings is None:
        _settings = SshlerSettings()
    return _settings


def reset_settings() -> None:
    """Reset settings (useful for testing)."""
    global _settings
    _settings = None
