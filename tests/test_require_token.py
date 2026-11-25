from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sshler.api.dependencies import APIDependencies

EXPECTED = "abcdefgh-REAL-secret-tail-9876"
SUPPLIED = "abcdefgh-FAKE-wrong-value-1234"


def _request(path: str, *, header: str | None = None, query: str = "") -> Request:
    headers = [(b"x-sshler-token", header.encode())] if header else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode(),
            "headers": headers,
        }
    )


def _deps() -> APIDependencies:
    return APIDependencies(SimpleNamespace(csrf_token=EXPECTED))


def _runs(text: str, size: int = 4) -> set[str]:
    return {text[i : i + size] for i in range(len(text) - size + 1)}


@pytest.mark.parametrize(
    ("kwargs", "path", "supplied_word"),
    [
        ({"header": SUPPLIED}, "/api/v1/boxes", "supplied=yes"),
        ({"query": f"token={SUPPLIED}"}, "/api/v1/files", "supplied=yes"),
        ({}, "/api/v1/boxes", "supplied=no"),
    ],
)
def test_token_mismatch_log_holds_no_token_characters(caplog, kwargs, path, supplied_word):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HTTPException) as excinfo:
        _deps().require_token(_request(path, **kwargs))
    assert excinfo.value.status_code == 403

    messages = [r.getMessage() for r in caplog.records]
    assert messages == [f"Token mismatch on {path}: {supplied_word}"]
    blob = " ".join(messages)
    assert not (_runs(blob) & (_runs(EXPECTED) | _runs(SUPPLIED)))


def test_matching_token_passes_and_logs_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    _deps().require_token(_request("/api/v1/boxes", header=EXPECTED))
    assert caplog.records == []
