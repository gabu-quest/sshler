"""Configuration caching with TTL to avoid reloading on every request."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AppConfig


class ConfigCache:
    """Thread-safe configuration cache with TTL and file watching."""

    def __init__(self, ttl: int = 60):
        """Initialize the config cache.

        Args:
            ttl: Time-to-live in seconds before cache expires
        """
        self._cache: AppConfig | None = None
        self._lock = asyncio.Lock()
        self._cached_at: float | None = None
        self._ttl = ttl
        self._load_func = None
        self._signature: object | None = None

    async def get(self, load_func, *, signature: object | None = None) -> AppConfig:
        """Get cached config or reload if expired.

        Args:
            load_func: Function to load configuration
            signature: Optional value used to invalidate the cache when it changes

        Returns:
            Cached or freshly loaded AppConfig
        """
        async with self._lock:
            now = time.time()
            signature_changed = signature is not None and signature != self._signature

            # Check if cache is still valid
            if self._cache and self._cached_at and not signature_changed:
                if now - self._cached_at < self._ttl:
                    return self._cache

            # Reload config
            if asyncio.iscoroutinefunction(load_func):
                self._cache = await load_func()
            else:
                self._cache = await asyncio.to_thread(load_func)

            self._cached_at = now
            self._load_func = load_func
            self._signature = signature
            return self._cache

    def invalidate(self):
        """Force cache refresh on next request."""
        self._cache = None
        self._cached_at = None
        self._signature = None

    async def refresh(self):
        """Manually refresh the cache."""
        if self._load_func:
            await self.get(self._load_func, signature=self._signature)


# Global cache instance
_global_cache: ConfigCache | None = None


def get_cache(ttl: int = 60) -> ConfigCache:
    """Get the global config cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = ConfigCache(ttl=ttl)
    return _global_cache
