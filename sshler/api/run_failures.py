"""What a program run on a box fails with when the program is not there.

In a module of its own so that ``failure.py`` (the owner of every failure's reason) can
name it by its type without importing the runner. The runner (``box_runner.py``) raises
it, the same type on both kinds of box.
"""

from __future__ import annotations


class ProgramNotFoundError(Exception):
    """The box has no program by this name: the local box finds none on its ``PATH``;
    on a remote box the shell answered status 127. The message names the program and
    the box."""

    def __init__(self, program: str, box: str) -> None:
        super().__init__(f"{program} is not installed on {box}")
        self.program = program
        self.box = box
