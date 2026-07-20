"""CLI tests for `sshler progress push/list/delete`.

HTTP is intercepted via ``httpx.MockTransport`` so no live server is needed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest import mock

import httpx
import pytest

from sshler import cli


def _ns(**kw) -> argparse.Namespace:
    base = dict(
        url=None,
        token=None,
        json_output=False,
        color=None,
        label=None,
        status="running",
        meta=None,
        meta_json=None,
        merge=False,
        clear_meta=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _patch_httpx_client(monkeypatch, handler):
    """Replace httpx.Client with one that routes through MockTransport(handler)."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("httpx.Client", factory)


# ---------------------------------------------------------------------------
# Token / URL resolution
# ---------------------------------------------------------------------------


def test_resolve_token_flag_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "env-token")
    (tmp_path / "runtime-token").write_text("cache-token", encoding="utf-8")
    args = _ns(token="flag-token")
    assert cli._resolve_progress_token(args) == "flag-token"


def test_resolve_token_env_beats_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "env-token")
    (tmp_path / "runtime-token").write_text("cache-token", encoding="utf-8")
    assert cli._resolve_progress_token(_ns()) == "env-token"


def test_resolve_token_falls_back_to_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("SSHLER_TOKEN", raising=False)
    (tmp_path / "runtime-token").write_text("cache-token", encoding="utf-8")
    assert cli._resolve_progress_token(_ns()) == "cache-token"


def test_resolve_token_exits_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("SSHLER_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli._resolve_progress_token(_ns())
    assert exc.value.code == 2


def test_resolve_url_default(monkeypatch):
    monkeypatch.delenv("SSHLER_PROGRESS_URL", raising=False)
    assert cli._resolve_progress_url(_ns()) == "http://127.0.0.1:8822"


def test_resolve_url_env_override(monkeypatch):
    monkeypatch.setenv("SSHLER_PROGRESS_URL", "http://10.0.0.1:9000/")
    assert cli._resolve_progress_url(_ns()) == "http://10.0.0.1:9000"


def test_resolve_url_flag_beats_env(monkeypatch):
    monkeypatch.setenv("SSHLER_PROGRESS_URL", "http://10.0.0.1:9000")
    args = _ns(url="http://other:1111")
    assert cli._resolve_progress_url(args) == "http://other:1111"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_push_rejects_invalid_name(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    rc = cli.progress_push(_ns(name="bad name", current=1.0, total=2.0))
    assert rc == 2
    assert "invalid name" in capsys.readouterr().err


def test_push_rejects_invalid_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    rc = cli.progress_push(
        _ns(name="ok", current=1.0, total=2.0, status="garbage")
    )
    assert rc == 2
    assert "invalid status" in capsys.readouterr().err


def test_delete_rejects_invalid_name(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    rc = cli.progress_delete(_ns(name="bad name"))
    assert rc == 2
    assert "invalid name" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HTTP behavior — push
# ---------------------------------------------------------------------------


def test_push_posts_to_correct_endpoint(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "secret-token")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["method"] = req.method
        seen["token"] = req.headers.get("x-sshler-token")
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "name": "build",
                "current": 3.0,
                "total": 10.0,
                "color": "blue",
                "label": "lbl",
                "status": "running",
                "created_at": 1.0,
                "updated_at": 2.0,
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(name="build", current=3.0, total=10.0, color="blue", label="lbl")
    )
    assert rc == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:8822/api/v1/progress/build"
    assert seen["token"] == "secret-token"
    assert seen["body"] == {
        "current": 3.0,
        "total": 10.0,
        "status": "running",
        "color": "blue",
        "label": "lbl",
    }
    out = capsys.readouterr().out
    assert "build" in out
    assert "3/10" in out
    assert "(30%)" in out


def test_push_json_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "x",
                "current": 1.0,
                "total": 2.0,
                "color": None,
                "label": None,
                "status": "running",
                "created_at": 1.0,
                "updated_at": 2.0,
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(name="x", current=1.0, total=2.0, json_output=True)
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["name"] == "x"
    assert parsed["current"] == 1.0


def _ok_handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "name": "build",
            "current": 1.0,
            "total": 2.0,
            "color": None,
            "label": None,
            "status": "running",
            "created_at": 1.0,
            "updated_at": 2.0,
            "metadata": {},
            "metadata_error": None,
        },
    )


def test_push_meta_pairs_build_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _ok_handler(req)

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(name="build", current=1.0, total=2.0, meta=["stage=link", "warnings=3"])
    )
    assert rc == 0
    assert seen["body"]["metadata"] == {"stage": "link", "warnings": "3"}
    assert "merge" not in seen["body"]


def test_push_meta_json_with_merge_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _ok_handler(req)

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(
            name="build",
            current=1.0,
            total=2.0,
            meta_json='{"a": 1, "b": "two"}',
            merge=True,
        )
    )
    assert rc == 0
    assert seen["body"]["metadata"] == {"a": 1, "b": "two"}
    assert seen["body"]["merge"] is True


def test_push_invalid_meta_json_warns_but_still_pushes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _ok_handler(req)

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(name="build", current=1.0, total=2.0, meta_json="{not valid")
    )
    assert rc == 0  # the push still went through
    assert "metadata" not in seen["body"]  # bad metadata dropped, bar advances
    assert "invalid --meta-json" in capsys.readouterr().err


def test_push_clear_meta_sends_empty_object(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return _ok_handler(req)

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(
        _ns(name="build", current=1.0, total=2.0, meta=["x=1"], clear_meta=True)
    )
    assert rc == 0
    assert seen["body"]["metadata"] == {}  # clear wins over --meta


def test_push_floors_displayed_percent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "build",
                "current": 3299.0,
                "total": 3300.0,
                "color": None,
                "label": None,
                "status": "running",
                "created_at": 1.0,
                "updated_at": 2.0,
                "metadata": {},
                "metadata_error": None,
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(_ns(name="build", current=3299.0, total=3300.0))
    assert rc == 0
    out = capsys.readouterr().out
    assert "(99%)" in out  # floored, not 100%
    assert "100%" not in out


def test_push_returns_1_on_http_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_push(_ns(name="x", current=1.0, total=2.0))
    assert rc == 1
    assert "push failed: HTTP 403" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HTTP behavior — list
# ---------------------------------------------------------------------------


def test_list_pretty_renders_bars(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "bars": [
                    {
                        "name": "build-ci",
                        "current": 42.0,
                        "total": 100.0,
                        "color": "blue",
                        "label": "webpack",
                        "status": "running",
                        "created_at": 1.0,
                        "updated_at": 999999999999.0,
                    }
                ]
            },
        )

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_list(_ns())
    assert rc == 0
    out = capsys.readouterr().out
    assert "NAME" in out
    assert "build-ci" in out
    assert "42/100" in out
    assert "webpack" in out


def test_render_table_floors_percent():
    table = cli._render_progress_table(
        [{"name": "b", "current": 3299, "total": 3300, "status": "running", "updated_at": 0}]
    )
    assert "(99%)" in table
    assert "100%" not in table


def test_list_empty_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": []})

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_list(_ns())
    assert rc == 0
    assert "no progress bars" in capsys.readouterr().out


def test_list_json_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": []})

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_list(_ns(json_output=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"bars": []}


# ---------------------------------------------------------------------------
# HTTP behavior — delete
# ---------------------------------------------------------------------------


def test_delete_calls_delete_endpoint(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True, "removed": True})

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_delete(_ns(name="x"))
    assert rc == 0
    assert seen["method"] == "DELETE"
    assert seen["url"] == "http://127.0.0.1:8822/api/v1/progress/x"
    out = capsys.readouterr().out
    assert "deleted 'x'" in out


def test_delete_noop_when_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("SSHLER_TOKEN", "t")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "removed": False})

    _patch_httpx_client(monkeypatch, handler)
    rc = cli.progress_delete(_ns(name="ghost"))
    assert rc == 0
    assert "did not exist" in capsys.readouterr().out
