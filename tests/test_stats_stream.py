"""Tests for SSH connection fail cache and SSE auth."""

import os
import time
from pathlib import Path

import yaml

from sshler.webapp import ServerSettings, make_app
from sshler.api.dependencies import _CONNECT_FAIL_CACHE, _CONNECT_FAIL_COOLDOWN, APIDependencies


TEST_TOKEN = "test-sse-token"


def build_client(tmp_path: Path):
    from fastapi.testclient import TestClient

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


# --- SSE Auth Tests (non-streaming, just check 403 vs 200) ---


def test_stats_stream_requires_auth(tmp_path):
    """SSE endpoint rejects requests without token."""
    client = build_client(tmp_path)
    # Non-streaming GET — FastAPI returns 403 before opening the stream
    resp = client.get("/api/v1/boxes/stats/stream")
    assert resp.status_code == 403


def test_stats_stream_wrong_token_rejected(tmp_path):
    """SSE endpoint rejects wrong token."""
    client = build_client(tmp_path)
    resp = client.get("/api/v1/boxes/stats/stream?token=wrong-token")
    assert resp.status_code == 403


# --- Connection Fail Cache Tests ---


def test_fail_cache_stores_failure_timestamp():
    """Recording a failure stores the current timestamp."""
    _CONNECT_FAIL_CACHE.clear()
    now = time.time()
    _CONNECT_FAIL_CACHE["test-box"] = now

    assert _CONNECT_FAIL_CACHE["test-box"] == now
    assert (time.time() - _CONNECT_FAIL_CACHE["test-box"]) < 1.0

    _CONNECT_FAIL_CACHE.clear()


def test_fail_cache_entry_within_cooldown():
    """A recent failure is within the cooldown period."""
    _CONNECT_FAIL_CACHE.clear()
    _CONNECT_FAIL_CACHE["test-box"] = time.time()

    elapsed = time.time() - _CONNECT_FAIL_CACHE["test-box"]
    assert elapsed < _CONNECT_FAIL_COOLDOWN

    _CONNECT_FAIL_CACHE.clear()


def test_fail_cache_entry_expired():
    """An old failure is beyond the cooldown period."""
    _CONNECT_FAIL_CACHE.clear()
    _CONNECT_FAIL_CACHE["test-box"] = time.time() - 120

    elapsed = time.time() - _CONNECT_FAIL_CACHE["test-box"]
    assert elapsed >= _CONNECT_FAIL_COOLDOWN

    _CONNECT_FAIL_CACHE.clear()


def test_fail_cache_clear_removes_entry():
    """clear_connect_failure removes a specific box from the cache."""
    _CONNECT_FAIL_CACHE.clear()
    _CONNECT_FAIL_CACHE["box-a"] = time.time()
    _CONNECT_FAIL_CACHE["box-b"] = time.time()

    APIDependencies.clear_connect_failure("box-a")

    assert "box-a" not in _CONNECT_FAIL_CACHE
    assert "box-b" in _CONNECT_FAIL_CACHE

    _CONNECT_FAIL_CACHE.clear()


def test_fail_cache_clear_nonexistent_is_noop():
    """Clearing a box not in the cache doesn't raise."""
    _CONNECT_FAIL_CACHE.clear()
    APIDependencies.clear_connect_failure("nonexistent")
    assert len(_CONNECT_FAIL_CACHE) == 0


def test_fail_cache_cooldown_value():
    """Cooldown is 60 seconds."""
    assert _CONNECT_FAIL_COOLDOWN == 60
