"""Revocable, role-scoped MCP tools over canonical Flowfield operations.

These grants are created by the service, never from agent-supplied role/scope.
They do not replace the standalone MCP interface or authorize code approval.
"""

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError

from flowfield.errors import ApplicationError
from flowfield.mcp import create_mcp
from flowfield.worker_tools import WorkerBridge, worker_tools

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

# Explicit authority, not "anything with a project_id argument". Human answer,
# result approval, worker settings, queue and project adoption are absent.
COORDINATOR_TOOLS = frozenset(
    {
        "get_project",
        "get_integration_settings",
        "configure_integration",
        "get_inspection_settings",
        "configure_inspection",
        "get_setup_validation",
        "validate_project_setup",
        "get_board",
        "search_context",
        "list_milestones",
        "get_milestone",
        "create_milestone",
        "edit_milestone",
        "list_tasks",
        "get_task",
        "create_task",
        "edit_task",
        "prioritize_task",
        "publish_task",
        "reconcile_task",
        "list_task_relationships",
        "list_task_revisions",
        "get_text",
        "get_activity",
        "list_activity",
        "list_questions",
        "get_question",
        "ask_question",
        "apply_answer",
        "follow_up_question",
        "get_task_conversation",
        "get_conversation_source",
        "get_task_stages",
        "update_task_stages",
    }
)


class ScopedTools:
    def __init__(
        self,
        tools: list[Tool],
        call: Callable[[str, dict[str, Any]], Awaitable[CallToolResult]],
        *,
        parallel_reads: bool = False,
    ):
        self.tools = {tool.name: tool for tool in tools}
        self._call = call
        self.revoked = False
        self._lock = asyncio.Lock()
        self._parallel_reads = parallel_reads
        self._reading = 0
        self.server: Server[Any] = Server("flowfield-scoped")

        @self.server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_tools() -> list[Tool]:
            return [] if self.revoked else list(self.tools.values())

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            return await self.call(name, arguments)

    async def call(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            if self.revoked:
                raise ApplicationError("scope_closed", "This agent's tool access has ended.", 403)
            if name not in self.tools:
                raise ApplicationError(
                    "operation_denied", "Operation is outside this role's scope.", 403
                )
            # Coordinator reads use canonical snapshots and may overlap. Native
            # agents routinely batch independent reads; do not turn that into a
            # spurious tool failure. Bound in-flight work without a waiting queue.
            annotation = self.tools[name].annotations
            if self._parallel_reads and annotation and annotation.readOnlyHint:
                if self._reading >= 8:
                    raise ApplicationError(
                        "tool_busy", "Too many concurrent reads. Retry after one finishes.", 409
                    )
                self._reading += 1
                try:
                    return await self._call(name, arguments)
                finally:
                    self._reading -= 1
            # No unbounded waiting tool queue; a result cannot overtake an active command.
            if self._lock.locked():
                raise ApplicationError(
                    "tool_busy", "Wait for the current operation to finish.", 409
                )
            async with self._lock:
                return await self._call(name, arguments)
        except ApplicationError as error:
            return failure(error.code, error.message)
        except ValidationError:
            return failure("invalid_request", "Tool input does not match its schema.")

    def revoke(self) -> None:
        # Already-running operations remain owned by their executor; revoking access
        # does not claim to cancel a command or roll back an accepted mutation.
        self.revoked = True


def failure(code: str, message: str) -> CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=True,
    )


def worker_scope(bridge: WorkerBridge) -> ScopedTools:
    async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        result = await bridge.call(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=result)])

    return ScopedTools(
        [
            Tool(
                name=item["name"], description=item["description"], inputSchema=item["inputSchema"]
            )
            for item in worker_tools()
        ],
        call,
    )


async def coordinator_scope(supervisor: "Supervisor", project_id: str) -> ScopedTools:
    workspace = supervisor.workspace
    workspace.project(project_id)
    canonical = create_mcp(lambda: workspace, lambda: supervisor, origin="")
    scoped = []
    for tool in await canonical.list_tools():
        if tool.name not in COORDINATOR_TOOLS:
            continue
        schema = copy.deepcopy(tool.inputSchema)
        if "project_id" not in schema.get("properties", {}):
            raise RuntimeError("Coordinator tool lacks a project binding")
        del schema["properties"]["project_id"]
        schema["required"] = [key for key in schema.get("required", []) if key != "project_id"]
        schema["additionalProperties"] = False
        scoped.append(tool.model_copy(update={"inputSchema": schema}))

    async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if "project_id" in arguments:
            raise ApplicationError("scope_mismatch", "Project is fixed by the service.", 403)
        bound = copy.deepcopy(arguments)
        bound["project_id"] = project_id
        # Coordinator-created evidence must not impersonate human authorship.
        for value in bound.values():
            if isinstance(value, dict) and "author" in value:
                value["author"] = "agent"
        # FastMCP's annotation omits its CallToolResult and structured tuple variants.
        result: Any = await canonical.call_tool(name, bound)
        if isinstance(result, CallToolResult):
            return result
        if isinstance(result, tuple):
            content, structured = result
            return CallToolResult(content=content, structuredContent=structured)
        if isinstance(result, dict):
            # FastMCP preserves CallToolResult as its serialized protocol object.
            if "content" in result:
                return CallToolResult.model_validate(result)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structuredContent=result,
            )
        return CallToolResult(content=list(result))

    return ScopedTools(scoped, call, parallel_reads=True)
