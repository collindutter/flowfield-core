"""Shared ACP lifecycle against a real deterministic subprocess, with no credentials."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from flowfield.adapters.acp_session import AcpSession

FAKE = Path(__file__).with_name("fake_acp.py")


async def start(tmp_path, events, *flags, permission=None, load=None, resume=None):
    client = AcpSession(events.append, on_permission=permission, request_timeout=2, turn_timeout=4)
    await client.start(
        [sys.executable, str(FAKE), *flags],
        cwd=tmp_path,
        env={},
        mcp_servers=[],
        load_session_id=load,
        resume_session_id=resume,
    )
    return client


def test_resume_negotiates_capability_and_does_not_replay_history(tmp_path):
    async def exercise():
        events = []
        client = await start(tmp_path, events, resume="test-session")
        try:
            assert not events
            assert client.session_id == "test-session"
            assert await client.prompt('{"mode":"normal"}') == "end_turn"
        finally:
            assert (await client.close()).process_group_exited
        with pytest.raises(RuntimeError, match="does not support resuming"):
            await start(tmp_path, [], "no-resume", resume="test-session")
        with pytest.raises(ValueError, match="load|resume"):
            await start(tmp_path, [], load="test-session", resume="test-session")

    asyncio.run(exercise())


def test_stream_model_scope_and_no_private_reasoning(tmp_path, caplog):
    async def exercise():
        events = []
        client = await start(tmp_path, events)
        try:
            with pytest.raises(ValueError, match="unavailable"):
                await client.select("model", "absent")
            await client.select("model", "second")
            assert client.config[0]["currentValue"] == "second"
            assert await client.prompt('{"mode":"stderr"}') == "end_turn"
            assert [event.kind for event in events] == ["tool", "usage", "text"]
            assert events[1].data == {"used": 100, "size": 1000}
            assert "PRIVATE" not in str(events) and "WRONG" not in str(events)
            assert client.state == "ready"
        finally:
            receipt = await client.close()
        assert receipt.turn_finished and receipt.process_group_exited
        assert await client.close() == receipt

    asyncio.run(exercise())
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "choice,expected", [("allow", "selected"), ("invented", "cancelled"), (None, "cancelled")]
)
def test_permissions_only_accept_offered_options(tmp_path, choice, expected):
    async def exercise():
        async def permission(request):
            assert request.tool_id == "tool-1"
            return choice

        events = []
        client = await start(tmp_path, events, permission=permission)
        try:
            await client.prompt('{"mode":"permission"}')
            response = json.loads(events[0].data["text"])
            assert response["outcome"]["outcome"] == expected
        finally:
            await client.close()

    asyncio.run(exercise())


def test_stop_cancels_pending_permission_and_blocks_new_work(tmp_path):
    async def exercise():
        requested = asyncio.Event()

        async def permission(request):
            requested.set()
            await asyncio.Event().wait()

        client = await start(tmp_path, [], "close-session", permission=permission)
        prompt = asyncio.create_task(client.prompt('{"mode":"permission"}'))
        await asyncio.wait_for(requested.wait(), 3)
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("second turn")
        with pytest.raises(RuntimeError, match="not ready"):
            await client.select("model", "second")
        receipt = await client.close()
        assert receipt.turn_finished and receipt.process_group_exited
        assert receipt.session_closed is True
        assert await prompt == "cancelled"
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("after stop")

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["disconnect", "oversized", "malformed"])
def test_broken_transport_never_replays_or_reuses_session(tmp_path, mode):
    async def exercise():
        client = await start(tmp_path, [])
        with pytest.raises((ConnectionError, RuntimeError)):
            await client.prompt(json.dumps({"mode": mode}))
        assert client.state == "interrupted"
        assert client.process.returncode is not None
        with pytest.raises(RuntimeError, match="not ready"):
            await client.prompt("retry")

    asyncio.run(exercise())


def test_unacknowledged_stop_is_uncertain_even_after_process_exit(tmp_path):
    async def exercise():
        client = await start(tmp_path, [])
        prompt = asyncio.create_task(client.prompt('{"mode":"ignore_cancel"}'))
        await asyncio.sleep(0.1)
        receipt = await client.close(timeout=0.15)
        assert not receipt.turn_finished
        assert receipt.process_group_exited
        assert client.state == "interrupted"
        with pytest.raises((ConnectionError, asyncio.CancelledError)):
            await prompt

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "flag,load,message",
    [
        ("bad-version", None, "protocol version"),
        ("no-load", "old", "loading sessions"),
        ("", "missing", "Session missing"),
    ],
)
def test_capability_and_missing_session_errors_are_explicit(tmp_path, flag, load, message):
    async def exercise():
        with pytest.raises(Exception, match=message):
            await start(tmp_path, [], flag, load=load)

    asyncio.run(exercise())


def test_model_fallback_closes_session(tmp_path):
    async def exercise():
        client = await start(tmp_path, [], "fallback")
        with pytest.raises(RuntimeError, match="did not apply"):
            await client.select("model", "second")
        assert client.state != "ready" and client.process.returncode is not None

    asyncio.run(exercise())


def test_event_delivery_failure_interrupts_instead_of_claiming_success(tmp_path):
    async def exercise():
        client = await start(tmp_path, [])

        def fail(event):
            raise RuntimeError("Storage unavailable")

        client.on_event = fail
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await client.prompt("{}")
        assert client.state == "interrupted"
        assert client.process.returncode is not None

    asyncio.run(exercise())


def test_load_is_explicit_and_does_not_resend_previous_work(tmp_path):
    async def exercise():
        events = []
        client = await start(tmp_path, events, load="test-session")
        assert client.state == "ready" and events == []
        await client.prompt("{}")
        assert [event.data["text"] for event in events if event.kind == "text"] == ["finished"]
        await client.close()

    asyncio.run(exercise())


def test_stop_during_startup_and_cancelled_stop_caller(tmp_path):
    async def exercise():
        client = AcpSession(lambda event: None, request_timeout=2)
        startup = asyncio.create_task(
            client.start(
                [sys.executable, str(FAKE), "slow-start"],
                cwd=tmp_path,
                env={},
                mcp_servers=[],
            )
        )
        async with asyncio.timeout(2):
            while client.process is None:
                await asyncio.sleep(0.001)
        stop = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        receipt = await client.close()
        assert receipt.process_group_exited
        with pytest.raises((ConnectionError, RuntimeError)):
            await startup
        assert client.state != "ready" and client.process.returncode is not None

    asyncio.run(exercise())


def test_negotiated_session_close_and_eof_precede_process_signals(tmp_path):
    async def exercise():
        client = await start(tmp_path, [], "close-session", "slow-exit")
        await client.prompt("{}")
        receipt = await client.close(timeout=1)
        assert receipt.session_closed is True
        assert receipt.process_exited_gracefully
        assert receipt.process_group_exited and receipt.turn_finished
        assert client.process.returncode == 0 and client.state == "closed"

    asyncio.run(exercise())


@pytest.mark.parametrize("flag", ["close-failure", "close-hang"])
def test_failed_native_session_close_retains_uncertainty(tmp_path, flag):
    async def exercise():
        client = await start(tmp_path, [], "close-session", flag)
        receipt = await client.close(timeout=0.15)
        assert receipt.session_closed is False
        assert receipt.process_group_exited
        assert client.state == "interrupted"

    asyncio.run(exercise())


def test_closing_one_session_does_not_interrupt_another(tmp_path):
    async def exercise():
        first_events, second_events = [], []
        first = await start(tmp_path, first_events, "close-session")
        second = await start(tmp_path, second_events, "close-session")
        first_turn = asyncio.create_task(first.prompt('{"mode":"wait"}'))
        second_turn = asyncio.create_task(second.prompt('{"mode":"wait"}'))
        try:
            async with asyncio.timeout(3):
                while not first_events or not second_events:
                    await asyncio.sleep(0.01)
            receipt = await first.close(timeout=1)
            assert receipt.session_closed and receipt.process_group_exited
            assert await first_turn == "cancelled"
            assert not second_turn.done()
            assert second.state == "running" and second.process.returncode is None
        finally:
            await first.close(timeout=1)
            await second.close(timeout=1)
            await asyncio.gather(first_turn, second_turn, return_exceptions=True)

    asyncio.run(exercise())


def test_partial_tool_updates_keep_public_facts_and_bound_cache():
    from acp.schema import ToolCallProgress, ToolCallStart

    from flowfield.adapters.codex_agent import codex_activity_details

    async def exercise():
        events = []
        client = AcpSession(events.append, activity_projection=codex_activity_details)
        client.session_id = "session"
        client.state = "running"
        await client.session_update(
            "session",
            ToolCallStart.model_validate(
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "check",
                    "title": "Run tests",
                    "kind": "execute",
                    "status": "in_progress",
                    "locations": [{"path": "/project/test.py"}],
                    "rawInput": {"command": "pnpm test", "cwd": "/project", "secret": "PRIVATE"},
                    "rawOutput": "PRIVATE OUTPUT",
                }
            ),
        )
        await client.session_update(
            "session",
            ToolCallProgress.model_validate(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "check",
                    "status": "completed",
                }
            ),
        )
        final = events[-1].data
        assert final["title"] == "Run tests" and final["status"] == "completed"
        assert final["kind"] == "execute"
        assert "pnpm test" in final["details"] and "/project/test.py" in final["details"]
        assert "PRIVATE" not in str(events)
        assert events[0].data["status"] == "in_progress"
        await client.session_update(
            "session",
            ToolCallProgress.model_validate(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "check",
                    "status": "failed",
                    "rawOutput": {
                        "error": {"message": "Connection closed", "secret": "PRIVATE"},
                        "result": {
                            "structuredContent": {
                                "error": {
                                    "code": "tool_busy",
                                    "message": "Wait for the current operation.",
                                },
                                "private": "PRIVATE",
                            },
                        },
                    },
                }
            ),
        )
        assert "Connection closed" in events[-1].data["details"]
        assert "Wait for the current operation." in events[-1].data["details"]
        assert "PRIVATE" not in str(events)
        for index in range(110):
            await client.session_update(
                "session",
                ToolCallStart.model_validate(
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": str(index),
                        "title": "x" * 10000,
                        "kind": "read",
                        "status": "completed",
                    }
                ),
            )
        assert len(client._tool_activity) == 100
        assert all(len(item["title"]) <= 4000 for item in client._tool_activity.values())

    asyncio.run(exercise())
