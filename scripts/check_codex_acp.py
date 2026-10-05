"""Probe an installed Codex ACP bridge with a fake app-server, never a model.

Usage: python scripts/check_codex_acp.py /absolute/path/to/codex-acp/dist/index.js
Requires Node only for this explicit development probe, not Flowfield at runtime.
The probe reports enforcement gaps; it does not enable the bridge for managed work.
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


async def probe(bridge: Path, scratch: Path, node: str) -> dict:
    fake = Path(__file__).resolve().parents[1] / "tests/fake_acp_app_server.py"
    launcher = scratch / "codex"
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} "$@"\n'
    )
    launcher.chmod(0o700)
    record = scratch / "rpc.jsonl"
    home = scratch / "home"
    home.mkdir()
    events = []
    client = AcpSession(events.append, request_timeout=10, turn_timeout=10)
    await client.start(
        [node, str(bridge)],
        cwd=scratch,
        mcp_servers=[],
        env={
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "CODEX_PATH": str(launcher),
            "PATH": os.path.dirname(node),
            "FLOWFIELD_TEST_RPC_RECORD": str(record),
            "CODEX_CONFIG": json.dumps(
                {
                    "default_permissions": "flowfield_worker",
                    "approval_policy": "never",
                    "model": "test-model",
                }
            ),
        },
    )
    try:
        await client.select("model", "test-model")
        result = await client.prompt("Offline conformance probe")
    finally:
        stopped = await client.close()
    messages = [json.loads(line) for line in record.read_text().splitlines()]
    thread = next(item["params"] for item in messages if item.get("method") == "thread/start")
    turn = next(item["params"] for item in messages if item.get("method") == "turn/start")
    return {
        "prompt_stop_reason": result,
        "text_streamed": any(event.kind == "text" for event in events),
        "process_group_exited": stopped.process_group_exited,
        "configured_permission_profile": thread.get("config", {}).get("default_permissions"),
        "explicit_thread_permissions": thread.get("permissions"),
        "turn_sandbox_policy": turn.get("sandboxPolicy"),
        "turn_approval_policy": turn.get("approvalPolicy"),
        "turn_approval_reviewer": turn.get("approvalsReviewer"),
        "worker_profile_forwarded": (
            thread.get("permissions") == "flowfield_worker"
            and turn.get("permissions") == "flowfield_worker"
            and "sandboxPolicy" not in turn
            and turn.get("approvalPolicy") == "never"
        ),
        "limits": (
            "Fake app-server only; filesystem isolation and detached command cleanup are unproven."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bridge", type=Path)
    args = parser.parse_args()
    node = shutil.which("node")
    if node is None or not args.bridge.is_file():
        parser.error("An installed Node executable and bridge file are required")
    with tempfile.TemporaryDirectory(prefix="flowfield-acp-probe-") as directory:
        print(
            json.dumps(asyncio.run(probe(args.bridge.resolve(), Path(directory), node)), indent=2)
        )


if __name__ == "__main__":
    main()
