"""The local stats sampler must not block the event loop."""

from __future__ import annotations

import threading

import psutil
import pytest

from sshler.api import boxes


@pytest.mark.asyncio
async def test_local_cpu_sample_runs_off_the_loop(monkeypatch):
    seen: list[int] = []

    def fake_cpu(interval=None):
        seen.append(threading.get_ident())
        return 1.0

    monkeypatch.setattr(psutil, "cpu_percent", fake_cpu)
    await boxes._get_local_stats("local")
    assert seen and seen[0] != threading.get_ident()
