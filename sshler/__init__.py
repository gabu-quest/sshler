"""Package metadata export for sshler."""

from importlib import metadata

__all__ = ["__version__"]

try:
    __version__ = metadata.version("sshler")
except metadata.PackageNotFoundError:  # pragma: no cover - fallback for editable installs
    __version__ = "0.0.0"
