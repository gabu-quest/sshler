from __future__ import annotations

import logging
import os
import subprocess

from fastapi import APIRouter, Request

from .. import __version__
from ..pdf import PDF_RENDERER
from ..ssh_pool import get_pool
from .dependencies import APIDependencies
from .failure import REASON_INVALID, Failure
from .helpers import (
    LOCAL_IS_WINDOWS,
    available_windows_shells,
    default_windows_shell,
)
from .models import (
    APIBootstrap,
    APIPoolConfig,
    APIPoolConfigUpdate,
    APIWindowsShell,
)

logger = logging.getLogger(__name__)


def _detect_wsl_distro() -> str | None:
    """Detect WSL distro name. Works in both interactive and systemd contexts."""
    # Fast path: env var is set in interactive shells
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro:
        return distro

    # Systemd context: env var may not be set, detect via /proc/version + wslpath
    try:
        with open("/proc/version") as f:
            if "microsoft" not in f.read().lower():
                return None
        # WSL confirmed — get distro name via wslpath
        result = subprocess.run(
            ["wslpath", "-m", "/"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        # Output: //wsl.localhost/Ubuntu/ or //wsl$/Ubuntu/
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().rstrip("/").split("/")
            # ['', '', 'wsl.localhost', 'Ubuntu'] or ['', '', 'wsl$', 'Ubuntu']
            if len(parts) >= 4:
                return parts[3]
    except Exception as e:
        logger.debug(f"WSL detection failed: {e}")

    return None


# Detect once at import time
_WSL_DISTRO = _detect_wsl_distro()

# Enumerate selectable Windows shells once (cheap which/wsl probes).
if LOCAL_IS_WINDOWS:
    _WINDOWS_SHELLS = [
        APIWindowsShell(id=str(s["id"]), label=str(s["label"]), available=bool(s["available"]))
        for s in available_windows_shells()
    ]
    _DEFAULT_SHELL = default_windows_shell()
else:
    _WINDOWS_SHELLS = []
    _DEFAULT_SHELL = None


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/bootstrap", response_model=APIBootstrap)
    async def api_bootstrap(request: Request) -> APIBootstrap:
        """Expose runtime settings to bootstrap the SPA.

        The route is open without credentials when a password is set, so the sign-in
        page can load: ``auth_required`` says a password is set, ``authenticated``
        whether this request carried a live session or a good Basic header (the
        server's door decides and leaves it on ``request.state``), and the token is
        given, with the rest of the settings, only to a request that is authenticated.
        """
        auth_required = deps.settings.auth_manager is not None
        authenticated = not auth_required or getattr(request.state, "authenticated", False) is True

        if not authenticated:
            # A stranger gets only what the sign-in page needs.
            return APIBootstrap(
                version=__version__,
                token_header="X-SSHLER-TOKEN",
                token=None,
                auth_required=auth_required,
                authenticated=False,
                allow_origins=[],
                spa_base="/app/" if deps.settings.serve_spa else "",
                spa_enabled=deps.settings.serve_spa,
                platform="windows" if LOCAL_IS_WINDOWS else "posix",
            )

        return APIBootstrap(
            version=__version__,
            token_header="X-SSHLER-TOKEN",
            token=deps.settings.csrf_token,
            auth_required=auth_required,
            authenticated=True,
            allow_origins=deps.settings.allow_origins,
            spa_base="/app/" if deps.settings.serve_spa else "",
            spa_enabled=deps.settings.serve_spa,
            wsl_distro=_WSL_DISTRO,
            pdf_available=PDF_RENDERER.available,
            platform="windows" if LOCAL_IS_WINDOWS else "posix",
            windows_shells=_WINDOWS_SHELLS,
            default_shell=_DEFAULT_SHELL,
            artifact_server_enabled=bool(
                deps.settings.serve_artifacts
                and deps.settings.artifact_server_port_actual is not None
            ),
            artifact_server_port=deps.settings.artifact_server_port_actual,
        )

    @router.get("/pool/config", response_model=APIPoolConfig)
    async def get_pool_config() -> APIPoolConfig:
        """Get current SSH connection pool configuration."""
        pool = get_pool()
        config = pool.get_config()
        return APIPoolConfig(
            idle_timeout=config["idle_timeout"],
            max_lifetime=config["max_lifetime"],
            max_connections_per_box=config["max_connections_per_box"] or 5,
        )

    @router.put("/pool/config", response_model=APIPoolConfig)
    async def update_pool_config(update: APIPoolConfigUpdate) -> APIPoolConfig:
        """Update SSH connection pool configuration dynamically."""
        pool = get_pool()

        # Validate inputs
        if update.idle_timeout is not None and update.idle_timeout < 0:
            raise Failure(REASON_INVALID, "idle_timeout must be >= 0 or null")
        if update.max_lifetime is not None and update.max_lifetime < 0:
            raise Failure(REASON_INVALID, "max_lifetime must be >= 0 or null")
        if update.max_connections_per_box is not None and update.max_connections_per_box < 1:
            raise Failure(REASON_INVALID, "max_connections_per_box must be >= 1")

        # Apply updates
        pool.update_config(
            idle_timeout=update.idle_timeout,
            max_lifetime=update.max_lifetime,
            max_connections_per_box=update.max_connections_per_box,
        )

        # Return updated config
        config = pool.get_config()
        return APIPoolConfig(
            idle_timeout=config["idle_timeout"],
            max_lifetime=config["max_lifetime"],
            max_connections_per_box=config["max_connections_per_box"] or 5,
        )

    return router
