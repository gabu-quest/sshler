"""The snapshot loop follows runtime changes to the snapshot settings.

``PUT /api/v1/snapshot/config`` writes to the server settings object; the loop
reads that same object on every tick, so turning snapshots off stops them and a
new interval applies to the next sleep.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sshler import snapshot
from sshler.webapp import ServerSettings, make_app


class _Sleeps:
    """Stand-in for ``asyncio`` inside sshler.snapshot: each sleep runs the
    next scripted step (which may change settings), then the loop is cancelled
    once the script is used up."""

    CancelledError = asyncio.CancelledError

    def __init__(self, settings, steps):
        self.settings = settings
        self.steps = list(steps)
        self.requested: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.requested.append(delay)
        if not self.steps:
            raise asyncio.CancelledError
        self.steps.pop(0)(self.settings)

    def __getattr__(self, name):
        return getattr(asyncio, name)


async def _run_loop(settings, steps, monkeypatch):
    taken: list[int] = []

    async def fake_snapshot_all() -> int:
        taken.append(len(taken) + 1)
        return 0

    async def fake_purge() -> int:
        return 0

    sleeps = _Sleeps(settings, steps)
    monkeypatch.setattr(snapshot, "asyncio", sleeps)
    monkeypatch.setattr(snapshot, "snapshot_all_sessions", fake_snapshot_all)
    monkeypatch.setattr(snapshot.state, "purge_stale_snapshots_async", fake_purge)
    with pytest.raises(asyncio.CancelledError):
        await snapshot.snapshot_loop(settings=settings)
    return taken, sleeps.requested


@pytest.mark.asyncio
async def test_loop_stops_snapshotting_when_disabled_at_runtime(monkeypatch):
    """Mutation killed: the loop reading ``snapshot_enabled`` once at start
    (a snapshot is still taken on the tick after the flag is turned off:
    taken == [1, 2] instead of [1])."""
    settings = SimpleNamespace(snapshot_enabled=True, snapshot_interval=30)

    def noop(_s):
        pass

    def turn_off(s):
        s.snapshot_enabled = False

    taken, _ = await _run_loop(settings, [noop, turn_off], monkeypatch)
    assert taken == [1]


@pytest.mark.asyncio
async def test_loop_uses_the_new_interval_on_the_next_sleep(monkeypatch):
    """Mutation killed: the loop reading ``snapshot_interval`` once at start
    (requested sleeps are [30, 30, 30] instead of [30, 30, 60])."""
    settings = SimpleNamespace(snapshot_enabled=True, snapshot_interval=30)

    def noop(_s):
        pass

    def slower(s):
        s.snapshot_interval = 60

    _, requested = await _run_loop(settings, [noop, slower], monkeypatch)
    assert requested == [30, 30, 60]


def test_put_snapshot_config_reaches_the_running_loop(monkeypatch):
    """The settings object the lifespan hands to ``snapshot_loop`` is the one
    ``PUT /api/v1/snapshot/config`` changes.

    Mutation killed: the lifespan passing a copy of the settings (the loop's
    ``snapshot_enabled`` stays True and its interval stays 30 after the PUT).
    """
    import sshler.webapp as webapp

    seen: list[object] = []

    async def fake_loop(settings) -> None:
        seen.append(settings)
        await asyncio.Event().wait()

    async def nothing(*_a, **_k):
        return None

    async def no_sessions():
        return []

    async def zero() -> int:
        return 0

    monkeypatch.setattr(snapshot, "snapshot_loop", fake_loop)
    monkeypatch.setattr(snapshot, "reconcile_on_startup", no_sessions)
    monkeypatch.setattr(webapp, "load_config", lambda: None)
    monkeypatch.setattr(webapp, "initialize_pool", nothing)
    monkeypatch.setattr(webapp, "shutdown_pool", nothing)
    monkeypatch.setattr(webapp, "get_pool", lambda: None)
    monkeypatch.setattr(webapp.state, "purge_stale_snapshots_async", zero)
    from sshler.pdf import PDF_RENDERER

    monkeypatch.setattr(PDF_RENDERER, "start", nothing)
    monkeypatch.setattr(PDF_RENDERER, "stop", nothing)

    server = ServerSettings(csrf_token="t", snapshot_enabled=True, snapshot_interval=30)
    with TestClient(make_app(server), headers={"X-SSHLER-TOKEN": "t"}) as client:
        response = client.put(
            "/api/v1/snapshot/config", json={"enabled": False, "interval": 90}
        )
        assert response.status_code == 200
        assert response.json() == {"enabled": False, "interval": 90}
        assert len(seen) == 1
        loop_settings = seen[0]
        assert (loop_settings.snapshot_enabled, loop_settings.snapshot_interval) == (False, 90)
