"""Allowlisted Codex public events translated to harness-neutral attempt activity."""

import base64
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from flowfield.run_activity import ActivityUpdate


class CodexActivity:
    def __init__(self, emit: Callable[[ActivityUpdate], None]):
        self.emit = emit
        self.messages: OrderedDict[str, str] = OrderedDict()
        self.output: dict[str, dict[str, bytes]] = {}

    def key(self, identity: str) -> str:
        if identity not in self.messages:
            self.messages[identity] = uuid4().hex
            if len(self.messages) > 256:
                self.messages.popitem(last=False)
        return self.messages[identity]

    def event(
        self,
        method: str | None,
        params: dict[str, Any],
        thread: str | None,
        turn: str | None,
        commands: set[str],
    ) -> None:
        if method == "command/exec/outputDelta":
            identity = params.get("processId")
            stream = params.get("stream")
            if identity not in commands or stream not in ("stdout", "stderr"):
                return
            try:
                raw = base64.b64decode(params.get("deltaBase64", ""), validate=True)
            except ValueError:
                return
            streams = self.output.setdefault(identity, {})
            combined = streams.get(stream, b"") + raw
            clipped = len(combined) > 12000
            marker = b"\n... middle omitted ...\n"
            streams[stream] = (
                combined[:6000] + marker + combined[-(6000 - len(marker)) :]
                if clipped
                else combined
            )
            self.emit(
                ActivityUpdate(
                    key=self.key(identity + stream),
                    kind="output",
                    text=streams[stream].decode("utf-8", errors="replace"),
                    omitted=clipped or bool(params.get("capReached")),
                )
            )
            return
        if (
            not thread
            or not turn
            or params.get("threadId") != thread
            or params.get("turnId") != turn
        ):
            return
        if method == "item/agentMessage/delta":
            self.emit(
                ActivityUpdate(
                    key=self.key(params["itemId"]),
                    kind="agent",
                    text=params.get("delta", ""),
                    append=True,
                )
            )
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                self.emit(
                    ActivityUpdate(
                        key=self.key(item["id"]), kind="agent", text=item.get("text", "")
                    )
                )
        # No reasoning, raw response items, config, errors or unsupported item payloads.
