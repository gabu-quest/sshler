"""Join one WebSocket to one terminal transport.

What the client receives: output as binary frames; ``{"op": "windows", ...}`` text
frames while the transport has tmux windows; ``{"op": "ping"}`` text frames. What it
sends: binary frames are input; text frames are JSON control messages (``resize``,
``select-window``, ``rename-window``, ``send``); text that is not JSON is ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .transport import Transport, WindowList, teardown_scope

logger = logging.getLogger(__name__)

# How often the tmux window list is sent, and how often a ping keeps proxies and NAT open.
WINDOW_POLL_INTERVAL = 2.0
PING_INTERVAL = 30.0
# How long the socket waits for a message the client sent with its connect.
EARLY_MESSAGE_WAIT = 0.05

WindowsSeen = Callable[[WindowList], Awaitable[None]]


class PumpEnd(enum.Enum):
    """Why a pump stopped."""

    CLIENT_LEFT = "client left"
    INPUT_FAILED = "input failed"
    OUTPUT_ENDED = "output ended"
    OUTPUT_FAILED = "output failed"


async def apply_control(payload: str, transport: Transport) -> None:
    """Apply one JSON control message to *transport*.

    Text that is not JSON is ignored. A resize whose numbers are not both positive is
    ignored; one whose fields are not numbers raises, as does JSON that is not an
    object, and the raise ends the session.
    """
    try:
        message = json.loads(payload)
    except json.JSONDecodeError:
        return
    operation = message.get("op")
    if operation == "resize":
        cols = int(message.get("cols", 0) or 0)
        rows = int(message.get("rows", 0) or 0)
        logger.info(f"[Resize] Received resize: {cols}x{rows}")
        if cols > 0 and rows > 0:
            await transport.resize(cols, rows)
    elif operation == "select-window":
        target = message.get("target")
        if target is not None and transport.tmux is not None:
            await transport.tmux.select(target)
    elif operation == "rename-window":
        name = message.get("target")
        if name and transport.tmux is not None:
            await transport.tmux.rename(str(name))
    elif operation == "send":
        data = message.get("data")
        if data:
            try:
                await transport.write(data.encode())
            except Exception as exc:
                logger.debug(f"Failed to send data to process: {exc}")


async def deliver(message: dict[str, Any], transport: Transport) -> bool:
    """Hand one received WebSocket message to *transport*; False once the client left."""
    if message.get("type") == "websocket.disconnect":
        return False
    text = message.get("text")
    if text is not None:
        if isinstance(text, str):
            await apply_control(text, transport)
        return True
    data = message.get("bytes")
    if isinstance(data, bytes):
        await transport.write(data)
    return True


async def _receive(websocket: WebSocket) -> dict[str, Any]:
    """The client's next message, or a disconnect once the server has closed the socket
    (its session ended): nothing the client sends after that reaches the terminal."""
    message = dict(await websocket.receive())
    if websocket.application_state != WebSocketState.CONNECTED:
        return {"type": "websocket.disconnect"}
    return message


async def take_early_message(websocket: WebSocket, transport: Transport) -> bool:
    """Apply a message the client sent with its connect (a first resize); False if it left."""
    try:
        message = await asyncio.wait_for(_receive(websocket), timeout=EARLY_MESSAGE_WAIT)
    except TimeoutError:
        return True
    except WebSocketDisconnect:
        return False
    return await deliver(message, transport)


def _connected(websocket: WebSocket) -> bool:
    return websocket.client_state == WebSocketState.CONNECTED


async def _pump_output(websocket: WebSocket, transport: Transport) -> PumpEnd:
    """Send output as binary frames until the terminal or the client ends."""
    while True:
        try:
            data = await transport.read()
        except Exception as exc:
            logger.info(f"Terminal output failed: {exc}")
            return PumpEnd.OUTPUT_FAILED
        if not data:
            return PumpEnd.OUTPUT_ENDED
        if not _connected(websocket):
            return PumpEnd.CLIENT_LEFT
        try:
            await websocket.send_bytes(data)
        except Exception as exc:
            logger.debug(f"Sending output ended: {exc}")
            return PumpEnd.CLIENT_LEFT


async def _pump_input(websocket: WebSocket, transport: Transport) -> PumpEnd:
    """Deliver the client's messages until it leaves or the terminal refuses one."""
    try:
        while await deliver(await _receive(websocket), transport):
            pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"Terminal input failed: {exc}", exc_info=True)
        return PumpEnd.INPUT_FAILED
    return PumpEnd.CLIENT_LEFT


async def _poll_windows(websocket: WebSocket, transport: Transport, seen: WindowsSeen) -> None:
    """Send the tmux window list every few seconds."""
    tmux = transport.tmux
    if tmux is None:
        return
    with contextlib.suppress(Exception):
        while _connected(websocket):
            windows = await tmux.list_windows()
            if windows is not None:
                if not _connected(websocket):
                    return
                await websocket.send_text(json.dumps({"op": "windows", "windows": windows}))
                await seen(windows)
            await asyncio.sleep(WINDOW_POLL_INTERVAL)


async def _ping(websocket: WebSocket) -> None:
    """Send a ping text frame every half minute."""
    with contextlib.suppress(Exception):
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if not _connected(websocket):
                return
            await websocket.send_text(json.dumps({"op": "ping"}))


# How the socket closes when the terminal ended first. A terminal that exited is a
# normal closure, which the client does not reconnect after; a failed transport (a
# dropped SSH connection) is not, so the client reconnects.
CLOSE_WHEN = {
    PumpEnd.OUTPUT_ENDED: (1000, "shell exited"),
    PumpEnd.OUTPUT_FAILED: (1011, "terminal connection lost"),
}


async def run_pump(websocket: WebSocket, transport: Transport, seen: WindowsSeen) -> PumpEnd:
    """Carry the session until the client leaves or the terminal ends.

    *seen* is called with each window list sent. Whatever ends it, every task the
    pump started is cancelled and awaited before it returns or raises. When the
    terminal ended first, the socket is closed as ``CLOSE_WHEN`` says.
    """
    end = await _carry(websocket, transport, seen)
    if end in CLOSE_WHEN:
        code, reason = CLOSE_WHEN[end]
        with contextlib.suppress(Exception):
            await websocket.close(code=code, reason=reason)
    return end


async def _carry(websocket: WebSocket, transport: Transport, seen: WindowsSeen) -> PumpEnd:
    output = asyncio.create_task(_pump_output(websocket, transport), name="terminal-output")
    input_ = asyncio.create_task(_pump_input(websocket, transport), name="terminal-input")
    helpers = [
        asyncio.create_task(_poll_windows(websocket, transport, seen), name="terminal-windows"),
        asyncio.create_task(_ping(websocket), name="terminal-ping"),
    ]
    tasks = [output, input_, *helpers]
    try:
        done, _ = await asyncio.wait((output, input_), return_when=asyncio.FIRST_COMPLETED)
        # The output's ending names the cause when both ended together.
        return output.result() if output in done else input_.result()
    finally:
        for task in tasks:
            task.cancel()
        with teardown_scope():
            await asyncio.gather(*tasks, return_exceptions=True)
