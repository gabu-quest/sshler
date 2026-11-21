"""The reason of every failure of the routes that are not files or programs on a box.

One table of rows (the request, the failure made to happen, the exact status, reason and
text): the PDF, diff notebook, snippet, pool config, progress, SPA, login, Claude session,
recovery, tunnel and artifact routes. Nothing runs: the box is a fake connection that
opens no SSH connection, the PDF renderer, the Claude session scan and the recovery
snapshot are fakes, and a port "already taken" is a loopback port the test binds itself
and releases at the end. Names, paths and texts are data.

Every row also checks that no fake SSH connection is left open.
"""

from __future__ import annotations

import base64
import errno
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from argon2 import PasswordHasher as RealArgon2Hasher
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler.api import artifacts as artifacts_api
from sshler.api import claude_sessions as claude_api
from sshler.api import pdf as pdf_api
from sshler.api import recovery as recovery_api
from sshler.api.dependencies import APIDependencies
from sshler.artifacts import ArtifactValidationError
from sshler.pdf import PDF_RENDERER
from sshler.settings import reset_settings
from sshler.ssh import SSHError
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

pytestmark = pytest.mark.posix_only("a loopback port taken on purpose, POSIX error texts")

TOKEN = "route-failure-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
BOX = "lb"
SESSION_UUID = "123e4567-e89b-42d3-a456-426614174000"
LOGIN_USER = "admin"
LOGIN_PASSWORD = "Corr3ct-Horse-Battery!"


class FakeListener:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def close(self) -> None:
        self.sock.close()


class FakeConnection:
    """A box connection. ``forward_local_port`` binds the real loopback port asked for,
    so a port another socket holds is refused by the system, as it would be."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.closed = False

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        return SimpleNamespace(exit_status=0, returncode=0, stdout="test\n", stderr="")

    async def forward_local_port(self, *, listen_host, listen_port, dest_host, dest_port):
        if self.world.forward_error is not None:
            raise self.world.forward_error
        sock = socket.socket()
        try:
            sock.bind((listen_host, listen_port))
        except OSError:
            sock.close()
            raise
        return FakeListener(sock)

    def close(self) -> None:
        self.closed = True


@dataclass
class World:
    """The app of a row, the test's temporary folder and what the fakes are told to do."""

    client: TestClient
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch
    opened: list[FakeConnection] = field(default_factory=list)
    connect_error: Exception | None = None
    forward_error: Exception | None = None
    held: list[socket.socket] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)

    def live(self) -> int:
        return sum(1 for conn in self.opened if not conn.closed)

    def make_artifact(self, name: str = "page.html", project: str = "Docs") -> str:
        """Register ``name`` under ``project``; returns the registration's id."""
        page = self.tmp_path / name
        page.write_text("<title>T</title>", encoding="utf-8")
        made = self.client.post(
            "/api/v1/artifacts",
            headers=HEADERS,
            json={"source_path": str(page), "project": project},
        )
        assert made.status_code == 200
        self.ids[name] = made.json()["id"]
        self.ids[f"project:{project}"] = made.json()["project_id"]
        return made.json()["id"]

    def make_project(self, name: str) -> str:
        made = self.client.post("/api/v1/artifact-projects", headers=HEADERS, json={"name": name})
        assert made.status_code == 200
        self.ids[f"project:{name}"] = made.json()["id"]
        return made.json()["id"]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {BOX}\n  HostName example.invalid\n  User tester\n")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setenv("SSHLER_COOKIE_SECURE", "false")
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    state.reset_state()
    state.initialize(config_dir)
    holder: dict[str, World] = {}

    async def fake_connect(self, box, application_config):
        current = holder["world"]
        if current.connect_error is not None:
            raise current.connect_error
        conn = FakeConnection(current)
        current.opened.append(conn)
        return conn

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    found = World(client=client, tmp_path=tmp_path, monkeypatch=monkeypatch)
    holder["world"] = found
    try:
        yield found
    finally:
        for sock in found.held:
            sock.close()
        client.close()
        state.reset_state()


@dataclass(frozen=True)
class Row:
    id: str
    method: str
    url: str | Callable[[World], str]
    status: int
    reason: str
    detail: str
    body: Any = None  # a JSON body, or a function of the world that gives one
    arrange: Callable[[World], None] | None = None


# --- arrangements ----------------------------------------------------------------------


def _renderer(world: World, available: bool, render: Callable | None = None) -> None:
    world.monkeypatch.setattr(PDF_RENDERER, "available", available)
    if render is not None:
        world.monkeypatch.setattr(PDF_RENDERER, "render", render)


def _pdf_not_installed(world: World) -> None:
    _renderer(world, False)


def _pdf_too_large(world: World) -> None:
    _renderer(world, True)
    world.monkeypatch.setattr(pdf_api, "_MAX_HTML_BYTES", 10)


def _pdf_stopped(world: World) -> None:
    async def render(html: str) -> bytes:
        raise RuntimeError("PDF renderer is not running")

    _renderer(world, True, render)


def _pdf_fails(world: World) -> None:
    async def render(html: str) -> bytes:
        raise ValueError("page crashed")

    _renderer(world, True, render)


def _corrupt_notebook(world: World) -> None:
    async def get_notebook(notebook_id: str):
        return SimpleNamespace(id=notebook_id, envelope_json="{not json", label="", cell_count=0)

    world.monkeypatch.setattr("sshler.state.get_diff_notebook_async", get_notebook)


def _spa_without_index(world: World) -> None:
    dist = world.tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    world.monkeypatch.setattr("sshler.spa.SPA_DIST_DIR", dist)
    # The route is mounted when the app is made, so this row makes its own app.
    world.client.close()
    world.client = TestClient(make_app(ServerSettings(csrf_token=TOKEN, serve_spa=True)))


def _login_app(world: World, enabled: bool) -> None:
    if enabled:
        world.monkeypatch.setattr(
            "sshler.auth.Argon2PasswordHasher",
            lambda **_kw: RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
        )
        reset_settings()
        settings = ServerSettings(csrf_token=TOKEN, basic_auth=(LOGIN_USER, LOGIN_PASSWORD))
    else:
        reset_settings()
        settings = ServerSettings(csrf_token=TOKEN)
    world.client.close()
    world.client = TestClient(make_app(settings))
    if enabled:
        sent = base64.b64encode(f"{LOGIN_USER}:{LOGIN_PASSWORD}".encode()).decode()
        world.client.headers["Authorization"] = f"Basic {sent}"


def _login_wrong_password(world: World) -> None:
    _login_app(world, True)


def _login_disabled(world: World) -> None:
    _login_app(world, False)


def _session_info(world: World, cwd: str) -> None:
    info = SimpleNamespace(cwd=cwd, repo_root=None, title="t")
    world.monkeypatch.setattr(claude_api, "get_claude_session", lambda session_id: info)


def _session_unknown(world: World) -> None:
    world.monkeypatch.setattr(claude_api, "get_claude_session", lambda session_id: None)


def _session_folder_gone(world: World) -> None:
    _session_info(world, str(world.tmp_path / "gone"))


def _lost_session(world: World, recreate: Callable | None) -> None:
    lost = [{"id": "s1", "session_name": "work", "windows": []}]
    world.monkeypatch.setattr(recovery_api, "get_recovery_sessions", lambda: lost)
    if recreate is not None:
        world.monkeypatch.setattr(recovery_api, "recreate_session", recreate)


def _recreate_raises(error: Exception) -> Callable[[World], None]:
    async def recreate(name: str, windows: list) -> bool:
        raise error

    return lambda world: _lost_session(world, recreate)


def _recreate_returns_false(world: World) -> None:
    async def recreate(name: str, windows: list) -> bool:
        return False

    _lost_session(world, recreate)


def _no_lost_sessions(world: World) -> None:
    _lost_session(world, None)
    world.monkeypatch.setattr(recovery_api, "get_recovery_sessions", lambda: [])


def _unreachable_box(world: World) -> None:
    world.connect_error = SSHError("lb: connection refused")


def _forward_times_out(world: World) -> None:
    world.forward_error = TimeoutError()


def _forward_refused(world: World) -> None:
    world.forward_error = OSError(errno.EACCES, "Permission denied")


def _port_taken(world: World) -> None:
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    world.held.append(taken)
    world.ids["port"] = str(taken.getsockname()[1])


def _tunnel_body(world: World) -> dict[str, Any]:
    return {
        "tunnel_type": "local",
        "local_host": "127.0.0.1",
        "local_port": int(world.ids["port"]),
        "remote_host": "127.0.0.1",
        "remote_port": 80,
    }


def _tunnel(**changes: Any) -> dict[str, Any]:
    return {
        "tunnel_type": "local",
        "local_host": "127.0.0.1",
        "local_port": 18080,
        "remote_host": "127.0.0.1",
        "remote_port": 80,
        **changes,
    }


def _two_projects(world: World) -> None:
    world.make_project("Alpha")
    world.make_project("Beta")


def _project_with_artifact(world: World) -> None:
    world.make_artifact()


def _artifact_in_other_project(world: World) -> None:
    world.make_artifact("first.html", "One")
    world.make_artifact("second.html", "Two")


def _discovery_refused(world: World) -> None:
    world.make_artifact()

    def discover(registration, refresh=False):
        raise ArtifactValidationError("Collection exceeds the 1000-page limit")

    world.monkeypatch.setattr(artifacts_api, "discover_pages", discover)


def _discovery_refused_before_create(world: World) -> None:
    def discover(registration, refresh=False):
        raise ArtifactValidationError("Collection exceeds the 1000-page limit")

    world.monkeypatch.setattr(artifacts_api, "discover_pages", discover)
    page = world.tmp_path / "page.html"
    page.write_text("<title>T</title>", encoding="utf-8")


def _taken_mount(world: World) -> None:
    first = world.tmp_path / "one.html"
    first.write_text("<title>1</title>", encoding="utf-8")
    second = world.tmp_path / "two.html"
    second.write_text("<title>2</title>", encoding="utf-8")
    made = world.client.post(
        "/api/v1/artifacts",
        headers=HEADERS,
        json={"source_path": str(first), "project": "Docs", "mount_path": "docs/one"},
    )
    assert made.status_code == 200


def _page_to_register(world: World) -> None:
    (world.tmp_path / "page.html").write_text("<title>T</title>", encoding="utf-8")


def _two_artifacts_one_project(world: World) -> None:
    world.make_artifact("one.html", "Docs")
    world.make_artifact("two.html", "Docs")


def _project(world: World, name: str) -> str:
    return world.ids[f"project:{name}"]


def _artifact_url(suffix: str = "") -> Callable[[World], str]:
    return lambda world: f"/api/v1/artifacts/{world.ids['page.html']}{suffix}"


def _create(world: World, **fields: Any) -> dict[str, Any]:
    return {"source_path": str(world.tmp_path / "page.html"), **fields}


ROWS: list[Row] = [
    # PDF
    Row(
        "pdf-renderer-not-installed",
        "POST",
        "/api/v1/pdf/render",
        501,
        "program_missing",
        "PDF export not available. Install with 'pip install sshler[pdf]' and run "
        "'playwright install chromium'.",
        body={"html": "<p>x</p>"},
        arrange=_pdf_not_installed,
    ),
    Row(
        "pdf-html-too-large",
        "POST",
        "/api/v1/pdf/render",
        413,
        "too_large",
        "HTML payload too large",
        body={"html": "<p>more than ten bytes</p>"},
        arrange=_pdf_too_large,
    ),
    Row(
        "pdf-renderer-stopped-mid-request",
        "POST",
        "/api/v1/pdf/render",
        501,
        "program_missing",
        "PDF renderer is not running",
        body={"html": "<p>x</p>"},
        arrange=_pdf_stopped,
    ),
    Row(
        "pdf-render-fails",
        "POST",
        "/api/v1/pdf/render",
        500,
        "failed",
        "PDF render failed: page crashed",
        body={"html": "<p>x</p>"},
        arrange=_pdf_fails,
    ),
    # Diff notebooks
    Row(
        "diff-id-of-the-wrong-shape",
        "GET",
        "/api/v1/diff/notebooks/bad.id.12",
        404,
        "not_found",
        "Diff notebook not found",
    ),
    Row(
        "diff-notebook-missing",
        "GET",
        "/api/v1/diff/notebooks/AAAAAAAA",
        404,
        "not_found",
        "Diff notebook not found",
    ),
    Row(
        "diff-stored-notebook-corrupt",
        "GET",
        "/api/v1/diff/notebooks/AAAAAAAA",
        500,
        "failed",
        "Stored notebook is corrupt",
        arrange=_corrupt_notebook,
    ),
    Row(
        "diff-envelope-over-the-limit",
        "POST",
        "/api/v1/diff/notebooks",
        413,
        "too_large",
        "Notebook envelope too large (>1000000 bytes)",
        body={"envelope": {"v": 1, "cells": [{"text": "a" * 1_000_100}]}},
    ),
    # Snippets
    Row(
        "snippet-empty-label",
        "POST",
        "/api/v1/snippets",
        400,
        "invalid",
        "Label cannot be empty",
        body={"box": BOX, "label": "  ", "command": "ls"},
    ),
    Row(
        "snippet-empty-command",
        "POST",
        "/api/v1/snippets",
        400,
        "invalid",
        "Command cannot be empty",
        body={"box": BOX, "label": "list", "command": " "},
    ),
    Row(
        "snippet-update-missing",
        "PUT",
        "/api/v1/snippets/nope",
        404,
        "not_found",
        "Snippet not found",
        body={"label": "x"},
    ),
    Row(
        "snippet-delete-missing",
        "DELETE",
        "/api/v1/snippets/nope",
        404,
        "not_found",
        "Snippet not found",
    ),
    # Pool config
    Row(
        "pool-negative-idle-timeout",
        "PUT",
        "/api/v1/pool/config",
        400,
        "invalid",
        "idle_timeout must be >= 0 or null",
        body={"idle_timeout": -1},
    ),
    Row(
        "pool-negative-max-lifetime",
        "PUT",
        "/api/v1/pool/config",
        400,
        "invalid",
        "max_lifetime must be >= 0 or null",
        body={"max_lifetime": -1},
    ),
    Row(
        "pool-no-connections-allowed",
        "PUT",
        "/api/v1/pool/config",
        400,
        "invalid",
        "max_connections_per_box must be >= 1",
        body={"max_connections_per_box": 0},
    ),
    # Progress bars
    Row(
        "progress-name-of-the-wrong-shape",
        "GET",
        "/api/v1/progress/bad%20name",
        400,
        "invalid",
        "Invalid name: must match ^[A-Za-z0-9._:-]{1,64}$",
    ),
    Row(
        "progress-bar-missing",
        "GET",
        "/api/v1/progress/none",
        404,
        "not_found",
        "Progress bar not found",
    ),
    Row(
        "progress-status-not-allowed",
        "POST",
        "/api/v1/progress/job",
        400,
        "invalid",
        "Invalid status: must be one of ['cancelled', 'done', 'failed', 'running']",
        body={"current": 1, "total": 2, "status": "weird"},
    ),
    # The SPA
    Row(
        "spa-index-missing",
        "GET",
        "/app/",
        404,
        "not_found",
        "Not found",
        arrange=_spa_without_index,
    ),
    # Login
    Row(
        "login-wrong-password",
        "POST",
        "/api/v1/auth/login",
        401,
        "auth",
        "Invalid username or password",
        body={"username": LOGIN_USER, "password": "wrong-password"},
        arrange=_login_wrong_password,
    ),
    Row(
        "login-when-auth-is-off",
        "POST",
        "/api/v1/auth/login",
        400,
        "invalid",
        "Authentication is not enabled",
        body={"username": "a", "password": "b"},
        arrange=_login_disabled,
    ),
    # Claude sessions
    Row(
        "claude-session-id-not-a-uuid",
        "POST",
        "/api/v1/claude/sessions/not-a-uuid/open",
        400,
        "invalid",
        "Invalid Claude session id",
    ),
    Row(
        "claude-template-without-placeholder",
        "POST",
        f"/api/v1/claude/sessions/{SESSION_UUID}/open",
        400,
        "invalid",
        "Resume command template must contain {id}",
        body={"command_template": "claude --resume"},
    ),
    Row(
        "claude-template-with-a-newline",
        "POST",
        f"/api/v1/claude/sessions/{SESSION_UUID}/open",
        400,
        "invalid",
        "Resume command template contains illegal characters",
        body={"command_template": 'run "{id}"\nnext'},
    ),
    Row(
        "claude-session-unknown",
        "POST",
        f"/api/v1/claude/sessions/{SESSION_UUID}/open",
        404,
        "not_found",
        "Claude session not found",
        arrange=_session_unknown,
    ),
    Row(
        "claude-session-folder-gone",
        "POST",
        f"/api/v1/claude/sessions/{SESSION_UUID}/open",
        404,
        "not_found",
        "Working directory no longer exists",
        arrange=_session_folder_gone,
    ),
    # Recovery
    Row(
        "recreate-session-not-listed",
        "POST",
        "/api/v1/recovery/s1/recreate",
        404,
        "not_found",
        "Session not in recovery list",
        arrange=_no_lost_sessions,
    ),
    Row(
        "recreate-box-unreachable",
        "POST",
        "/api/v1/recovery/s1/recreate",
        502,
        "unreachable",
        "lb: connection refused",
        arrange=_recreate_raises(SSHError("lb: connection refused")),
    ),
    Row(
        "recreate-times-out",
        "POST",
        "/api/v1/recovery/s1/recreate",
        504,
        "timeout",
        "tmux did not answer",
        arrange=_recreate_raises(TimeoutError("tmux did not answer")),
    ),
    Row(
        "recreate-returns-false",
        "POST",
        "/api/v1/recovery/s1/recreate",
        500,
        "failed",
        "Failed to recreate tmux session",
        arrange=_recreate_returns_false,
    ),
    # Tunnels
    Row(
        "tunnel-type-unknown",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        400,
        "invalid",
        "tunnel_type must be 'local' or 'remote'",
        body=_tunnel(tunnel_type="udp"),
    ),
    Row(
        "tunnel-local-port-zero",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        400,
        "invalid",
        "local_port must be 1-65535",
        body=_tunnel(local_port=0),
    ),
    Row(
        "tunnel-remote-port-too-high",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        400,
        "invalid",
        "remote_port must be 1-65535",
        body=_tunnel(remote_port=65536),
    ),
    Row(
        "tunnel-box-unreachable",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        502,
        "unreachable",
        "lb: connection refused",
        body=_tunnel(),
        arrange=_unreachable_box,
    ),
    Row(
        "tunnel-local-port-taken",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        500,
        "failed",
        "Address already in use",
        body=_tunnel_body,
        arrange=_port_taken,
    ),
    Row(
        "tunnel-forward-times-out",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        504,
        "timeout",
        "Timed out",
        body=_tunnel(),
        arrange=_forward_times_out,
    ),
    Row(
        "tunnel-forward-refused-by-the-system",
        "POST",
        f"/api/v1/boxes/{BOX}/tunnels",
        403,
        "denied",
        "Permission denied",
        body=_tunnel(),
        arrange=_forward_refused,
    ),
    Row(
        "tunnel-delete-unknown",
        "DELETE",
        f"/api/v1/boxes/{BOX}/tunnels/nothere",
        404,
        "not_found",
        "Tunnel not found",
    ),
    # Artifact projects
    Row(
        "project-name-refused-on-create",
        "POST",
        "/api/v1/artifact-projects",
        400,
        "invalid",
        "Project name must be 1-100 printable characters",
        body={"name": "   "},
    ),
    Row(
        "project-rename-to-a-name-another-has",
        "PATCH",
        lambda w: f"/api/v1/artifact-projects/{_project(w, 'Alpha')}",
        409,
        "exists",
        "A project with that name already exists",
        body={"name": "Beta"},
        arrange=_two_projects,
    ),
    Row(
        "project-rename-to-a-name-the-check-refuses",
        "PATCH",
        lambda w: f"/api/v1/artifact-projects/{_project(w, 'Alpha')}",
        400,
        "invalid",
        "Project name must be 1-100 printable characters",
        body={"name": "   "},
        arrange=_two_projects,
    ),
    Row(
        "project-rename-missing",
        "PATCH",
        "/api/v1/artifact-projects/nothere",
        404,
        "not_found",
        "Artifact project not found",
        body={"name": "Gamma"},
    ),
    Row(
        "project-delete-with-artifacts-without-cascade",
        "DELETE",
        lambda w: f"/api/v1/artifact-projects/{_project(w, 'Docs')}",
        400,
        "invalid",
        "Project still contains artifact registrations",
        arrange=_project_with_artifact,
    ),
    # Artifact list
    Row(
        "artifacts-list-mode-unknown",
        "GET",
        "/api/v1/artifacts?mode=zip",
        400,
        "invalid",
        "Invalid artifact mode",
    ),
    Row(
        "artifacts-list-group-refused",
        "GET",
        "/api/v1/artifacts?group=..%2Fx",
        400,
        "invalid",
        "Invalid artifact group path",
    ),
    # Artifact create
    Row(
        "artifact-create-project-id-and-name",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "Use project_id or project, not both",
        body=lambda w: _create(w, project_id="p1", project="Docs"),
        arrange=_page_to_register,
    ),
    Row(
        "artifact-create-project-unknown",
        "POST",
        "/api/v1/artifacts",
        404,
        "not_found",
        "Artifact project not found",
        body=lambda w: _create(w, project_id="nothere"),
        arrange=_page_to_register,
    ),
    Row(
        "artifact-create-project-name-refused",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "Project name must be 1-100 printable characters",
        body=lambda w: _create(w, project="   "),
        arrange=_page_to_register,
    ),
    Row(
        "artifact-create-source-missing",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "[Errno 2] No such file or directory: '{tmp}/absent.html'",
        body=lambda w: {"source_path": str(w.tmp_path / "absent.html")},
    ),
    Row(
        "artifact-create-source-not-html",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "File artifacts require an HTML file",
        body=lambda w: {"source_path": str(w.tmp_path / "config" / "boxes.yaml"), "mode": "file"},
    ),
    Row(
        "artifact-create-group-refused",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "Invalid artifact group path",
        body=lambda w: _create(w, group_path="../up"),
        arrange=_page_to_register,
    ),
    Row(
        "artifact-create-registered-with-other-metadata",
        "POST",
        "/api/v1/artifacts",
        409,
        "exists",
        "Artifact is already registered with different metadata; update it instead",
        body=lambda w: _create(w, project="Other"),
        arrange=_project_with_artifact,
    ),
    Row(
        "artifact-create-mount-path-taken",
        "POST",
        "/api/v1/artifacts",
        409,
        "exists",
        "That artifact mount path is already registered",
        body=lambda w: {
            "source_path": str(w.tmp_path / "two.html"),
            "project": "Docs",
            "mount_path": "docs/one",
        },
        arrange=_taken_mount,
    ),
    Row(
        "artifact-create-discovery-refused",
        "POST",
        "/api/v1/artifacts",
        400,
        "invalid",
        "Collection exceeds the 1000-page limit",
        body=lambda w: _create(w, project="Docs"),
        arrange=_discovery_refused_before_create,
    ),
    # Artifact read, update, pages, rescan
    Row(
        "artifact-get-missing",
        "GET",
        "/api/v1/artifacts/nothere",
        404,
        "not_found",
        "Artifact not found",
    ),
    Row(
        "artifact-update-missing",
        "PATCH",
        "/api/v1/artifacts/nothere",
        404,
        "not_found",
        "Artifact not found",
        body={"title": "x"},
    ),
    Row(
        "artifact-update-project-unknown",
        "PATCH",
        _artifact_url(),
        404,
        "not_found",
        "Artifact project not found",
        body={"project_id": "nothere"},
        arrange=_project_with_artifact,
    ),
    Row(
        "artifact-update-group-refused",
        "PATCH",
        _artifact_url(),
        400,
        "invalid",
        "Invalid artifact group path",
        body={"group_path": "../up"},
        arrange=_project_with_artifact,
    ),
    Row(
        "artifact-update-source-already-registered",
        "PATCH",
        lambda w: f"/api/v1/artifacts/{w.ids['two.html']}",
        409,
        "exists",
        "Artifact source is already registered",
        body=lambda w: {"source_path": str(w.tmp_path / "one.html")},
        arrange=_two_artifacts_one_project,
    ),
    Row(
        "artifact-update-alias-taken",
        "PATCH",
        lambda w: f"/api/v1/artifacts/{w.ids['two.html']}",
        409,
        "exists",
        "An artifact with that alias already exists in the project",
        body={"slug": "one"},
        arrange=_two_artifacts_one_project,
    ),
    Row(
        "artifact-update-discovery-refused",
        "PATCH",
        _artifact_url(),
        400,
        "invalid",
        "Collection exceeds the 1000-page limit",
        body={"title": "x"},
        arrange=_discovery_refused,
    ),
    Row(
        "artifact-pages-missing",
        "GET",
        "/api/v1/artifacts/nothere/pages",
        404,
        "not_found",
        "Artifact not found",
    ),
    Row(
        "artifact-pages-discovery-refused",
        "GET",
        _artifact_url("/pages?refresh=true"),
        400,
        "invalid",
        "Collection exceeds the 1000-page limit",
        arrange=_discovery_refused,
    ),
    Row(
        "artifact-rescan-missing",
        "POST",
        "/api/v1/artifacts/nothere/rescan",
        404,
        "not_found",
        "Artifact not found",
    ),
    Row(
        "artifact-rescan-discovery-refused",
        "POST",
        _artifact_url("/rescan"),
        400,
        "invalid",
        "Collection exceeds the 1000-page limit",
        arrange=_discovery_refused,
    ),
]


def _ids() -> list[str]:
    return [row.id for row in ROWS]


@pytest.mark.parametrize("row", ROWS, ids=_ids())
def test_failure_of_a_route_has_the_reason_of_its_row(world: World, row: Row) -> None:
    if row.arrange is not None:
        row.arrange(world)
    url = row.url(world) if callable(row.url) else row.url
    body = row.body(world) if callable(row.body) else row.body
    response = world.client.request(row.method, url, headers=HEADERS, json=body)

    expected_detail = row.detail.replace("{tmp}", str(world.tmp_path))
    assert (response.status_code, response.json()) == (
        row.status,
        {"detail": expected_detail, "reason": row.reason},
    )
    assert world.live() == 0


def test_a_failure_text_with_the_clients_quote_and_newline_comes_back_as_text(
    world: World,
) -> None:
    source = world.tmp_path / 'it\'s "quoted"\nand split.html'
    response = world.client.post(
        "/api/v1/artifacts", headers=HEADERS, json={"source_path": str(source)}
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": f"[Errno 2] No such file or directory: {str(source)!r}",
        "reason": "invalid",
    }


def test_the_batch_recreate_stays_a_200_and_each_failed_item_carries_its_words(
    world: World,
) -> None:
    lost = [
        {"id": "unreachable", "session_name": "a", "windows": []},
        {"id": "refused", "session_name": "b", "windows": []},
    ]
    world.monkeypatch.setattr(recovery_api, "get_recovery_sessions", lambda: lost)

    async def recreate(name: str, windows: list) -> bool:
        if name == "a":
            raise SSHError("lb: connection refused")
        return False

    world.monkeypatch.setattr(recovery_api, "recreate_session", recreate)
    response = world.client.post(
        "/api/v1/recovery/recreate-batch",
        headers=HEADERS,
        json={"session_ids": ["unreachable", "refused", "absent"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "results": {
            "unreachable": "lb: connection refused",
            "refused": "tmux creation failed",
            "absent": "not found",
        }
    }


def test_a_failed_tunnel_leaves_no_tunnel_listed(world: World) -> None:
    world.forward_error = TimeoutError()
    world.client.post(f"/api/v1/boxes/{BOX}/tunnels", headers=HEADERS, json=_tunnel())
    listed = world.client.get(f"/api/v1/boxes/{BOX}/tunnels", headers=HEADERS)

    assert listed.json() == []
    assert len(world.opened) == 1
    assert world.live() == 0
