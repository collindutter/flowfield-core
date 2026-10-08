"""Installed Earendil Pi's native JSONL RPC, with only turn-scoped MCP.

Pi has unsandboxed host access, not approval or sandbox modes. Managed launches
isolate executable configuration without replacing native credentials/registry.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from acp.schema import HttpMcpServer

from flowfield.adapters.acp_session import PermissionHandler
from flowfield.adapters.local_process import LocalLaunchCancelled, LocalProcess
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.run_activity import ActivityUpdate, ContextUsage, Kind

FULL_ACCESS = NativeMode(
    id="full-access",
    name="Full access (unsandboxed host)",
    description="Pi can read, write and execute on the host without sandboxing or tool approvals.",
)
RPC_TIMEOUT = 30
STOP_TIMEOUT = 5
MAX_RECORD = 16 * 1024 * 1024
COMMANDS = [
    AgentCommand(name="compact", description="Compact this native Pi conversation."),
    AgentCommand(name="status", description="Show native Pi model and context status."),
]


def status(directory: Path, environment: Mapping[str, str]) -> dict[str, Any]:
    """Preflight only; installation and authentication remain native Pi operations."""
    binary = shutil.which(environment.get("PI_PATH") or "pi", path=environment.get("PATH", ""))
    if binary is None:
        raise ApplicationError(
            "pi_missing", "Install Earendil Pi on the service PATH or set PI_PATH.", 409
        )
    return {
        "harness": "pi",
        "available": True,
        "executable": str(Path(binary).absolute()),
        "pi": binary,
        "pi_available": True,
        "message": "Installed native Pi RPC; full unsandboxed host access (no tool approvals).",
    }


@dataclass
class PiSession:
    session_id: str | None = None


class PiAgent:
    supports_activity = True

    def __init__(self, directory: Path, cwd: Path, environment: Mapping[str, str]):
        self.command = [status(directory, environment)["executable"], "--mode", "rpc"]
        self.directory = directory.resolve()
        self.cwd = cwd
        self.environment = dict(environment)
        self.session = PiSession()
        self.on_activity: Callable[[ActivityUpdate], None] | None = None
        self.cleanup_confirmed = True
        self.stopping = False
        self._owner: LocalProcess | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._counter = 0
        self._settled: asyncio.Future[dict[str, Any]] | None = None
        self._prompt_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[bool] | None = None
        self._failure: str | None = None
        self._fatal: str | None = None
        self._model: dict[str, Any] = {}
        self._message = 0
        self._tools: dict[str, str] = {}
        self._prompt_sent = False
        self._session_file: Path | None = None

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._owner.process if self._owner else None

    def _launch(self, servers: list[HttpMcpServer]) -> tuple[list[str], dict[str, str]]:
        self._temporary = tempfile.TemporaryDirectory(prefix="flowfield-pi-")
        overlay = Path(self._temporary.name)
        native = (
            Path(
                self.environment.get("PI_CODING_AGENT_DIR")
                or str(Path(self.environment.get("HOME", str(Path.home()))) / ".pi/agent")
            )
            .expanduser()
            .resolve()
        )
        # No ambient settings, packages, MCP, extensions or system-prompt overrides.
        # Native OAuth refreshes may update auth.json through the symlink.
        for name in (
            "auth.json",
            "models.json",
            "AGENTS.override.md",
            "AGENTS.md",
            "AGENTS.MD",
            "CLAUDE.md",
            "CLAUDE.MD",
            "skills",
        ):
            if (native / name).exists() and not (overlay / name).exists():
                (overlay / name).symlink_to(native / name)
        (overlay / "settings.json").write_text(
            json.dumps(
                {
                    "cacheWarming": "off",
                    "enableInstallTelemetry": False,
                    "enableAnalytics": False,
                }
            )
        )
        env = dict(self.environment)
        env.update(PI_CODING_AGENT_DIR=str(overlay), PI_SKIP_VERSION_CHECK="1", PI_OFFLINE="1")
        command = [
            *self.command,
            "--no-extensions",
            "--no-approve",
            "--no-themes",
            "--no-prompt-templates",
            "--tools",
            "read,bash,edit,write,grep,find,ls,mcp__*",
            "--append-system-prompt",
            "Managed Flowfield work: do not daemonize, detach, or leave background jobs running. "
            "All processes you start must finish within the current turn.",
        ]
        if servers:
            # A fresh extension file is not persisted in the native session. Credentials
            # live only in this private temporary directory, never argv or metadata.
            registrations = []
            for server in servers:
                name = server.name
                config = {
                    "url": server.url,
                    "headers": {header.name: header.value for header in server.headers},
                    "exposure": "direct",
                }
                registrations.append(
                    f"pi.registerMcpServer({json.dumps(name)}, {json.dumps(config)});"
                )
            extension = overlay / "scoped-mcp.ts"
            extension.write_text(
                "export default function (pi) {\n" + "\n".join(registrations) + "\n}\n"
            )
            extension.chmod(0o600)
            command.extend(["-e", "builtin:mcp", "-e", str(extension)])
        else:
            command.append("--no-mcp")
        return command, env

    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        if self._owner is not None or self.stopping:
            raise ApplicationError("pi_start_failed", "Pi cannot be started twice.", 409)
        self.cleanup_confirmed = False
        try:
            command, env = self._launch(servers)
            if persistent or resume:
                root = self.directory / "harnesses/pi/sessions"
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                identity = str(UUID(resume)) if resume else str(uuid4())
                path = root / (identity + ".jsonl")
                if resume:
                    if identity != resume or not path.is_file() or path.is_symlink():
                        raise ValueError("Saved native session missing")
                    with path.open() as source:
                        header = json.loads(source.readline(MAX_RECORD))
                        if (
                            not isinstance(header, dict)
                            or header.get("type") != "session"
                            or header.get("id") != identity
                        ):
                            raise ValueError("Saved native session header mismatch")
                        # Native Pi skips malformed lines. Reject damaged/truncated
                        # history rather than silently resuming a shortened branch.
                        while line := source.readline(MAX_RECORD):
                            if not line.endswith("\n"):
                                raise ValueError("Incomplete native session record")
                            if line.strip():
                                entry = json.loads(line)
                                if not isinstance(entry, dict) or not isinstance(
                                    entry.get("type"), str
                                ):
                                    raise ValueError("Invalid native session record")
                else:
                    # Pi normally writes lazily on the first conversation message. A
                    # native v3 header makes even an empty saved session resumable.
                    with path.open("x") as output:
                        path.chmod(0o600)
                        output.write(
                            json.dumps(
                                {
                                    "type": "session",
                                    "version": 3,
                                    "id": identity,
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "cwd": str(self.cwd),
                                }
                            )
                            + "\n"
                        )
                self._session_file = path
                command.extend(["--session-dir", str(root), "--session", str(path)])
            else:
                command.append("--no-session")
            try:
                self._owner = await LocalProcess.start(
                    command, cwd=self.cwd, env=env, limit=MAX_RECORD
                )
            except LocalLaunchCancelled as error:
                self._owner = error.owner
                raise
            self._readers = [
                asyncio.create_task(self._read_stdout()),
                asyncio.create_task(self._drain_stderr()),
            ]
            state = await self._rpc("get_state")
            native_identity = state.get("sessionId")
            if not isinstance(native_identity, str) or not native_identity:
                raise ValueError("Pi omitted native session ID")
            if self._session_file and (
                native_identity != self._session_file.stem
                or Path(state.get("sessionFile", "")).resolve() != self._session_file
            ):
                raise ValueError("Pi did not open the exact saved session")
            self.session.session_id = native_identity
            self._model = state.get("model") or {}
        except asyncio.CancelledError:
            await self.stop()
            raise
        except (OSError, ValueError, RuntimeError, TimeoutError, ApplicationError) as error:
            await self.stop()
            raise ApplicationError(
                "agent_resume_failed" if resume else "pi_start_failed",
                "The saved Pi session could not be resumed. Nothing was replayed."
                if resume
                else "Pi could not open native RPC. Check its installation and login.",
                409,
            ) from error

    async def _write(self, record: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise RuntimeError("Pi RPC is not running")
        process.stdin.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
        await process.stdin.drain()

    async def _rpc(self, kind: str, *, timeout: float = RPC_TIMEOUT, **data: Any) -> dict[str, Any]:
        if self._fatal:
            raise RuntimeError(self._fatal)
        self._counter += 1
        identity = str(self._counter)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[identity] = future
        try:
            async with asyncio.timeout(timeout):
                await self._write({"id": identity, "type": kind, **data})
                response = await future
            if response.get("command") != kind or not response.get("success"):
                raise RuntimeError("Pi rejected " + kind)
            if self._fatal:
                raise RuntimeError(self._fatal)
            result = response.get("data")
            return result if isinstance(result, dict) else {}
        finally:
            self._pending.pop(identity, None)

    async def _drain_stderr(self) -> None:
        process = self.process
        assert process and process.stderr
        # Diagnostics can contain credentials/prompts; drain without retention.
        while await process.stderr.read(65536):
            pass

    async def _read_stdout(self) -> None:
        process = self.process
        assert process and process.stdout
        try:
            while line := await process.stdout.readline():
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Invalid Pi RPC record")
                if event.get("type") == "response":
                    future = self._pending.get(event.get("id", ""))
                    if future and not future.done():
                        future.set_result(event)
                elif event.get("type") == "extension_ui_request":
                    if event.get("method") in {"select", "confirm", "input", "editor"}:
                        await self._write(
                            {"type": "extension_ui_response", "id": event["id"], "cancelled": True}
                        )
                else:
                    self._event(event)
        except (ValueError, OSError, RuntimeError, TypeError, KeyError, AttributeError) as error:
            self._fatal = "Pi RPC stream failed: " + type(error).__name__
        finally:
            self._fatal = self._fatal or "Pi RPC closed"
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(self._fatal))
            if self._settled and not self._settled.done():
                self._settled.set_exception(RuntimeError(self._fatal))

    def _activity(self, key: str, kind: Kind, text: str, *, append: bool = False) -> None:
        if self.on_activity:
            self.on_activity(
                ActivityUpdate(
                    key=key, kind=kind, text=text[:6000], append=append, omitted=len(text) > 6000
                )
            )

    def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "message_start" and event.get("message", {}).get("role") == "assistant":
            self._message += 1
        elif kind == "message_update":
            update = event.get("assistantMessageEvent", {})
            key = f"pi-text-{self._message}-{update.get('contentIndex', 0)}"
            if update.get("type") == "text_delta":
                self._activity(key, "agent", update.get("delta", ""), append=True)
            elif update.get("type") == "text_end":
                self._activity(key, "agent", update.get("content", ""))
            # Never project thinking, signatures, tool arguments or partial results.
        elif kind == "message_end":
            message = event.get("message", {})
            if message.get("role") == "assistant":
                self._failure = message.get("stopReason")
                for index, block in enumerate(message.get("content", [])):
                    if block.get("type") == "text":
                        self._activity(f"pi-text-{self._message}-{index}", "agent", block["text"])
        elif kind in {"tool_execution_start", "tool_execution_end"}:
            identity = str(event.get("toolCallId", "tool"))
            name = str(event.get("toolName", "Tool"))
            if kind == "tool_execution_start":
                self._tools[identity] = name
                state = "running"
            else:
                self._tools.pop(identity, None)
                state = "failed" if event.get("isError") else "completed"
            self._activity(
                hashlib.sha256(identity.encode()).hexdigest(),
                "command" if name == "bash" else "tool",
                f"{name} · {state}",
            )
        elif kind == "agent_settled" and self._settled and not self._settled.done():
            self._settled.set_result(event)
        elif kind == "extension_error":
            self._fatal = "Pi scoped extension failed"
            if self._settled and not self._settled.done():
                self._settled.set_exception(RuntimeError(self._fatal))
        elif kind == "auto_retry_start":
            self._activity("pi-retry", "status", "Pi retrying provider request")
        elif kind == "compaction_start":
            self._activity("pi-compaction", "status", "Pi compacting context")

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping:
            raise ApplicationError("agent_stopping", "The agent is stopping.", 409)
        try:
            if choice.harness != "pi" or choice.mode != FULL_ACCESS.id or choice.fast:
                raise ValueError("Pi requires explicit unsandboxed full access; no fast mode")
            provider, model = choice.model.split("/", 1)
            await self._rpc("set_model", provider=provider, modelId=model)
            levels = (await self._rpc("get_available_thinking_levels"))["levels"]
            if choice.effort not in levels:
                raise ValueError("Unsupported native effort")
            await self._rpc("set_thinking_level", level=choice.effort)
            state = await self._rpc("get_state")
            self._model = state.get("model") or {}
            if (
                self._model.get("provider") != provider
                or self._model.get("id") != model
                or state.get("thinkingLevel") != choice.effort
            ):
                raise ValueError("Native settings did not match")
            return choice.model_copy(update={"fast": False})
        except (ValueError, KeyError, RuntimeError, OSError, TimeoutError) as error:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Pi could not apply the saved model, effort or access. "
                "Reload available choices and save settings.",
                409,
            ) from error

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None:
        if any(item["mime"].startswith("image/") for item in attachments) and (
            "image" not in self._model.get("input", [])
        ):
            raise ApplicationError(
                "images_unavailable", "This Pi model cannot receive images.", 409
            )

    async def command_options(self) -> list[AgentCommand]:
        return list(COMMANDS)

    async def command_prompt(
        self, text: str, *, has_history: bool, attachments: list[dict[str, str]]
    ) -> str | None:
        words = text.strip().split(maxsplit=1)
        if not words or not words[0].startswith("/"):
            return None
        name = words[0][1:]
        if name not in {command.name for command in COMMANDS}:
            raise ApplicationError("command_unavailable", "Choose a command from the menu.", 409)
        if attachments or len(words) != 1:
            raise ApplicationError("command_input", "No arguments or attachments supported.", 409)
        if name == "compact" and not has_history:
            raise ApplicationError("command_session", "Send a message before compacting.", 409)
        return "/" + name

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        async with self._prompt_lock:
            if self.stopping:
                return {"status": "stopped"}
            self.validate_attachments(attachments or [])
            self._failure = None
            self._settled = asyncio.get_running_loop().create_future()
            try:
                if text == "/compact":
                    self._prompt_sent = True
                    await self._rpc("compact", timeout=300)
                elif text == "/status":
                    state = await self._rpc("get_state")
                    model = state.get("model") or {}
                    self._activity(
                        "pi-status",
                        "status",
                        f"Pi {model.get('provider', '')}/"
                        f"{model.get('id', '')} · {state.get('thinkingLevel', '')}",
                    )
                else:
                    images = []
                    for item in attachments or []:
                        text += f"\n\nHuman attachment: {item['name']}\n"
                        if item["mime"].startswith("image/"):
                            images.append(
                                {"type": "image", "data": item["data"], "mimeType": item["mime"]}
                            )
                        else:
                            text += base64.b64decode(item["data"]).decode("utf-8")
                    self._prompt_sent = True
                    accepted = await self._rpc("prompt", message=text, images=images)
                    if accepted.get("disposition") != "handled":
                        settled = await self._settled
                        if settled.get("aborted") or self._failure == "aborted":
                            return {"status": "stopped"}
                        if self._failure not in {"stop", "toolUse"}:
                            raise RuntimeError("Pi did not finish successfully")
                await self._context()
                return {"status": "completed"}
            except asyncio.CancelledError:
                await self.stop()
                raise
            except (OSError, RuntimeError, TimeoutError) as error:
                if self.stopping:
                    return {"status": "stopped"}
                raise ApplicationError(
                    "pi_prompt_failed",
                    "Pi could not complete this turn. Check native login "
                    "and provider availability before retrying.",
                    409,
                ) from error
            finally:
                # Retrieve a stream exception even when command rejection won the race.
                if self._settled.done() and not self._settled.cancelled():
                    self._settled.exception()
                self._settled = None

    async def _context(self) -> None:
        stats = await self._rpc("get_session_stats")
        usage = stats.get("contextUsage") or {}
        used, size = usage.get("tokens"), usage.get("contextWindow")
        if self.on_activity and type(used) is int and type(size) is int and used >= 0 and size > 0:
            self.on_activity(
                ActivityUpdate(
                    key="context",
                    kind="status",
                    text="",
                    context=ContextUsage(used=used, size=size),
                )
            )

    async def stop(self) -> bool:
        self.stopping = True
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop())
        # Caller cancellation cannot abandon an owned process or its scoped credentials.
        return await asyncio.shield(self._stop_task)

    async def _stop(self) -> bool:
        idle = False
        orderly = False
        if self._owner:
            process = self._owner.process
            try:
                await self._rpc("clear_queue", timeout=STOP_TIMEOUT)
                await self._rpc("abort", timeout=STOP_TIMEOUT)
                idle = True
                if process.stdin:
                    process.stdin.close()
                async with asyncio.timeout(STOP_TIMEOUT):
                    await process.wait()
                orderly = process.returncode == 0
            except (OSError, RuntimeError, TimeoutError):
                pass
            exited = await self._owner.close(timeout=STOP_TIMEOUT)
            # Native bash uses its own process groups. A killed RPC leader is not
            # a receipt for those groups. Only native idle + orderly disposal counts.
            self.cleanup_confirmed = exited and (
                (idle and orderly and not self._tools) or not self._prompt_sent
            )
        else:
            self.cleanup_confirmed = True
        for task in self._readers:
            task.cancel()
        for task in self._readers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        return self.cleanup_confirmed

    async def close(self) -> None:
        await self.stop()


async def model_options(directory: Path) -> list[ModelOption]:
    with tempfile.TemporaryDirectory(prefix="flowfield-pi-catalog-") as temporary:
        agent = PiAgent(directory, Path(temporary).resolve(), os.environ)
        try:
            async with asyncio.timeout(120):
                await agent.start([])
                models = (await agent._rpc("get_available_models"))["models"]
                if len(models) > 512:
                    raise ApplicationError(
                        "agent_catalog_limit", "Pi returned too many models.", 409
                    )
                result = []
                for model in models:
                    await agent._rpc("set_model", provider=model["provider"], modelId=model["id"])
                    efforts = (await agent._rpc("get_available_thinking_levels"))["levels"]
                    result.append(
                        ModelOption(
                            harness="pi",
                            id=f"{model['provider']}/{model['id']}",
                            name=model["name"],
                            efforts=efforts,
                            modes=[FULL_ACCESS],
                            fast=False,
                        )
                    )
                return result
        except (KeyError, OSError, RuntimeError, TimeoutError) as error:
            raise ApplicationError(
                "pi_catalog_failed", "Pi could not discover native models.", 409
            ) from error
        finally:
            await agent.close()


async def command_options(directory: Path, cwd: Path, choice: AgentChoice) -> list[AgentCommand]:
    agent = PiAgent(directory, cwd, os.environ)
    try:
        async with asyncio.timeout(60):
            await agent.start([])
            await agent.configure(choice)
            return await agent.command_options()
    finally:
        await agent.close()
