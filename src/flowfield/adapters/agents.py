"""Registered harnesses implement one managed-session contract."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acp.schema import HttpMcpServer
from pydantic import BaseModel

from flowfield.adapters import codex_agent, codex_install, pi_agent
from flowfield.adapters.acp_session import PermissionHandler
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.run_activity import ActivityUpdate


class Session(Protocol):
    session_id: str | None


class Agent(Protocol):
    on_activity: Callable[[ActivityUpdate], None] | None
    cleanup_confirmed: bool

    @property
    def session(self) -> Session: ...

    @property
    def process(self) -> asyncio.subprocess.Process | None: ...

    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None: ...

    async def configure(self, choice: AgentChoice) -> AgentChoice: ...

    def validate_attachments(self, attachments: list[dict[str, str]]) -> None: ...

    async def command_options(self) -> list[AgentCommand]: ...

    async def command_prompt(
        self, text: str, *, has_history: bool, attachments: list[dict[str, str]]
    ) -> str | None: ...

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]: ...

    async def stop(self) -> bool: ...

    async def close(self) -> None: ...


class HarnessInfo(BaseModel):
    id: str
    name: str
    description: str


class HarnessStatus(HarnessInfo):
    available: bool
    message: str


@dataclass(frozen=True)
class Harness:
    info: HarnessInfo
    create: Callable[[Path, Path, Mapping[str, str]], Agent]
    models: Callable[[Path], Awaitable[list[ModelOption]]]
    status: Callable[[Path, Mapping[str, str]], dict[str, Any]]
    install: Callable[[Path, Path | None, str | None], None] | None = None
    legacy_mode: str | None = None


def _install_codex(directory: Path, bundle: Path | None, sha256: str | None) -> None:
    if (bundle is None) != (sha256 is None):
        raise ApplicationError("invalid_request", "Use --bundle and --sha256 together.")
    if bundle is not None and sha256 is not None:
        codex_install.install(directory, bundle.expanduser(), sha256)
    else:
        codex_install.download_install(directory)


HARNESSES: dict[str, Harness] = {
    "codex": Harness(
        info=HarnessInfo(id="codex", name="Codex", description="Managed ACP runtime"),
        create=lambda *args: codex_agent.CodexAgent(*args),
        models=codex_agent.model_options,
        status=codex_install.status,
        install=_install_codex,
        legacy_mode="read-only",
    ),
    "pi": Harness(
        info=HarnessInfo(id="pi", name="Pi", description="Native RPC, unsandboxed host access"),
        create=lambda *args: pi_agent.PiAgent(*args),
        models=pi_agent.model_options,
        status=pi_agent.status,
    ),
}


def harness_options(directory: Path, environment: Mapping[str, str]) -> list[HarnessStatus]:
    options = []
    for registered in HARNESSES.values():
        try:
            status = registered.status(directory, environment)
            available = bool(status.get("available", False))
            message = str(status.get("message", ""))
        except (ApplicationError, OSError) as error:
            available, message = False, str(error)
        options.append(
            HarnessStatus(**registered.info.model_dump(), available=available, message=message)
        )
    return options


def get_harness(identity: str) -> Harness:
    try:
        return HARNESSES[identity]
    except KeyError as error:
        raise ApplicationError(
            "unsupported_harness", f"Unknown harness: {identity}. Choose an available harness.", 409
        ) from error


def create_agent(
    choice: AgentChoice, directory: Path, cwd: Path, environment: Mapping[str, str]
) -> Agent:
    return get_harness(choice.harness).create(directory, cwd, environment)


async def model_options(directory: Path, identity: str = "codex") -> list[ModelOption]:
    models = await get_harness(identity).models(directory)
    return [model.model_copy(update={"harness": identity}) for model in models]


async def command_options(directory: Path, cwd: Path, choice: AgentChoice) -> list[AgentCommand]:
    import os

    agent = create_agent(choice, directory, cwd, os.environ)
    try:
        async with asyncio.timeout(60):
            await agent.start([])
            await agent.configure(choice)
            return await agent.command_options()
    finally:
        await agent.close()


def install_runtime(
    identity: str,
    directory: Path,
    environment: Mapping[str, str],
    bundle: Path | None,
    sha256: str | None,
) -> dict[str, Any]:
    registered = get_harness(identity)
    if registered.install:
        registered.install(directory, bundle, sha256)
    elif bundle is not None or sha256 is not None:
        raise ApplicationError("invalid_request", "This harness does not use a Flowfield bundle.")
    result = registered.status(directory, environment)
    if registered.install is None and result.get("available") is False:
        raise ApplicationError("harness_missing", result["message"], 409)
    return result
