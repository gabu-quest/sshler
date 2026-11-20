"""The terminal behind ``/ws/term``: transports, and the pump that joins one to a socket.

A transport (``transport.Transport``) is one way of running a terminal: a tmux client
on a local PTY (``local``), tmux over SSH (``remote``), or a persisted native Windows
shell (``conpty``). It opens, reads, writes, resizes and closes, and knows nothing of
the WebSocket. The pump (``pump.run_pump``) carries bytes and control messages between
one socket and one transport, and knows nothing of which transport it drives.
"""

from .pump import PumpEnd, run_pump, take_early_message
from .transport import OpenedTerminal, TerminalOpenError, TmuxWindows, Transport

__all__ = [
    "OpenedTerminal",
    "PumpEnd",
    "TerminalOpenError",
    "TmuxWindows",
    "Transport",
    "run_pump",
    "take_early_message",
]
