"""WebSocket fan-out tests for /ws/progress."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from sshler import state
from sshler.webapp import Broadcaster, ServerSettings, make_app

TEST_TOKEN = "api-token"


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def build_client(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    state.reset_state()
    state.initialize(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_snapshot_on_connect_when_empty(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["bars"] == []
    finally:
        client.close()


def test_snapshot_includes_existing_bars(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        client.post(
            "/api/v1/progress/preexisting",
            headers=auth_headers(),
            json={"current": 5, "total": 10, "label": "before"},
        )
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["bars"]) == 1
            assert snapshot["bars"][0]["name"] == "preexisting"
            assert snapshot["bars"][0]["current"] == 5
            assert snapshot["bars"][0]["label"] == "before"
    finally:
        client.close()


def test_push_broadcasts_upsert_event(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"

            resp = client.post(
                "/api/v1/progress/live-bar",
                headers=auth_headers(),
                json={"current": 1, "total": 4, "color": "green"},
            )
            assert resp.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "upsert"
            assert event["name"] == "live-bar"
            assert event["bar"]["current"] == 1
            assert event["bar"]["total"] == 4
            assert event["bar"]["color"] == "green"
    finally:
        client.close()


def test_delete_broadcasts_delete_event(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        client.post(
            "/api/v1/progress/will-vanish",
            headers=auth_headers(),
            json={"current": 1, "total": 2},
        )
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            ws.receive_json()  # snapshot

            resp = client.delete(
                "/api/v1/progress/will-vanish", headers=auth_headers()
            )
            assert resp.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "delete"
            assert event["name"] == "will-vanish"
            assert event["bar"] is None
    finally:
        client.close()


def test_two_clients_both_receive_broadcast(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        with client.websocket_connect(
            f"/ws/progress?token={TEST_TOKEN}"
        ) as ws_a, client.websocket_connect(
            f"/ws/progress?token={TEST_TOKEN}"
        ) as ws_b:
            ws_a.receive_json()  # snapshot for A
            ws_b.receive_json()  # snapshot for B

            resp = client.post(
                "/api/v1/progress/fanout",
                headers=auth_headers(),
                json={"current": 2, "total": 3},
            )
            assert resp.status_code == 200

            event_a = ws_a.receive_json()
            event_b = ws_b.receive_json()
            assert event_a["type"] == "upsert"
            assert event_a["name"] == "fanout"
            assert event_b["type"] == "upsert"
            assert event_b["name"] == "fanout"
            assert event_a["bar"]["current"] == 2
            assert event_b["bar"]["current"] == 2
    finally:
        client.close()


def test_bad_token_closes_connection(tmp_path, monkeypatch):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/progress?token=wrong") as ws:
                ws.receive_json()
        assert exc_info.value.code == 4403
    finally:
        client.close()


# --- Broadcaster: one stalled client never stalls the others -------------------------


class FakeSocket:
    """A socket double. ``stall`` makes every send wait on an event nobody sets."""

    def __init__(self, stall: bool = False, fail: bool = False, delay: bool = False) -> None:
        self.stall = stall
        self.fail = fail
        self.delay = delay
        self.sent: list[dict] = []
        self.closed = False
        self.never = asyncio.Event()
        self.gate = asyncio.Event()

    async def send_json(self, event: dict) -> None:
        if self.stall:
            await self.never.wait()
        if self.delay:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("socket gone")
        self.sent.append(event)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True


async def _until(predicate, spins: int = 200) -> None:
    for _ in range(spins):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


def test_a_client_that_never_reads_does_not_stall_the_others():
    async def scenario() -> None:
        hub = Broadcaster()
        stuck, live = FakeSocket(stall=True), FakeSocket()
        await hub.connect(stuck)
        await hub.connect(live)

        await asyncio.wait_for(hub.broadcast({"n": 0}), timeout=5)
        await _until(lambda: live.sent == [{"n": 0}])

        late = FakeSocket()
        await asyncio.wait_for(hub.connect(late), timeout=5)
        await asyncio.wait_for(hub.disconnect(late), timeout=5)

        for n in range(1, 100):
            await hub.broadcast({"n": n})
        await _until(lambda: len(live.sent) == 100)
        assert live.sent == [{"n": n} for n in range(100)]
        assert stuck.sent == []
        await hub.disconnect(stuck)
        await hub.disconnect(live)

    asyncio.run(scenario())


def test_a_slow_client_is_dropped_and_closed_and_the_others_are_not():
    async def scenario() -> None:
        hub = Broadcaster()
        slow, live = FakeSocket(delay=True), FakeSocket()
        await hub.connect(slow)
        await hub.connect(live)
        for n in range(1000):
            await hub.broadcast({"n": n})
            await asyncio.sleep(0)
        await _until(lambda: slow.closed)
        await _until(lambda: len(live.sent) == 1000)
        assert live.sent == [{"n": n} for n in range(1000)]
        assert live.closed is False
        await hub.broadcast({"n": 1000})
        await _until(lambda: len(live.sent) == 1001)
        assert slow.sent == []
        await hub.disconnect(live)

    asyncio.run(scenario())


def test_a_client_whose_send_fails_is_dropped_and_closed():
    async def scenario() -> None:
        hub = Broadcaster()
        broken, live = FakeSocket(fail=True), FakeSocket()
        await hub.connect(broken)
        await hub.connect(live)
        await hub.broadcast({"n": 1})
        await _until(lambda: broken.closed)
        await hub.broadcast({"n": 2})
        await _until(lambda: live.sent == [{"n": 1}, {"n": 2}])
        assert broken.sent == []
        await hub.disconnect(live)

    asyncio.run(scenario())


def test_a_client_that_disconnects_with_events_queued_is_forgotten():
    async def scenario() -> None:
        hub = Broadcaster()
        leaving, live = FakeSocket(delay=True), FakeSocket()
        await hub.connect(leaving)
        await hub.connect(live)
        await hub.broadcast({"n": 1})
        await hub.disconnect(leaving)
        await hub.broadcast({"n": 2})
        await _until(lambda: live.sent == [{"n": 1}, {"n": 2}])
        leaving.gate.set()
        await asyncio.sleep(0)
        assert leaving.sent == []
        assert leaving.closed is False
        await hub.disconnect(live)

    asyncio.run(scenario())


def test_a_broadcast_with_no_client_returns():
    asyncio.run(asyncio.wait_for(Broadcaster().broadcast({"n": 1}), timeout=5))


def test_the_first_message_comes_before_every_later_event():
    async def scenario() -> None:
        hub = Broadcaster()
        sock = FakeSocket()

        release = asyncio.Event()

        async def first() -> dict:
            await release.wait()
            return {"type": "snapshot"}

        await hub.connect(sock, first=first())
        await hub.broadcast({"n": 1})
        await hub.broadcast({"n": 2})
        release.set()
        await _until(lambda: len(sock.sent) == 3)
        assert sock.sent == [{"type": "snapshot"}, {"n": 1}, {"n": 2}]
        await hub.disconnect(sock)

    asyncio.run(scenario())
