"""CLI tests for local HTML artifact registration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from sshler import cli


def _args(**values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "url": None,
        "token": "token",
        "json_output": False,
        "project": None,
        "group": None,
        "group_path": None,
        "mode": "auto",
        "discover": False,
        "entrypoint": None,
        "title": None,
        "slug": None,
        "mount_path": None,
        "query": None,
        "exists": None,
        "path": None,
        "id": "artifact12345678",
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _patch_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("httpx.Client", factory)


def _artifact_payload(source: str) -> dict:
    return {
        "id": "artifact12345678",
        "project_id": "project123456",
        "project_name": "Sample Project",
        "project_slug": "sample-project",
        "group_path": "design/layout",
        "source_path": source,
        "mode": "file",
        "entrypoint": None,
        "title": None,
        "slug": "sample",
        "alias_path": "/r/sample-project/sample/",
        "mount_path": None,
        "exists": True,
        "created_at": 1.0,
        "updated_at": 1.0,
        "created": True,
    }


def test_artifact_add_posts_canonical_source_and_prints_json(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    page = tmp_path / "sample.html"
    page.write_text("<title>Sample</title>", encoding="utf-8")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_artifact_payload(str(page.resolve())))

    _patch_client(monkeypatch, handler)
    result = cli.artifact_add(
        _args(
            path=str(page),
            project="Sample Project",
            group="design/layout",
            json_output=True,
        )
    )

    assert result == 0
    assert seen == {
        "method": "POST",
        "url": "http://127.0.0.1:8822/api/v1/artifacts",
        "body": {
            "source_path": str(page.resolve()),
            "mode": "auto",
            "group_path": "design/layout",
            "project": "Sample Project",
        },
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["catalog_url"].endswith("/app/artifacts/artifact12345678")


def test_artifact_add_discover_overrides_auto_mode(monkeypatch, tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "tool.html").write_text("<title>Tool</title>", encoding="utf-8")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        payload = _artifact_payload(str(site.resolve()))
        payload["mode"] = "collection"
        return httpx.Response(200, json=payload)

    _patch_client(monkeypatch, handler)
    result = cli.artifact_add(_args(path=str(site), discover=True))

    assert result == 0
    assert seen["mode"] == "collection"


def test_artifact_remove_describes_non_destructive_behavior(monkeypatch, capsys) -> None:
    _patch_client(
        monkeypatch,
        lambda request: httpx.Response(200, json={"ok": True, "removed": True}),
    )

    result = cli.artifact_remove(_args())

    assert result == 0
    output = capsys.readouterr().out
    assert "unregistered" in output
    assert "files on disk were not changed" in output


def test_artifact_find_sends_catalog_filters(monkeypatch, capsys) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"artifacts": []})

    _patch_client(monkeypatch, handler)
    result = cli.artifact_find(
        _args(
            query="quarterly summary",
            project="demo-tools",
            group="published",
            mode="collection",
            exists=True,
        )
    )

    assert result == 0
    assert seen["url"].endswith(
        "/api/v1/artifacts?project=demo-tools&group=published"
        "&mode=collection&q=quarterly+summary&exists=true"
    )
    assert capsys.readouterr().out.strip() == "(no artifacts)"


def test_general_url_env_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("SSHLER_URL", "http://localhost:9001/")
    monkeypatch.setenv("SSHLER_PROGRESS_URL", "http://localhost:9002/")

    assert cli._resolve_progress_url(_args()) == "http://localhost:9001"
