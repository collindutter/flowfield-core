"""Owned Codex App Server connection. Its wire IDs never become application identities."""

import asyncio
import contextlib
import json
import os
import shutil
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from flowfield.adapters.codex_activity import CodexActivity
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, Usage
from flowfield.run_activity import ActivityUpdate


def toml(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


BASE_CONFIG: dict[str, Any] = {
    "web_search": "disabled",
    "apps._default.enabled": False,
    "allow_login_shell": False,
    "shell_environment_policy.inherit": "none",
    "features.network_proxy": True,
}
for _feature in (
    "apps",
    "plugins",
    "hooks",
    "multi_agent",
    "multi_agent_v2",
    "browser_use",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "shell_snapshot",
    "remote_plugin",
    "workspace_dependencies",
    "skill_mcp_dependency_install",
):
    BASE_CONFIG[f"features.{_feature}"] = False
BASE_CONFIG["features.skip_host_skill_discovery"] = True


class CodexWorker:
    supports_activity = True

    def __init__(self, cwd: Path, config: dict[str, Any] | None = None):
        binary = shutil.which("codex")
        if not binary:
            raise ApplicationError(
                "codex_missing",
                "Codex CLI is not on the service PATH. Install it if needed, then restart "
                "Flowfield from a terminal where codex is available.",
                409,
            )
        self.binary = Path(binary).resolve()
        self.cwd, self.config = cwd, {**BASE_CONFIG, **(config or {})}
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.sequence = 0
        self.reader: asyncio.Task[None] | None = None
        self.errors: asyncio.Task[None] | None = None
        self.handlers: set[asyncio.Task[None]] = set()
        self.commands: set[str] = set()
        self.commands_done = asyncio.Event()
        self.commands_done.set()
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.completed: asyncio.Future[dict[str, Any]] | None = None
        self.on_tool: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None
        self.on_usage: Callable[[Usage], None] | None = None
        self.on_commands: Callable[[list[str]], None] | None = None
        self.on_activity: Callable[[ActivityUpdate], None] | None = None
        self.activity = CodexActivity(self._activity)
        self.last_usage = Usage()
        self.stopping = False
        self.cleanup_confirmed = True
        self.stop_lock = asyncio.Lock()

    async def start(self) -> None:
        args = [str(self.binary), "app-server", "--listen", "stdio://"]
        for key, value in self.config.items():
            args.extend(["-c", f"{key}={toml(value)}"])
        self.process = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=2_000_000,
        )
        self.reader = asyncio.create_task(self._read())
        self.errors = asyncio.create_task(self._drain_errors())
        await self.rpc(
            "initialize",
            {
                "clientInfo": {"name": "flowfield_worker", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self._send({"method": "initialized", "params": {}})

    def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.returncode is not None:
            raise ApplicationError(
                "harness_disconnected", "The worker harness disconnected; work is preserved."
            )
        self.process.stdin.write(json.dumps(message).encode() + b"\n")

    async def rpc(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30
    ) -> Any:
        self.sequence += 1
        identity = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            self._send({"id": identity, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(identity, None)

    async def _drain_errors(self) -> None:
        assert self.process and self.process.stderr
        # Do not persist harness diagnostics containing personal config/auth details.
        while await self.process.stderr.read(8192):
            pass

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message and "id" in message:
                    task = asyncio.create_task(self._request(message))
                    self.handlers.add(task)
                    task.add_done_callback(self.handlers.discard)
                elif "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                ApplicationError(
                                    "harness_error",
                                    str(message["error"].get("message", "Codex request failed")),
                                )
                            )
                        else:
                            future.set_result(message.get("result"))
                else:
                    self._event(message.get("method"), message.get("params", {}))
        except (OSError, ValueError, asyncio.CancelledError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        ApplicationError(
                            "harness_disconnected", "Codex exited before confirming the operation."
                        )
                    )
            if self.completed and not self.completed.done():
                self.completed.set_result(
                    {"status": "failed", "error": {"message": "Harness disconnected"}}
                )

    def _event(self, method: str | None, params: dict[str, Any]) -> None:
        self.activity.event(method, params, self.thread_id, self.turn_id, self.commands)
        if not self.thread_id or params.get("threadId") != self.thread_id:
            return
        if method == "turn/started":
            if self.turn_id is None:
                self.turn_id = params["turn"]["id"]
        elif method == "turn/completed" and self.completed and not self.completed.done():
            if params["turn"]["id"] == self.turn_id:
                self.completed.set_result(params["turn"])
        elif method == "thread/tokenUsage/updated":
            if not self.turn_id or params.get("turnId") != self.turn_id:
                return
            total = params.get("tokenUsage", {}).get("total", {})
            self.last_usage = Usage(
                input_tokens=total.get("inputTokens"),
                cached_input_tokens=total.get("cachedInputTokens"),
                cache_write_input_tokens=total.get("cacheWriteInputTokens"),
                output_tokens=total.get("outputTokens"),
                reasoning_output_tokens=total.get("reasoningOutputTokens"),
                total_tokens=total.get("totalTokens"),
            )
            if self.on_usage:
                self.on_usage(self.last_usage)

    def _activity(self, update: ActivityUpdate) -> None:
        if self.on_activity:
            self.on_activity(update)

    async def _request(self, message: dict[str, Any]) -> None:
        identity, params = message["id"], message.get("params", {})
        try:
            if (
                self.stopping
                or message["method"] != "item/tool/call"
                or params.get("threadId") != self.thread_id
                or params.get("turnId") != self.turn_id
                or not self.on_tool
            ):
                raise ApplicationError(
                    "worker_scope_closed", "Operation is unavailable for this attempt."
                )
            output = await self.on_tool(params["tool"], params.get("arguments", {}))
            result = {"success": True, "contentItems": [{"type": "inputText", "text": output}]}
        except Exception as error:
            result = {
                "success": False,
                "contentItems": [{"type": "inputText", "text": str(error)[:2000]}],
            }
        with contextlib.suppress(ApplicationError, BrokenPipeError):
            self._send({"id": identity, "result": result})

    async def models(self) -> list[ModelOption]:
        result: list[ModelOption] = []
        cursor = None
        while True:
            page = await self.rpc(
                "model/list",
                {"limit": 100, "includeHidden": True, **({"cursor": cursor} if cursor else {})},
            )
            result.extend(
                ModelOption(
                    id=item["model"],
                    name=item.get("displayName", item["model"]),
                    efforts=[e["reasoningEffort"] for e in item["supportedReasoningEfforts"]],
                )
                for item in page["data"]
            )
            cursor = page.get("nextCursor")
            if not cursor:
                return result

    async def command(self, script: str, *, timeout_ms: int = 60000) -> dict[str, Any]:
        if self.stopping:
            raise ApplicationError("worker_scope_closed", "The attempt is stopping.")
        process_id = uuid4().hex
        key = self.activity.key(process_id)
        self._activity(ActivityUpdate(key=key, kind="command", text=script))
        self.commands.add(process_id)
        self.commands_done.clear()
        if self.on_commands:
            self.on_commands(sorted(self.commands))
        try:
            value: dict[str, Any] = await self.rpc(
                "command/exec",
                {
                    "command": ["/bin/sh", "-c", script],
                    "cwd": str(self.cwd),
                    "permissionProfile": "flowfield_worker",
                    "processId": process_id,
                    "streamStdin": True,
                    "streamStdoutStderr": self.on_activity is not None,
                    "timeoutMs": timeout_ms,
                    "outputBytesCap": 12000,
                },
                timeout=timeout_ms / 1000 + 15,
            )
            self._activity(
                ActivityUpdate(
                    key=key,
                    kind="command",
                    text=script + "\nExit code: " + str(value.get("exitCode", "unknown")),
                )
            )
            # Streaming responses omit buffered stdout/stderr. Preserve the same bounded
            # tool result for workers and service checks while also presenting it live.
            for stream, data in self.activity.output.get(process_id, {}).items():
                value[stream] = data.decode("utf-8", errors="replace")
            return value
        except (Exception, asyncio.CancelledError):
            self._activity(
                ActivityUpdate(
                    key=key,
                    kind="command",
                    text=script + "\nCommand interrupted or failed before exit was confirmed.",
                )
            )
            try:
                await self.rpc("command/exec/terminate", {"processId": process_id})
            except Exception:
                self.cleanup_confirmed = False
            raise
        finally:
            self.activity.output.pop(process_id, None)
            if self.cleanup_confirmed:
                self.commands.discard(process_id)
            if self.on_commands:
                self.on_commands(sorted(self.commands))
            if not self.commands:
                self.commands_done.set()

    async def run(
        self, model: str, effort: str, prompt: str, tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        options = await self.models()
        if not any(item.id == model and effort in item.efforts for item in options):
            raise ApplicationError(
                "model_unavailable",
                (
                    "The selected worker model/effort is unavailable. Refresh Project "
                    "details and select explicitly."
                ),
                409,
            )
        effective = await self.rpc("config/read", {"cwd": str(self.cwd)})
        config = {**self.config, "model_reasoning_effort": effort}
        for name in effective["config"].get("mcp_servers", {}):
            config[f"mcp_servers.{name}.enabled"] = False
        thread = await self.rpc(
            "thread/start",
            {
                "model": model,
                "allowProviderModelFallback": False,
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "permissions": "flowfield_worker",
                "config": config,
                "environments": [],
                "selectedCapabilityRoots": [],
                "dynamicTools": tools,
                "developerInstructions": (
                    "You are a service-managed worker following the supplied assignment mode. "
                    "Keep the worker role even when repository guidance addresses coordinators. "
                    "The supplied tools are your complete interface. Use run_command for local "
                    "commands and read_context for frozen assignment evidence. Read repository "
                    "AGENTS.md at the checkout root. The assignment determines whether this is "
                    "implementation or read-only discussion; a discussion never authorizes edits, "
                    "dependency installation or tests. Never change global tools, credentials, "
                    "Git history or system services; access other projects; launch agents; or "
                    "operate the Flowfield CLI. Follow the assignment's scope and context policy. "
                    "Finish through submit_result with an honest complete/partial outcome. "
                    "Reported findings never grant human approval or authorize integration."
                ),
            },
        )
        if (
            thread["model"] != model
            or thread["reasoningEffort"] != effort
            or thread.get("activePermissionProfile", {}).get("id") != "flowfield_worker"
        ):
            raise ApplicationError(
                "harness_configuration_changed",
                (
                    "Codex did not honor the requested model, effort or permissions; "
                    "no turn was launched."
                ),
            )
        self.thread_id = thread["thread"]["id"]
        self.completed = asyncio.get_running_loop().create_future()
        turn = await self.rpc(
            "turn/start",
            {
                "threadId": self.thread_id,
                "model": model,
                "effort": effort,
                "permissions": "flowfield_worker",
                "input": [{"type": "text", "text": prompt}],
            },
        )
        self.turn_id = turn["turn"]["id"]
        result = await self.completed
        self.last_usage.complete = result.get("status") == "completed"
        if self.on_usage:
            self.on_usage(self.last_usage)
        return result

    async def stop(self) -> bool:
        async with self.stop_lock:
            return await self._stop()

    async def _stop(self) -> bool:
        self.stopping = True
        if self.thread_id and self.turn_id:
            with contextlib.suppress(Exception):
                await self.rpc(
                    "turn/interrupt",
                    {"threadId": self.thread_id, "turnId": self.turn_id},
                    timeout=10,
                )
        for process_id in list(self.commands):
            try:
                await self.rpc("command/exec/terminate", {"processId": process_id}, timeout=10)
            except Exception:
                if process_id in self.commands:
                    self.cleanup_confirmed = False
        if self.commands:
            try:
                await asyncio.wait_for(self.commands_done.wait(), 15)
            except TimeoutError:
                self.cleanup_confirmed = False
        if self.handlers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(self.handlers), return_exceptions=True), 15
                )
            except TimeoutError:
                self.cleanup_confirmed = False
        await self.close()
        return self.cleanup_confirmed and not self.commands

    async def close(self) -> None:
        process = self.process
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        for task in (self.reader, self.errors):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
