"""Tests for tunnel API endpoints."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sshler.api.tunnels import _active_tunnels, TunnelInfo


@pytest.fixture(autouse=True)
def _clear_tunnels():
    """Clear active tunnels before and after each test."""
    _active_tunnels.clear()
    yield
    _active_tunnels.clear()


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

    def test_active_tunnels_registry(self):
        t = TunnelInfo(
            id="t1",
            box="box1",
            tunnel_type="local",
            local_host="127.0.0.1",
            local_port=3000,
            remote_host="127.0.0.1",
            remote_port=3000,
        )
        _active_tunnels["t1"] = t
        assert "t1" in _active_tunnels
        assert _active_tunnels["t1"].box == "box1"

        del _active_tunnels["t1"]
        assert "t1" not in _active_tunnels

    def test_filter_tunnels_by_box(self):
        _active_tunnels["t1"] = TunnelInfo(
            id="t1", box="box1", tunnel_type="local",
            local_host="127.0.0.1", local_port=3000,
            remote_host="127.0.0.1", remote_port=3000,
        )
        _active_tunnels["t2"] = TunnelInfo(
            id="t2", box="box2", tunnel_type="remote",
            local_host="127.0.0.1", local_port=5432,
            remote_host="127.0.0.1", remote_port=5432,
        )
        _active_tunnels["t3"] = TunnelInfo(
            id="t3", box="box1", tunnel_type="local",
            local_host="127.0.0.1", local_port=8080,
            remote_host="127.0.0.1", remote_port=80,
        )

        box1_tunnels = [t for t in _active_tunnels.values() if t.box == "box1"]
        assert len(box1_tunnels) == 2
        assert {t.id for t in box1_tunnels} == {"t1", "t3"}

    def test_tunnel_listener_close(self):
        mock_listener = MagicMock()
        t = TunnelInfo(
            id="t1", box="box1", tunnel_type="local",
            local_host="127.0.0.1", local_port=3000,
            remote_host="127.0.0.1", remote_port=3000,
            listener=mock_listener,
        )
        _active_tunnels["t1"] = t

        # Simulate closing
        if t.listener and hasattr(t.listener, "close"):
            t.listener.close()

        mock_listener.close.assert_called_once()


class TestTunnelApiModels:
    def test_tunnel_to_api_conversion(self):
        from sshler.api.tunnels import _tunnel_to_api

        t = TunnelInfo(
            id="t1", box="mybox", tunnel_type="local",
            local_host="0.0.0.0", local_port=8080,
            remote_host="10.0.0.1", remote_port=80,
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
