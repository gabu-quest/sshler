"""A git ref is given to git as a bare word: one that starts with a dash would be read
as an option (``--output`` makes ``git diff`` write a file), so it is refused."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from sshler.api.git import _validate_ref


@pytest.mark.parametrize("ref", ["--output", "-p", "-O/etc/passwd", "--help"])
def test_a_ref_starting_with_a_dash_is_refused(ref):
    """Mutation: accept any ref the character set allows; the option reaches git."""
    with pytest.raises(HTTPException) as caught:
        _validate_ref(ref)
    assert (caught.value.status_code, caught.value.detail) == (400, "Invalid git ref")


@pytest.mark.parametrize("ref", ["HEAD", "main", "feature/x-y", "v1.2", "abc1234"])
def test_an_ordinary_ref_is_accepted(ref):
    assert _validate_ref(ref) == ref
