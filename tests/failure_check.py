"""The check every failure answer in the suite passes; `conftest.py` applies it to each
answer the test client receives. In a module of its own because `tests/e2e/` has a
`conftest` too, and a whole-suite run imports that one under the name."""

from __future__ import annotations

from sshler.api import failure as api_failure


def failure_body_problem(app: object, method: str, response) -> str | None:
    """What is wrong with a failure answer of an app made by ``make_app``: its body is
    not exactly ``{"detail": text, "reason": word}`` with a word of the list. None when
    the answer is right, or not a failure of such an app."""
    handlers = getattr(app, "exception_handlers", {})
    if response.status_code < 400 or method.upper() == "HEAD":
        return None
    if not any(getattr(h, "__module__", None) == api_failure.__name__ for h in handlers.values()):
        return None
    try:
        body = response.json()
    except ValueError:
        return f"{response.status_code} body is not JSON: {response.text[:200]!r}"
    if not isinstance(body, dict) or set(body) != {"detail", "reason"}:
        return f"{response.status_code} body is not exactly detail and reason: {body!r}"
    if not isinstance(body["detail"], str):
        return f"{response.status_code} detail is not a text: {body!r}"
    reason = body["reason"]
    if reason not in api_failure.STATUS_OF_REASON:
        return f"{response.status_code} reason is not a word of the list: {body!r}"
    return None
