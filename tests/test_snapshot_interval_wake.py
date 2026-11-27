"""A new snapshot interval wakes the loop, and the hourly purge runs by elapsed time."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sshler import snapshot
from sshler.api import recovery
from sshler.webapp import ServerSettings, make_app


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_purge_runs_at_3600_and_7200_seconds_across_an_interval_change(monkeypatch):
    """Intervals 300 s then 5 s: the old tick count purged at 3300 s and never again
    on the hour (purges at [3300, 6900, ...] instead of [3600, 7200])."""
    clock = _Clock()
    settings = SimpleNamespace(snapshot_enabled=False, snapshot_interval=300)
    purged_at: list[float] = []

    async def fake_purge() -> int:
        purged_at.append(clock.now)
        settings.snapshot_interval = 5  # changed after the first purge, at t=3600
        return 0

    async def fake_wait(interval: float) -> None:
        if clock.now >= 7200:
            raise asyncio.CancelledError
        clock.now += interval

    monkeypatch.setattr(snapshot, "_sleep_or_wake", fake_wait)
    monkeypatch.setattr(snapshot.state, "purge_stale_snapshots_async", fake_purge)
    with pytest.raises(asyncio.CancelledError):
        await snapshot.snapshot_loop(settings=settings, clock=clock)
    assert purged_at == [3600.0, 7200.0]


@pytest.mark.asyncio
async def test_notify_interval_changed_ends_a_long_wait_at_once():
    """A 300 s wait ends when the interval is changed, not 300 s later."""
    snapshot._wake = asyncio.Event()
    task = asyncio.create_task(snapshot._sleep_or_wake(300))
    await asyncio.sleep(0)
    assert not task.done()
    snapshot.notify_interval_changed()
    await asyncio.wait_for(task, timeout=2)
    assert task.done()


@pytest.mark.asyncio
async def test_a_wait_without_a_wake_runs_its_whole_interval():
    snapshot._wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    start = loop.time()
    await snapshot._sleep_or_wake(0.05)
    assert loop.time() - start >= 0.04


def test_put_interval_wakes_the_loop_once(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(recovery, "notify_interval_changed", lambda: calls.append(1))
    server = ServerSettings(csrf_token="t", snapshot_enabled=True, snapshot_interval=300)
    client = TestClient(make_app(server), headers={"X-SSHLER-TOKEN": "t"})
    response = client.put("/api/v1/snapshot/config", json={"interval": 5})
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "interval": 5}
    assert calls == [1]
