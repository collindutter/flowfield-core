"""Probe an installed Codex ACP bridge with a fake app-server, never a model.

Usage: python scripts/check_codex_acp.py /absolute/path/to/codex-acp/dist/index.js
Requires Node only for this explicit development probe, not Flowfield at runtime.
The probe reports native configuration and lifecycle forwarding. Use --scenario
cancel or slow-shutdown to test interruption and forced native exit. It does not
enable managed work or establish filesystem/process enforcement.
"""

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.codex_cleanup import quiesce, require_cleanup
from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost


async def probe(
    bridge: Path,
    scratch: Path,
    node: str,
    mode: str,
    scenario: str = "complete",
    cleanup: bool = False,
) -> dict:
    fake = Path(__file__).resolve().parents[1] / "tests/fake_acp_app_server.py"
    launcher = scratch / "codex"
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} "$@"\n'
    )
    launcher.chmod(0o700)
    record = scratch / "rpc.jsonl"
    home = scratch / "home"
    home.mkdir()
    repo = scratch / "project"
    repo.mkdir()
    git(repo, "init")
    git(
        repo,
        "-c",
        "user.name=Probe",
        "-c",
        "user.email=probe@invalid",
        "commit",
        "--allow-empty",
        "-m",
        "baseline",
    )
    host = LocalHost(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "CODEX_PATH": str(launcher),
            "PATH": os.path.dirname(node),
            "FLOWFIELD_TEST_RPC_RECORD": str(record),
            "FLOWFIELD_TEST_SCENARIO": scenario,
        }
    )
    attempt = host.prepare(scratch / "state", repo, "probe", baseline(repo))
    events = []
    started = asyncio.Event()

    def event(value):
        events.append(value)
        if value.kind == "text":
            started.set()

    client = AcpSession(
        event, request_timeout=10, turn_timeout=10, cleanup=quiesce if cleanup else None
    )
    await client.start(
        [node, str(bridge)],
        cwd=attempt.workspace.checkout,
        mcp_servers=[],
        env=attempt.launch_environment(),
    )
    try:
        if cleanup:
            require_cleanup(client.capabilities)
        await client.select("model", "test-model")
        await client.select("reasoning_effort", "low")
        await client.select("mode", mode)
        prompt = asyncio.create_task(client.prompt("Offline conformance probe"))
        if scenario == "cancel":
            await asyncio.wait_for(started.wait(), 10)
            await client.close(timeout=15)
        result = await prompt
    finally:
        stopped = await client.close(timeout=15)
    if cleanup and stopped.owned_work_stopped is not (scenario != "cleanup-refused"):
        raise AssertionError("Unexpected managed cleanup receipt")
    messages = [json.loads(line) for line in record.read_text().splitlines()]
    thread = next(item["params"] for item in messages if item.get("method") == "thread/start")
    turn = next(item["params"] for item in messages if item.get("method") == "turn/start")
    return {
        "prompt_stop_reason": result,
        "text_streamed": any(event.kind == "text" for event in events),
        "process_group_exited": stopped.process_group_exited,
        "session_closed": stopped.session_closed,
        "bridge_exited_gracefully": stopped.process_exited_gracefully,
        "native_work_stopped": stopped.owned_work_stopped,
        "fake_native_cleanup_completed": any(
            item.get("probe_native_cleanup_completed") for item in messages
        ),
        "native_lifecycle_requests": [
            item["method"]
            for item in messages
            if item.get("method")
            in {
                "turn/interrupt",
                "thread/unsubscribe",
                "thread/archive",
                "thread/backgroundTerminals/list",
                "thread/backgroundTerminals/terminate",
            }
        ],
        "scenario": scenario,
        "selected_mode": mode,
        "attempt_cwd_forwarded": thread.get("cwd") == str(attempt.workspace.checkout),
        "native_tools_disabled": thread.get("environments") == [],
        "turn_sandbox_policy": turn.get("sandboxPolicy"),
        "turn_approval_policy": turn.get("approvalPolicy"),
        "turn_approval_reviewer": turn.get("approvalsReviewer"),
        "turn_reasoning_effort": turn.get("effort"),
        "limits": (
            "Fake app-server only; filesystem isolation and detached command cleanup are unproven."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    parser.add_argument(
        "--mode", default="workspace-write", choices=["workspace-write", "read-only", "agent"]
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Require the Flowfield native cleanup extension"
    )
    parser.add_argument(
        "--scenario",
        default="complete",
        choices=["complete", "cancel", "slow-shutdown", "background", "cleanup-refused"],
    )
    args = parser.parse_args()
    node = shutil.which("node")
    if node is None or not args.bridge.is_file():
        parser.error("An installed Node executable and bridge file are required")
    with tempfile.TemporaryDirectory(prefix="flowfield-acp-probe-") as directory:
        print(
            json.dumps(
                asyncio.run(
                    probe(
                        args.bridge.resolve(),
                        Path(directory),
                        node,
                        args.mode,
                        args.scenario,
                        args.cleanup,
                    )
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
