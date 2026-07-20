"""Verify ``sshler serve`` writes the active CSRF token to a local cache file
so `sshler progress` commands can auto-discover it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from sshler import cli


def test_write_runtime_token_persists_value(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    cli._write_runtime_token("the-active-token")
    path = tmp_path / "runtime-token"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "the-active-token"


def test_write_runtime_token_sets_0600_on_posix(monkeypatch, tmp_path):
    if os.name != "posix":
        return  # chmod is a best-effort no-op elsewhere
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    cli._write_runtime_token("tok")
    mode = stat.S_IMODE((tmp_path / "runtime-token").stat().st_mode)
    assert mode == 0o600


def test_write_runtime_token_overwrites_previous(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    cli._write_runtime_token("first")
    cli._write_runtime_token("second")
    assert (tmp_path / "runtime-token").read_text(encoding="utf-8") == "second"


def test_read_runtime_token_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    assert cli._read_runtime_token() is None


def test_read_runtime_token_returns_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "runtime-token").write_text("cached", encoding="utf-8")
    assert cli._read_runtime_token() == "cached"


def test_read_runtime_token_strips_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "runtime-token").write_text("trimmed\n", encoding="utf-8")
    assert cli._read_runtime_token() == "trimmed"
