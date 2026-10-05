"""One ACP session owner for either role. Not a scheduler or an execution sandbox.

No automatic reconnect/replay: after an uncertain operation the caller reconciles
canonical Flowfield state before explicitly creating/loading another session.
"""

import asyncio
import contextlib
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, connect_to_agent, text_block
from acp.client import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    ClientCapabilities,
    HttpMcpServer,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from pydantic import BaseModel

from flowfield.adapters.acp_transport import MAX_FRAME, StdioTransport
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class PermissionRequest:
    tool_id: str
    title: str
    options: tuple[tuple[str, str, str], ...]  # id, label, kind


@dataclass(frozen=True)
class StopReceipt:
    turn_finished: bool
    process_group_exited: bool
    # This deliberately does NOT assert that an agent's detached tools have stopped.
    # A managed worker adapter must additionally verify its execution environment.


PermissionHandler = Callable[[PermissionRequest], Coroutine[Any, Any, str | None]]


class AcpSession:
    def __init__(
        self,
        on_event: Callable[[AgentEvent], None],
        *,
        on_permission: PermissionHandler | None = None,
        request_timeout: float = 30,
        turn_timeout: float = 3600,
    ):
        self.on_event, self.on_permission = on_event, on_permission
        self.request_timeout, self.turn_timeout = request_timeout, turn_timeout
        self.state = "new"
        self.session_id: str | None = None
        self.config: list[dict[str, Any]] = []
        self.capabilities: dict[str, Any] = {}
        self.process: asyncio.subprocess.Process | None = None
        self._local_process: LocalProcess | None = None
        self.connection: ClientSideConnection | None = None
        self.transport: StdioTransport | None = None
        self._turn: asyncio.Task[Any] | None = None
        self._permission: asyncio.Task[str | None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._spawn_lock = asyncio.Lock()
        self._close_task: asyncio.Task[StopReceipt] | None = None
        self._event_failed = False

    async def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        mcp_servers: list[HttpMcpServer],
        load_session_id: str | None = None,
    ) -> None:
        if self.state != "new":
            raise RuntimeError("Session has already been started")
        if not command or not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError(
                "An agent command and existing absolute working directory are required"
            )
        self.state = "starting"
        try:
            async with self._spawn_lock:
                try:
                    self._local_process = await LocalProcess.start(
                        command, cwd=cwd, env=env, limit=MAX_FRAME
                    )
                except LocalLaunchCancelled as error:
                    self._local_process = error.owner
                    self.process = error.owner.process
                    raise
                self.process = self._local_process.process
                assert self.process.stdin and self.process.stdout and self.process.stderr
                self._stderr = asyncio.create_task(self._drain_stderr(self.process.stderr))
                self.transport = StdioTransport(self.process.stdout, self.process.stdin)
                self.connection = connect_to_agent(cast(Client, self), self.transport)
            async with asyncio.timeout(self.request_timeout):
                initialized = await self.connection.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(name="flowfield", version="0.1.0"),
                )
                if initialized.protocol_version != PROTOCOL_VERSION:
                    raise RuntimeError("Agent does not support this ACP protocol version")
                self.capabilities = (
                    initialized.agent_capabilities.model_dump(by_alias=True, exclude_none=True)
                    if initialized.agent_capabilities
                    else {}
                )
                if mcp_servers and not self.capabilities.get("mcpCapabilities", {}).get("http"):
                    raise RuntimeError("Agent does not support scoped HTTP MCP servers")
                if load_session_id is not None:
                    if not self.capabilities.get("loadSession"):
                        raise RuntimeError("Agent does not support loading sessions")
                    # Bind before load so replay can be filtered; never resend a prompt.
                    self.session_id = load_session_id
                    loaded = await self.connection.load_session(
                        cwd=str(cwd),
                        session_id=load_session_id,
                        mcp_servers=[*mcp_servers],
                    )
                    self.config = [
                        item.model_dump(by_alias=True) for item in loaded.config_options or []
                    ]
                else:
                    created = await self.connection.new_session(
                        cwd=str(cwd), mcp_servers=[*mcp_servers]
                    )
                    self.session_id = created.session_id
                    self.config = [
                        item.model_dump(by_alias=True) for item in created.config_options or []
                    ]
            if self.state != "starting":
                raise RuntimeError("Agent session stopped during startup")
            self.state = "ready"
        except BaseException:
            self.state = "interrupted"
            await self.close()
            raise

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        # Drain without retaining raw agent diagnostics (may contain private content).
        while await stream.read(8192):
            pass

    def _ready(self) -> tuple[ClientSideConnection, str]:
        if self.state != "ready" or self.connection is None or self.session_id is None:
            raise RuntimeError(
                "Agent session is not ready; reconcile interrupted work before retrying"
            )
        return self.connection, self.session_id

    async def select(self, option_id: str, value: str) -> None:
        connection, session_id = self._ready()
        option = next((item for item in self.config if item["id"] == option_id), None)
        choices = [] if option is None else option.get("options", [])
        values = {entry["value"] for group in choices for entry in (group.get("options", [group]))}
        if value not in values:
            raise ValueError("Agent configuration choice is unavailable")
        self.state = "configuring"
        try:
            async with asyncio.timeout(self.request_timeout):
                response = await connection.set_config_option(option_id, session_id, value)
            self.config = [item.model_dump(by_alias=True) for item in response.config_options]
            selected = next((item for item in self.config if item["id"] == option_id), None)
            if selected is None or selected["currentValue"] != value:
                raise RuntimeError("Agent did not apply the requested configuration choice")
            if self.state != "configuring":
                raise RuntimeError("Agent session stopped while configuring")
            self.state = "ready"
        except BaseException:
            self.state = "interrupted"
            await self.close()
            raise

    async def prompt(self, text: str) -> str:
        connection, session_id = self._ready()
        if not text.strip() or len(text.encode()) > MAX_FRAME // 2:
            raise ValueError("Prompt must contain text and fit the session input limit")
        assert self.transport
        self.transport.reset_budget()
        self.state = "running"
        self._turn = asyncio.create_task(
            connection.prompt(session_id=session_id, prompt=[text_block(text)])
        )
        try:
            async with asyncio.timeout(self.turn_timeout):
                response = await asyncio.shield(self._turn)
            if self._event_failed:
                raise RuntimeError("Agent response could not be delivered")
            if self.state == "running":
                self.state = "ready"
            return str(response.stop_reason)
        except BaseException:
            if self.state != "stopping":
                self.state = "interrupted"
                await self.close()
            raise

    async def session_update(self, session_id: str, update: BaseModel, **kwargs: Any) -> None:
        if session_id != self.session_id or self.state not in {"starting", "running"}:
            return
        data = update.model_dump(by_alias=True, exclude_none=True)
        kind = data.pop("sessionUpdate", "")
        # Publish a deliberate projection, never raw reasoning, inputs, metadata or tool output.
        if kind == "agent_message_chunk" and self.state == "running":
            content = data.get("content", {})
            if content.get("type") == "text":
                self._emit(AgentEvent("text", {"text": content["text"]}))
        elif kind in {"tool_call", "tool_call_update"} and self.state == "running":
            self._emit(
                AgentEvent(
                    "tool",
                    {
                        key: data[key]
                        for key in ("toolCallId", "title", "status", "kind")
                        if key in data
                    },
                )
            )
        elif kind == "usage_update" and self.state == "running":
            self._emit(
                AgentEvent("usage", {key: data[key] for key in ("used", "size") if key in data})
            )
        elif kind == "config_option_update":
            self.config = data["configOptions"]

    def _emit(self, event: AgentEvent) -> None:
        try:
            self.on_event(event)
        except Exception:
            # The SDK logs callback errors and otherwise continues the prompt. A
            # lost application event must instead interrupt work, never look saved.
            self._event_failed = True
            if self._turn:
                self._turn.cancel()

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        cancelled = RequestPermissionResponse.model_validate({"outcome": {"outcome": "cancelled"}})
        if (
            session_id != self.session_id
            or self.state != "running"
            or self.on_permission is None
            or self._permission is not None
        ):
            return cancelled
        request = PermissionRequest(
            tool_call.tool_call_id,
            tool_call.title or "Tool permission",
            tuple((item.option_id, item.name, item.kind) for item in options),
        )
        task = self._permission = asyncio.create_task(self.on_permission(request))
        try:
            async with asyncio.timeout(self.turn_timeout):
                selected = await task
            if self.state != "running" or selected not in {item.option_id for item in options}:
                return cancelled
            return RequestPermissionResponse.model_validate(
                {
                    "outcome": {"outcome": "selected", "optionId": selected},
                }
            )
        except (asyncio.CancelledError, TimeoutError):
            return cancelled
        finally:
            self._permission = None

    async def close(self, *, timeout: float = 5) -> StopReceipt:
        if self._close_task is None:
            self.state = "stopping"
            self._close_task = asyncio.create_task(self._shutdown(timeout))
        # Cancelling an HTTP caller must not abandon an owned agent process.
        return await asyncio.shield(self._close_task)

    async def _shutdown(self, timeout: float) -> StopReceipt:
        self.state = "stopping"
        async with self._spawn_lock:
            pass  # A concurrently starting process must acquire its owner first.
        if self._permission:
            self._permission.cancel()
        turn_finished = self._turn is None
        if self._turn is not None and self.connection is not None:
            try:
                async with asyncio.timeout(timeout):
                    if not self._turn.done() and self.session_id:
                        await self.connection.cancel(session_id=self.session_id)
                    await asyncio.shield(self._turn)
                turn_finished = True
            except (Exception, asyncio.CancelledError):
                pass
        if self.connection:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(timeout):
                    await self.connection.close()
        exited = True
        if self._local_process is not None:
            exited = await self._local_process.close(timeout=timeout)
        if self._turn:
            self._turn.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._turn
        if self._stderr:
            self._stderr.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr
        self.state = "closed" if exited and turn_finished else "interrupted"
        return StopReceipt(turn_finished, exited)
