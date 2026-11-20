"""Every failed request answers ``{"detail": <text>, "reason": <word>}`` with the status of
its reason: a failure raised by reason, an ``HTTPException`` raised without one, FastAPI's
own check of a request's shape, an exception nobody answered, and the checks that answer
before any route runs. Files are made with ``pathlib`` in the test's temporary folder."""

from __future__ import annotations

import errno
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
import yaml
from failure_check import failure_body_problem
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from sshler.api import failure
from sshler.api.failure import Failure, failure_entry, failure_of
from sshler.api.rate_limiting import create_rate_limit_dependency
from sshler.rate_limit import RateLimiter
from sshler.validation import ValidationError
from sshler.webapp import ServerSettings, make_app

TOKEN = "failure-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}


def _app_raising(exc: BaseException) -> TestClient:
    application = FastAPI()
    failure.install(application)

    @application.get("/x")
    async def raise_it() -> None:
        raise exc

    return TestClient(application, raise_server_exceptions=False)


# --- an HTTPException raised without a reason keeps its status and text ---------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, "invalid"),
        (401, "auth"),
        (403, "token"),
        (404, "not_found"),
        (409, "exists"),
        (413, "too_large"),
        (418, "invalid"),
        (422, "invalid"),
        (429, "rate_limited"),
        (500, "failed"),
        (501, "program_missing"),
        (502, "unreachable"),
        (503, "failed"),
        (504, "timeout"),
    ],
)
def test_an_http_exception_without_a_reason_gets_the_reason_of_its_status(status, reason):
    client = _app_raising(HTTPException(status_code=status, detail="said so", headers={"X-A": "1"}))
    resp = client.get("/x")
    assert (resp.status_code, resp.json(), resp.headers["X-A"]) == (
        status,
        {"detail": "said so", "reason": reason},
        "1",
    )


def test_a_failure_raised_by_reason_answers_its_status_and_headers():
    client = _app_raising(Failure("rate_limited", "slow down", headers={"Retry-After": "7"}))
    resp = client.get("/x")
    assert (resp.status_code, resp.json(), resp.headers["Retry-After"]) == (
        429,
        {"detail": "slow down", "reason": "rate_limited"},
        "7",
    )


def test_a_failure_with_a_word_off_the_list_is_refused_when_raised():
    with pytest.raises(ValueError, match="not a reason of a failed request: 'oops'"):
        Failure("oops", "x")


def test_an_exception_nobody_answered_is_internal_logged_and_valid_json(caplog):
    """A quote, a newline and a lone surrogate (a file name that is not UTF-8) in the
    text still answer valid JSON."""
    client = _app_raising(RuntimeError('it\'s "odd"\nname \udcff'))
    with caplog.at_level(logging.ERROR, logger="sshler.api.failure"):
        resp = client.get("/x")
    assert (resp.status_code, json.loads(resp.content.decode("utf-8"))) == (
        500,
        {"detail": 'RuntimeError: it\'s "odd"\nname ?', "reason": "internal"},
    )
    assert [r.exc_info[0] for r in caplog.records if r.exc_info] == [RuntimeError]


# --- the one function: by type and errno, never by the words --------------------------


@pytest.mark.parametrize(
    ("exc", "reason", "detail"),
    [
        (FileNotFoundError(errno.ENOENT, "No such file or directory", "/a"), "not_found", None),
        (NotADirectoryError(errno.ENOTDIR, "Not a directory", "/a/b"), "not_found", None),
        (PermissionError(errno.EACCES, "Permission denied", "/a"), "denied", None),
        (PermissionError(errno.EPERM, "Operation not permitted", "/a"), "denied", None),
        (IsADirectoryError(errno.EISDIR, "Is a directory", "/a"), "invalid", None),
        (OSError(errno.ENOSPC, "No space left on device"), "failed", "No space left on device"),
        (OSError(errno.EROFS, "Read-only file system"), "failed", "Read-only file system"),
        # The words of a refusal are not read: a "Permission denied" text with no
        # errno is no refusal of access.
        (OSError("Permission denied"), "failed", "Permission denied"),
        (asyncssh.SFTPNoSuchFile("No such file"), "not_found", "No such file"),
        (asyncssh.SFTPNoSuchPath("No such path"), "not_found", "No such path"),
        (asyncssh.SFTPPermissionDenied("Permission denied"), "denied", "Permission denied"),
        (asyncssh.SFTPFileIsADirectory("Is a directory"), "invalid", "Is a directory"),
        (asyncssh.SFTPFailure("Failure"), "failed", "Failure"),
        (asyncssh.SFTPConnectionLost("Connection lost"), "unreachable", "Connection lost"),
        (asyncssh.ConnectionLost("Connection lost"), "unreachable", "Connection lost"),
        (ConnectionError("rb: unreachable (cached, retry in 42s)"), "unreachable", None),
        (ValidationError("Path cannot contain null bytes"), "invalid", None),
        (TimeoutError(), "timeout", "Timed out"),
        (ValueError("not ours"), "internal", "ValueError: not ours"),
    ],
    ids=repr,
)
def test_the_one_function_names_each_failure_by_its_type(exc, reason, detail):
    found = failure_of(exc)
    assert (found.reason, found.status_code) == (reason, failure.STATUS_OF_REASON[reason])
    if detail is not None:
        assert found.detail == detail


def test_the_system_words_name_the_path_as_sent_and_never_another():
    exc = PermissionError(errno.EACCES, "Permission denied", "/srv/real/a.txt")
    sent = {"/srv/real/a.txt": "~/a.txt"}
    assert failure_of(exc, as_sent=sent.get).detail == "Permission denied: ~/a.txt"
    assert failure_of(exc, as_sent={}.get).detail == "Permission denied"


def test_a_batch_item_carries_what_a_single_request_answers():
    exc = asyncssh.SFTPNoSuchFile("No such file")
    single = failure_of(exc, missing="File not found")
    assert failure_entry("/a", exc, missing="File not found") == {
        "path": "/a",
        "error": single.detail,
        "reason": single.reason,
    }
    assert single.reason == "not_found"


# --- through the app ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    yield test_client
    test_client.close()


def test_a_request_of_the_wrong_shape_is_422_with_one_text(client):
    resp = client.post("/api/v1/boxes/local/batch/delete", json={"paths": 5}, headers=HEADERS)
    assert (resp.status_code, resp.json()) == (
        422,
        {"detail": "body.paths: Input should be a valid list", "reason": "invalid"},
    )


def test_a_wrong_token_is_token(client):
    resp = client.get("/api/v1/boxes/local/ls", headers={"X-SSHLER-TOKEN": "wrong"})
    assert (resp.status_code, resp.json()) == (
        403,
        {"detail": "Missing or invalid X-SSHLER-TOKEN header", "reason": "token"},
    )


def test_a_box_that_is_not_configured_is_not_found(client):
    resp = client.get("/api/v1/boxes/nobox/ls", headers=HEADERS)
    assert (resp.status_code, resp.json()) == (
        404,
        {"detail": "Box not found", "reason": "not_found"},
    )


def test_a_foreign_origin_is_origin(client):
    resp = client.post(
        "/api/v1/boxes/local/batch/delete",
        json={"paths": ["/x"]},
        headers={**HEADERS, "Origin": "https://evil.example"},
    )
    assert (resp.status_code, resp.json()) == (
        403,
        {"detail": "Invalid Origin header", "reason": "origin"},
    )


def test_a_batch_item_of_a_missing_file_carries_the_single_answer(client, tmp_path):
    missing = str(tmp_path / "gone.txt")
    single = client.post("/api/v1/boxes/local/delete", json={"path": missing}, headers=HEADERS)
    batch = client.post(
        "/api/v1/boxes/local/batch/delete", json={"paths": [missing]}, headers=HEADERS
    )
    assert (single.status_code, single.json()) == (
        404,
        {"detail": "File not found", "reason": "not_found"},
    )
    assert (batch.status_code, batch.json()) == (
        200,
        {
            "status": "partial",
            "succeeded": [],
            "failed": [{"path": missing, "error": "File not found", "reason": "not_found"}],
        },
    )


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root reads mode 0")
def test_a_batch_item_the_system_refuses_is_denied_as_a_single_request_is(client, tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    (folder / "x.txt").write_bytes(b"x")
    folder.chmod(0)
    try:
        path = str(folder / "x.txt")
        single = client.post("/api/v1/boxes/local/delete", json={"path": path}, headers=HEADERS)
        batch = client.post(
            "/api/v1/boxes/local/batch/delete", json={"paths": [path]}, headers=HEADERS
        )
    finally:
        folder.chmod(0o755)
    expected = f"Permission denied: {path}"
    assert (single.status_code, single.json()) == (403, {"detail": expected, "reason": "denied"})
    assert batch.json()["failed"] == [{"path": path, "error": expected, "reason": "denied"}]


# --- the per-route rate limit says when to come back ----------------------------------


def test_the_seconds_until_a_drained_limit_allows_a_request():
    now = [1000.0]
    limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1, clock=lambda: now[0])
    assert (limiter.check("ip"), limiter.check("ip"), limiter.check("ip")) == (True, True, False)
    assert limiter.retry_after("ip") == 30
    now[0] += 20.5
    assert limiter.retry_after("ip") == 10
    assert limiter.retry_after("never seen") == 1


def test_a_route_past_its_rate_limit_answers_retry_after():
    application = FastAPI()
    failure.install(application)
    limit = create_rate_limit_dependency(name="failure-test", rate=2, per=60, capacity_multiplier=1)

    @application.get("/x", dependencies=[Depends(limit)])
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(application)
    statuses = [client.get("/x").status_code for _ in range(2)]
    refused = client.get("/x")
    assert statuses == [200, 200]
    assert (refused.status_code, refused.json(), refused.headers["Retry-After"]) == (
        429,
        {
            "detail": "Rate limit exceeded. Maximum 2 requests per 60 seconds allowed.",
            "reason": "rate_limited",
        },
        "30",
    )


# --- the check over every failure answer the suite's client receives -----------------


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"detail": [{"msg": "x"}], "reason": "invalid"}, "detail is not a text"),
        ({"detail": "x"}, "body is not exactly detail and reason"),
        ({"detail": "x", "reason": "oops"}, "reason is not a word of the list"),
        ({"detail": "x", "reason": "invalid", "more": 1}, "body is not exactly detail and reason"),
    ],
)
def test_the_suite_wide_check_refuses_a_wrong_failure_body(body, problem):
    app = SimpleNamespace(exception_handlers={Exception: failure._unanswered})
    response = SimpleNamespace(status_code=400, json=lambda: body, text=json.dumps(body))
    assert failure_body_problem(app, "GET", response) == f"400 {problem}: {body!r}"


def test_the_suite_wide_check_passes_a_right_failure_body():
    app = SimpleNamespace(exception_handlers={Exception: failure._unanswered})
    body = {"detail": "x", "reason": "invalid"}
    response = SimpleNamespace(status_code=400, json=lambda: body, text=json.dumps(body))
    assert failure_body_problem(app, "GET", response) is None
