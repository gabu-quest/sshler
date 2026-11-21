"""The page's list of failure reasons is the server's list.

`frontend/src/api/apiError.ts` holds the reasons the page knows, as a const list its
reason type is made from; a reason the server gives that is not in it reaches a person
as the no-reason line. This reads that file as text and compares its words with the
keys of `STATUS_OF_REASON`.
"""

from __future__ import annotations

import re
from pathlib import Path

from sshler.api.failure import STATUS_OF_REASON

PAGE_REASONS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api" / "apiError.ts"


def page_reasons(text: str) -> list[str]:
    """The quoted words of the `FAILURE_REASONS = [ ... ] as const` list in ``text``."""
    match = re.search(r"export const FAILURE_REASONS = \[(.*?)\] as const;", text, re.DOTALL)
    assert match is not None, "FAILURE_REASONS list not found"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def test_the_page_lists_the_servers_reasons_in_the_same_order() -> None:
    assert page_reasons(PAGE_REASONS.read_text(encoding="utf-8")) == list(STATUS_OF_REASON)


def test_a_list_missing_a_reason_is_told_apart() -> None:
    # The reader would pass any list if it took no words; one word fewer must differ.
    short = 'export const FAILURE_REASONS = [\n  "invalid",\n  "auth",\n] as const;'
    assert page_reasons(short) == ["invalid", "auth"]
    assert page_reasons(short) != list(STATUS_OF_REASON)
