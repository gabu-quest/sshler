"""The events of a transfer's stream, read and checked against the error contract.

``transfer_events`` parses the stream's text into ``(event, data)`` pairs and fails
the test when an ``error`` event or a failed item (a ``failed`` event, or an entry of
the ``done`` event's ``failed`` list) does not carry a text and a reason from the
closed list of reasons, so no test that reads a transfer can pass on a body without
them.
"""

from __future__ import annotations

import json
from typing import Any

from sshler.api.failure import STATUS_OF_REASON


def _check_error(data: dict[str, Any]) -> None:
    assert set(data) == {"detail", "reason"}, f"an error event is detail and reason: {data}"
    assert isinstance(data["detail"], str) and data["detail"], f"an error without text: {data}"
    assert data["reason"] in STATUS_OF_REASON, f"an error without a known reason: {data}"


def _check_item(item: dict[str, Any]) -> None:
    assert set(item) == {"path", "error", "reason"}, f"a failed item is path, error, reason: {item}"
    assert isinstance(item["error"], str) and item["error"], f"a failed item without text: {item}"
    assert item["reason"] in STATUS_OF_REASON, f"a failed item without a known reason: {item}"


def transfer_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """The stream's events in order, each checked."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        kind, data = lines["event"], json.loads(lines["data"])
        if kind == "error":
            _check_error(data)
        elif kind == "failed":
            _check_item(data)
        elif kind == "done":
            for item in data["failed"]:
                _check_item(item)
        events.append((kind, data))
    return events
