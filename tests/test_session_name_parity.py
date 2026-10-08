"""Cross-language parity: ``ts_session_name`` vs the frontend ``generateSessionName``.

Both this file and ``frontend/src/utils/sessionName.parity.spec.ts`` read
``tests/fixtures/session_name_vectors.json`` and assert every vector, so the two
implementations are pinned to the same outputs.

Mutation killed: narrowing the filter in ``ts_session_name`` to ASCII alnum (the
``isalnum`` call), mapping ``..`` to anything but ``home``, or splitting a POSIX
path on ``\\``, turns the matching vector red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sshler.tmux import ts_session_name

_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "session_name_vectors.json").read_text(encoding="utf-8")
)


# Vectors that must stay in the shared file: each pins a class of input the two
# implementations once disagreed on. The frontend spec asserts the same list.
_MUST_HAVE = [
    ("/srv/a😀b", "a_b"),  # astral-plane emoji: one code point, not two UTF-16 units
    ("/srv/日本語", "日本語"),  # CJK kept
    ("/srv/café", "café"),  # accented letters kept
    ("/srv/.hidden", "_hidden"),  # leading dot replaced
    ("/srv/ver 1.2.3", "ver_1_2_3"),  # dots and spaces replaced
    ("..", "home"),  # parent-dir fallback
    ("/srv/a\\b", "a_b"),  # `\` in a POSIX path is a character, not a separator
    ("C:\\Users\\x\\proj", "proj"),  # `\` separates segments in a drive path
    ("\\\\server\\share\\proj", "proj"),  # ... and in a UNC path
]


@pytest.mark.parametrize(("directory", "expected"), _MUST_HAVE)
def test_vector_file_contains_must_have_vector(directory: str, expected: str) -> None:
    """Mutation killed: deleting a vector class from the shared file (the parity
    check would then pass without covering it)."""
    assert {"directory": directory, "expected": expected} in _VECTORS


@pytest.mark.parametrize("vector", _VECTORS, ids=[repr(v["directory"]) for v in _VECTORS])
def test_ts_session_name_matches_golden_vector(vector: dict[str, str]) -> None:
    assert ts_session_name(vector["directory"]) == vector["expected"]
