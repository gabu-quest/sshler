import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sshler.state import reset_state  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state_store():
    reset_state()
    yield
    reset_state()
