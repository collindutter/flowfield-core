"""Native Codex MCP configuration and read-only readiness checks."""

import json
import shutil
import subprocess
import tempfile
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from flowfield.errors import ApplicationError


class CodexConnection:
    def __init__(self, port: int, name: str = "flowfield"):
        self.url = f"http://127.0.0.1:{port}/mcp/"
        self.name = name
        executable = shutil.which("codex")
        if executable is None:
            raise ApplicationError(
                "codex_missing", "Install Codex CLI and make codex available on PATH, then retry."
            )
        self.executable = executable

    def run(self, *args: str) -> str:
        # Configure the user layer, independent of the invoking project's overrides.
        with tempfile.TemporaryDirectory(prefix="flowfield-codex-") as directory:
            try:
                result = subprocess.run(
                    [self.executable, *args],
                    cwd=directory,
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ApplicationError(
                    "codex_unavailable",
                    "Codex CLI could not run. Check its installation and retry.",
                ) from error
        if result.returncode:
            # Harness diagnostics can contain personal configuration; do not echo them.
            raise ApplicationError(
                "codex_command_failed",
                "Codex configuration command failed. Run codex mcp list to diagnose; "
                "update Codex if its MCP commands are unavailable.",
            )
        return result.stdout

    def configuration(self) -> dict[str, Any] | None:
        try:
            servers = json.loads(self.run("mcp", "list", "--json"))
            if not isinstance(servers, list) or any(not isinstance(s, dict) for s in servers):
                raise ValueError("Unexpected configuration")
            return next((s for s in servers if s.get("name") == self.name), None)
        except (ValueError, TypeError) as error:
            raise ApplicationError(
                "codex_response_invalid",
                "Cannot read Codex MCP configuration. Update Codex CLI and retry.",
            ) from error

    def matches(self, config: dict[str, Any]) -> bool:
        transport = config.get("transport", {})
        return (
            isinstance(transport, dict)
            and transport.get("type") == "streamable_http"
            and transport.get("url") == self.url
        )

    def conflict(self) -> ApplicationError:
        return ApplicationError(
            "connection_conflict",
            f"Codex already has a different server named {self.name}. "
            f"Use --name with another name, or inspect codex mcp get {self.name}. "
            "Existing settings were preserved.",
        )

    def connect(self) -> dict[str, Any]:
        config = self.configuration()
        if config is not None and not self.matches(config):
            raise self.conflict()
        # Prove the intended service is usable before changing harness configuration.
        tools = anyio.run(probe, self.url)
        changed = config is None
        if changed:
            self.run("mcp", "add", self.name, "--url", self.url)
        config = self.configuration()
        if config is None or not self.matches(config):
            raise ApplicationError(
                "connection_not_saved",
                "Codex did not retain the requested connection. Inspect codex mcp list and retry.",
            )
        return self.report(config, tools, changed=changed)

    def report(
        self, config: dict[str, Any], tools: list[str], *, changed: bool = False
    ) -> dict[str, Any]:
        enabled = config.get("enabled", True)
        return {
            "harness": "codex",
            "name": self.name,
            "url": self.url,
            "configured": True,
            "enabled": enabled,
            "service": "reachable",
            "tools": tools,
            "changed": changed,
            "client": "not_observed",
            "message": (
                "Connection saved. Restart Codex, then check /mcp."
                if changed
                else "Connection is configured but disabled in Codex. Enable it in Codex settings."
                if not enabled
                else "Connection is configured and the service is ready. "
                "If this client has not loaded it, restart Codex and check /mcp."
            ),
        }

    def doctor(self) -> dict[str, Any]:
        config = self.configuration()
        if config is None:
            raise ApplicationError(
                "not_connected",
                "No connection configured. Run flowfield integration connect codex "
                "with the same --port and --name.",
            )
        if not self.matches(config):
            raise self.conflict()
        return self.report(config, anyio.run(probe, self.url))

    def disconnect(self) -> dict[str, Any]:
        config = self.configuration()
        if config is not None:
            if not self.matches(config):
                raise self.conflict()
            self.run("mcp", "remove", self.name)
            if self.configuration() is not None:
                raise ApplicationError(
                    "connection_not_removed",
                    "Codex still reports this connection. Inspect codex mcp list.",
                )
        return {
            "harness": "codex",
            "name": self.name,
            "configured": False,
            "changed": config is not None,
            "message": "Connection removed. Restart Codex to unload its tools. "
            "Projects and tasks are retained."
            if config
            else "Connection is already absent. Projects and tasks are retained.",
        }


async def probe(url: str) -> list[str]:
    try:
        with anyio.fail_after(10):
            async with (
                httpx.AsyncClient(trust_env=False, follow_redirects=False) as http,
                streamable_http_client(url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                initialized = await session.initialize()
                if initialized.serverInfo.name != "Flowfield":
                    raise ValueError("Wrong server")
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                if not {
                    "list_projects",
                    "get_task",
                    "create_task",
                    "edit_task",
                    "get_board",
                    "prioritize_task",
                    "review_result",
                }.issubset(tools):
                    raise ValueError("Missing Flowfield tools")
                return tools
    except Exception as error:
        raise ApplicationError(
            "mcp_unavailable",
            f"Cannot verify Flowfield MCP at {url}. "
            "Start flowfield serve on this port, then retry. No model call is needed.",
        ) from error
