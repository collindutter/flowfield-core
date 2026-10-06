"""Both roles use the same ACP client and real scoped MCP transport."""

import asyncio
import json
import sys

import httpx
import pytest
from test_acp_session import FAKE
from test_execution import BASE, fixture

from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.agent_mcp import serve_scope
from flowfield.agent_tools import COORDINATOR_TOOLS, coordinator_scope, worker_scope
from flowfield.supervisor import WorkerBridge


def test_coordinator_reads_overlap_with_bounded_revocable_access(tmp_path):
    service = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(service.workspace, "harbor")
        original = grant._call
        entered = asyncio.Queue()
        release = asyncio.Event()

        async def delayed(name, arguments):
            entered.put_nowait(name)
            await release.wait()
            return await original(name, arguments)

        grant._call = delayed
        calls = []
        for name in ["get_project", "get_board"] * 4:
            calls.append(asyncio.create_task(grant.call(name, {})))
            assert await asyncio.wait_for(entered.get(), 2) == name
        excess = await grant.call("get_board", {})
        assert excess.structuredContent["error"]["code"] == "tool_busy"
        # Cancellation releases capacity; revocation rejects new work without
        # falsely claiming to cancel operations already accepted.
        calls.pop().cancel()
        await asyncio.sleep(0)
        replacement = asyncio.create_task(grant.call("get_board", {}))
        assert await asyncio.wait_for(entered.get(), 2) == "get_board"
        calls.append(replacement)
        grant.revoke()
        closed = await grant.call("get_board", {})
        assert closed.structuredContent["error"]["code"] == "scope_closed"
        release.set()
        results = await asyncio.gather(*calls)
        assert all(not result.isError for result in results)
        assert grant._reading == 0

    asyncio.run(exercise())


async def journey(tmp_path, grant, calls):
    events = []
    client = AcpSession(events.append, request_timeout=5, turn_timeout=10)
    async with serve_scope(grant) as server:
        await client.start([sys.executable, str(FAKE)], cwd=tmp_path, env={}, mcp_servers=[server])
        try:
            await client.select("model", "second")
            assert await client.prompt(json.dumps({"calls": calls})) == "end_turn"
        finally:
            receipt = await client.close()
        assert receipt.turn_finished and receipt.process_group_exited
    assert grant.revoked
    return [
        json.loads(event.data["text"])
        for event in events
        if event.kind == "text" and event.data["text"].startswith("{")
    ]


def test_coordinator_captures_work_in_fixed_project(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(execution.workspace, "harbor")
        assert set(grant.tools) == COORDINATOR_TOOLS
        for tool in grant.tools.values():
            assert "project_id" not in tool.inputSchema["properties"]
        events = await journey(
            tmp_path,
            grant,
            [
                {"name": "get_board"},
                {
                    "name": "create_task",
                    "arguments": {
                        "task": {
                            "id": "captured",
                            "title": "Captured in conversation",
                            "body": "Agreed outcome",
                            "author": "human",
                        }
                    },
                },
                {"name": "get_board", "arguments": {"project_id": "elsewhere"}},
                {"name": "initialize_project", "arguments": {}},
                {"name": "get_task_stages", "arguments": {"task_id": "task-0"}},
            ],
        )
        assert len(events) == 6
        results = [event["result"] for event in events[1:]]
        assert not results[0]["isError"] and not results[1]["isError"], results
        assert results[2]["isError"] and results[3]["isError"]
        assert not results[4]["isError"]
        assert execution.workspace.task("harbor", "captured").title == "Captured in conversation"
        assert execution.workspace.task("harbor", "captured").updated_by == "agent"
        denied = await grant.call("get_board", {})
        assert denied.isError and denied.structuredContent["error"]["code"] == "scope_closed"

    asyncio.run(exercise())


def test_worker_reads_runs_submits_and_closes_same_bridge(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)

    class Commands:
        async def command(self, script, *, timeout_ms):
            # A deterministic executor, deliberately not a sandbox or live Codex proof.
            assert script == "perform assigned check"
            (tmp_path / "executed").write_text("done")
            return {"exitCode": 0, "stdout": "checked", "stderr": ""}

    async def exercise():
        bridge = WorkerBridge(execution, run, Commands())
        events = await journey(
            tmp_path,
            worker_scope(bridge),
            [
                {"name": "read_context", "arguments": {"section": "description"}},
                {"name": "run_command", "arguments": {"command": "perform assigned check"}},
                {
                    "name": "submit_result",
                    "arguments": {
                        "outcome": "complete",
                        "summary": "Checked",
                        "checks": "Deterministic check",
                    },
                },
                {"name": "run_command", "arguments": {"command": "late write"}},
            ],
        )
        results = [event["result"] for event in events[1:]]
        assert not results[0]["isError"] and not results[2]["isError"], results
        assert results[1]["isError"]
        assert results[3]["isError"]
        assert not (tmp_path / "executed").exists()  # Native commands never pass through MCP.
        assert bridge.result.summary == "Checked"
        # Saving a report is not execution finish, code approval or delivery.
        assert execution.get("harbor", run.id).status == "running"

    asyncio.run(exercise())


def test_scoped_endpoint_rejects_ambient_browser_and_expired_access(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(execution.workspace, "harbor")
        async with serve_scope(grant) as server:
            headers = {item.name: item.value for item in server.headers}
            async with httpx.AsyncClient() as client:
                assert (await client.get(server.url)).status_code == 403
                assert (
                    await client.get(
                        server.url, headers={**headers, "Origin": "https://evil.example"}
                    )
                ).status_code == 403
                assert (
                    await client.get(server.url, headers={**headers, "Host": "evil.example"})
                ).status_code == 421
                assert (
                    await client.post(server.url, headers=headers, content=b"x" * 65537)
                ).status_code == 413
                grant.revoke()
                assert (await client.get(server.url, headers=headers)).status_code == 403
        async with serve_scope(
            await coordinator_scope(execution.workspace, "harbor"), lifetime=-1
        ) as server:
            async with httpx.AsyncClient(
                headers={item.name: item.value for item in server.headers}
            ) as client:
                assert (await client.get(server.url)).status_code == 403

    asyncio.run(exercise())


def test_no_silent_loss_of_tools_for_unsupported_harness(tmp_path):
    execution = fixture(tmp_path)

    async def exercise():
        grant = await coordinator_scope(execution.workspace, "harbor")
        async with serve_scope(grant) as server:
            client = AcpSession(lambda event: None)
            with pytest.raises(RuntimeError, match="HTTP MCP"):
                await client.start(
                    [sys.executable, str(FAKE), "no-http"],
                    cwd=tmp_path,
                    env={},
                    mcp_servers=[server],
                )
            assert client.process.returncode is not None
        assert grant.revoked

    asyncio.run(exercise())


def test_pending_workflow_operation_blocks_report_and_revocation_prevents_late_work(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run
    execution.started("harbor", run.id)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class ControlledBridge(WorkerBridge):
            async def call(self, name, arguments):
                started.set()
                await release.wait()
                return await super().call(name, arguments)

        grant = worker_scope(ControlledBridge(execution, run, None))
        command = asyncio.create_task(grant.call("read_context", {"section": "description"}))
        await asyncio.wait_for(started.wait(), 2)
        result = await grant.call(
            "submit_result", {"outcome": "complete", "summary": "Late", "checks": "none"}
        )
        assert result.isError and result.structuredContent["error"]["code"] == "tool_busy"
        grant.revoke()
        release.set()
        await command
        assert (await grant.call("submit_result", {})).isError

    asyncio.run(exercise())
