"""Tests for grep (file content search) API."""

import os
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "grep-test-token"


def build_client(config_dir: Path) -> TestClient:
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


class TestGrepSearch:
    def test_grep_finds_content(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "greet.py").write_text("def greet():\n    print('Hello World')\n")
        (workdir / "other.txt").write_text("nothing here\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "Hello World", "directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["box"] == "local"
            assert data["pattern"] == "Hello World"
            assert data["directory"] == str(workdir)
            assert len(data["matches"]) == 1
            match = data["matches"][0]
            assert match["file"].endswith("greet.py")
            assert match["line_number"] == 2
            assert "Hello World" in match["line"]
        finally:
            client.close()

    def test_grep_case_insensitive(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "test.txt").write_text("FOO\nfoo\nFoO\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "foo", "directory": str(workdir), "case_sensitive": "false"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["matches"]) == 3
        finally:
            client.close()

    def test_grep_case_sensitive(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "test.txt").write_text("FOO\nfoo\nFoO\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "foo", "directory": str(workdir), "case_sensitive": "true"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["matches"]) == 1
            assert data["matches"][0]["line"].strip() == "foo"
        finally:
            client.close()

    def test_grep_no_results(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "test.txt").write_text("nothing relevant\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "xyznonexistent", "directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["matches"]) == 0
            assert data["truncated"] is False
        finally:
            client.close()

    def test_grep_multiple_files(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("target line 1\n")
        (workdir / "b.txt").write_text("target line 2\n")
        (workdir / "c.txt").write_text("no match\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "target", "directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["matches"]) == 2
            files = {m["file"] for m in data["matches"]}
            assert len(files) == 2
        finally:
            client.close()

    def test_grep_pattern_too_long(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "x" * 501, "directory": "/tmp"},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "too long" in resp.json()["detail"].lower()
        finally:
            client.close()

    def test_grep_null_bytes_rejected(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "test\x00evil", "directory": "/tmp"},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "null" in resp.json()["detail"].lower()
        finally:
            client.close()


class TestGrepInjectionPrevention:
    """Verify that shell injection attempts are safely handled."""

    def test_semicolon_injection(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "safe.txt").write_text("safe content\n")

        client = build_client(config_dir)
        try:
            # Pattern with injection attempt — grep should treat as literal
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "; rm -rf /", "directory": str(workdir)},
                headers=auth_headers(),
            )
            # Should succeed (no injection) with 0 matches
            assert resp.status_code == 200
            assert len(resp.json()["matches"]) == 0
            # Verify our test files are still intact
            assert (workdir / "safe.txt").exists()
        finally:
            client.close()

    def test_command_substitution_injection(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "safe.txt").write_text("safe\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "$(cat /etc/passwd)", "directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            # Files untouched
            assert (workdir / "safe.txt").exists()
        finally:
            client.close()

    def test_backtick_injection(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "safe.txt").write_text("safe\n")

        client = build_client(config_dir)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "`whoami`", "directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
        finally:
            client.close()
