"""Codex-specific permission translation and measured worker boundary."""

import asyncio
import shlex
from pathlib import Path
from uuid import uuid4

from flowfield.adapters.codex_worker import CodexWorker
from flowfield.adapters.local_environment import LocalEnvironment
from flowfield.adapters.toolchain import readable_tools
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult


def permission_profile(environment: LocalEnvironment, codex: Path) -> dict[str, object]:
    return {
        "extends": ":workspace",
        "filesystem": {
            ":root": "deny",
            ":minimal": "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(environment.checkout): "write",
            str(environment.runtime): "write",
            str(environment.common_git): "read",
            str(codex.parent.parent): "read",
            str(environment.python_runtime): "read",
            "/System/Library/OpenSSL": "read",
            "/etc/ssl/cert.pem": "read",
            "/etc/ssl/openssl.cnf": "read",
            **readable_tools(environment.runtime),
        },
        "network": {
            "enabled": True,
            "domains": {"*": "allow"},
            "allow_local_binding": False,
        },
    }


async def preflight(
    client: CodexWorker, environment: LocalEnvironment, state: Path, projects: list[Path]
) -> None:
    # Only disposable canaries are read; never read a real credential or database as a test.
    canary = state / f".worker-boundary-{uuid4().hex}"
    canary.write_text("FAKE STATE CANARY")
    alias = environment.runtime / "outside-state"
    alias.symlink_to(canary)
    contacted = False

    async def local_probe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal contacted
        contacted = True
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(local_probe, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        q = shlex.quote
        checks = [
            (f"printf ok > {q(str(environment.runtime / 'tmp' / 'probe'))}", True),
            (f"cat {q(str(canary))} >/dev/null", False),
            (f"cat {q(str(alias))} >/dev/null", False),
            (f"printf bad > {q(str(canary))}", False),
            ("git status --porcelain", True),
            ("python -c 'import sys; assert sys.version_info >= (3, 12)'", True),
            # Require an active network proxy, not merely configured domain rules.
            ('test -n "$HTTPS_PROXY$https_proxy"', True),
            (f"/usr/bin/curl -fsS --max-time 2 http://127.0.0.1:{port}/", False),
            (f"/usr/bin/curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:{port}/", False),
        ]
        for command, allowed in checks:
            result = await client.command(command, timeout_ms=10000)
            if (result["exitCode"] == 0) != allowed:
                raise ApplicationError(
                    "unsafe_local_environment",
                    (
                        "The local worker boundary failed. Keep Flowfield state and "
                        "protected projects outside shared temporary folders and check the"
                        " installed Codex permissions."
                    ),
                )
        if contacted:
            raise ApplicationError(
                "unsafe_local_environment",
                "Worker networking could reach a protected local service; launch stopped.",
            )
        # This measured build grants shared /tmp independently of named denials.
        for path in [
            state,
            *projects,
        ]:
            if any(
                path.is_relative_to(root)
                for root in (
                    Path("/private/tmp"),
                    Path("/tmp").resolve(),
                    Path("/private/var/tmp"),
                )
            ):
                raise ApplicationError(
                    "unsafe_local_environment",
                    (
                        "A registered project or state directory is in shared temporary "
                        "storage. Use a persistent local location before enabling managed "
                        "workers."
                    ),
                )
    finally:
        server.close()
        await server.wait_closed()
        alias.unlink(missing_ok=True)
        canary.unlink(missing_ok=True)


def configuration(environment: LocalEnvironment, binary: Path) -> dict[str, object]:
    return {
        "default_permissions": "flowfield_worker",
        "permissions.flowfield_worker": permission_profile(environment, binary),
        "shell_environment_policy.set": environment.shell_environment(),
    }


async def run_checks(client: CodexWorker, commands: list[str], timeout: int) -> list[CheckResult]:
    reports = []
    for command in commands:
        result = await client.command(command, timeout_ms=timeout * 1000)
        output = result.get("stdout", "") + result.get("stderr", "")
        reports.append(
            CheckResult(
                command=command,
                exit_code=result["exitCode"],
                output=output[:12000],
                truncated=len(output) >= 12000,
            )
        )
        if result["exitCode"]:
            break
    return reports
