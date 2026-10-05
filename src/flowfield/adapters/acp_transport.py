"""Bounded stdio framing for the ACP SDK; JSON-RPC dispatch remains SDK-owned."""

import asyncio
import json
from typing import Any

MAX_FRAME = 256 * 1024
MAX_TRAFFIC = 16 * 1024 * 1024
MAX_MESSAGES = 20000


class StdioTransport:
    # The SDK's default reader assembles arbitrarily large lines across stream limits.
    # Enforce a frame and per-turn budget before parsing or dispatching messages.
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self.reset_budget()

    def reset_budget(self) -> None:
        self.received = 0
        self.messages = 0

    async def receive(self) -> dict[str, Any] | None:
        while (message := await self._frame()) is not None:
            params = message.get("params")
            if message.get("method") == "session/update" and isinstance(params, dict):
                update = params.get("update")
                if (
                    isinstance(update, dict)
                    and update.get("sessionUpdate") == "agent_thought_chunk"
                ):
                    # Drop before SDK validation: even malformed private reasoning
                    # must not appear in the SDK's validation-error diagnostics.
                    continue
            return message
        return None

    async def _frame(self) -> dict[str, Any] | None:
        # Give already-dispatched callbacks a chance to finish before reading more.
        await asyncio.sleep(0)
        try:
            line = await self.reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as error:
            if error.partial:
                raise ConnectionError("Incomplete ACP frame") from None
            return None
        except asyncio.LimitOverrunError:
            raise ConnectionError("ACP frame exceeds limit") from None
        self.received += len(line)
        self.messages += 1
        if len(line) > MAX_FRAME or self.received > MAX_TRAFFIC or self.messages > MAX_MESSAGES:
            raise ConnectionError("ACP traffic exceeds limit")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            raise ConnectionError("Invalid ACP frame") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ConnectionError("Invalid ACP message")
        return message

    async def send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False).encode() + b"\n"
        if len(data) > MAX_FRAME:
            raise ValueError("ACP frame exceeds limit")
        self.writer.write(data)
        await self.writer.drain()

    async def close(self) -> None:
        self.writer.close()
        try:
            async with asyncio.timeout(2):
                await self.writer.wait_closed()
        except (OSError, TimeoutError):
            pass
