"""Tests for tunnel API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sshler.api.tunnels import TunnelInfo, TunnelRegistry


@pytest.fixture
def registry() -> TunnelRegistry:
    """A fresh registry, as ``make_app`` makes one per app."""
    return TunnelRegistry()


class TestTunnelRegistry:
    def test_tunnel_info_creation(self):
        t = TunnelInfo(
            id="test-123",
            box="mybox",
            tunnel_type="local",
            local_host="127.0.0.1",
            local_port=8080,
            remote_host="127.0.0.1",
            remote_port=80,
        )
        assert t.id == "test-123"
        assert t.box == "mybox"
        assert t.tunnel_type == "local"
        assert t.local_port == 8080
        assert t.remote_port == 80
        assert t.listener is None
        assert t.created_at > 0

    def test_active_tunnels_registry(self, registry):
        t = TunnelInfo(
            id="t1",
            box="box1",
            tunnel_type="local",
            local_host="127.0.0.1",
            local_port=3000,
            remote_host="127.0.0.1",
            remote_port=3000,
        )
        registry.add(t)
        found = registry.get("t1")
        assert found is not None
        assert found.box == "box1"

        assert registry.remove("t1") is t
        assert registry.get("t1") is None

    def test_filter_tunnels_by_box(self, registry):
        for tunnel_id, box, port in (
            ("t1", "box1", 3000),
            ("t2", "box2", 5432),
            ("t3", "box1", 8080),
        ):
            registry.add(
                TunnelInfo(
                    id=tunnel_id,
                    box=box,
                    tunnel_type="local",
                    local_host="127.0.0.1",
                    local_port=port,
                    remote_host="127.0.0.1",
                    remote_port=port,
                    created_at=port,
                )
            )

        box1_tunnels = registry.list_for_box("box1")
        assert [t.id for t in box1_tunnels] == ["t1", "t3"]
        assert [t.id for t in registry.list_for_box("box2")] == ["t2"]

    def test_removing_a_tunnel_closes_its_listener_and_connection(self, registry):
        mock_listener = MagicMock()
        mock_connection = MagicMock()
        registry.add(
            TunnelInfo(
                id="t1",
                box="box1",
                tunnel_type="local",
                local_host="127.0.0.1",
                local_port=3000,
                remote_host="127.0.0.1",
                remote_port=3000,
                listener=mock_listener,
                connection=mock_connection,
            )
        )

        registry.remove("t1")

        mock_listener.close.assert_called_once()
        mock_connection.close.assert_called_once()

    def test_close_all_closes_the_rest_when_one_listener_fails(self, registry):
        broken = MagicMock()
        broken.close.side_effect = OSError("already gone")
        fine = MagicMock()
        for tunnel_id, listener in (("t1", broken), ("t2", fine)):
            registry.add(
                TunnelInfo(
                    id=tunnel_id,
                    box="box1",
                    tunnel_type="local",
                    local_host="127.0.0.1",
                    local_port=3000,
                    remote_host="127.0.0.1",
                    remote_port=3000,
                    listener=listener,
                )
            )

        registry.close_all()

        fine.close.assert_called_once()
        assert registry.list_for_box("box1") == []


class TestTunnelApiModels:
    def test_tunnel_to_api_conversion(self):
        from sshler.api.tunnels import _tunnel_to_api

        t = TunnelInfo(
            id="t1",
            box="mybox",
            tunnel_type="local",
            local_host="0.0.0.0",
            local_port=8080,
            remote_host="10.0.0.1",
            remote_port=80,
        )
        api = _tunnel_to_api(t)
        assert api.id == "t1"
        assert api.box == "mybox"
        assert api.tunnel_type == "local"
        assert api.local_host == "0.0.0.0"
        assert api.local_port == 8080
        assert api.remote_host == "10.0.0.1"
        assert api.remote_port == 80
        assert api.created_at == t.created_at
