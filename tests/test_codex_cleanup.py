import asyncio
import sys

import pytest
from test_acp_session import FAKE

from flowfield.adapters.acp_session import AcpSession, ShutdownTimeouts
from flowfield.adapters.codex_cleanup import (
    CAPABILITY,
    CODEX_SHUTDOWN_TIMEOUTS,
    quiesce,
    require_cleanup,
)
from flowfield.errors import ApplicationError


@pytest.mark.parametrize(
    "capabilities",
    [
        {},
        {"_meta": None},
        *[
            {"_meta": {"flowfield.cleanup": {**CAPABILITY, "version": version}}}
            for version in [2, True, 1.0]
        ],
    ],
)
def test_missing_or_incompatible_capability_rejects_launch(capabilities):
    with pytest.raises(ApplicationError, match="verified managed cleanup"):
        require_cleanup(capabilities)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, True),
        ({"sessionId": "another"}, False),
        ({"status": "uncertain"}, False),
        ({"checkedThreads": 0}, False),
        ({"reason": "timeout"}, False),
        ({"version": 2}, False),
        ({"version": True}, False),
        ({"version": 1.0}, False),
        ({"status": "confirmed", "extra": "untrusted"}, False),
    ],
)
def test_receipt_is_exact_and_bound(change, expected):
    class Connection:
        async def ext_method(self, method, params):
            assert method == "flowfield/quiesce" and params == {"sessionId": "owned"}
            return {
                **CAPABILITY,
                "sessionId": "owned",
                "status": "confirmed",
                "reason": None,
                "checkedThreads": 1,
                "stoppedTerminals": 0,
                **change,
            }

    assert (
        asyncio.run(quiesce(Connection(), "owned", {"_meta": {"flowfield.cleanup": CAPABILITY}}))
        is expected
    )


@pytest.mark.parametrize(
    "flag,expected",
    [
        ("", True),
        ("cleanup-uncertain", False),
        ("cleanup-wrong-session", False),
    ],
)
def test_cleanup_receipt_controls_session_outcome(tmp_path, flag, expected):
    async def exercise():
        client = AcpSession(lambda event: None, cleanup=quiesce)
        await client.start(
            [sys.executable, str(FAKE), "cleanup", "close-session", flag],
            cwd=tmp_path,
            env={},
            mcp_servers=[],
        )
        require_cleanup(client.capabilities)
        await client.prompt("{}")
        # Receipt semantics do not require the OS to reap a process in 200 ms.
        # Use the normal bounded shutdown; exercise deadline expiry separately.
        receipt = await client.close()
        assert receipt.owned_work_stopped is expected
        assert receipt.process_group_exited
        assert client.state == ("closed" if expected else "interrupted")

    asyncio.run(exercise())


def test_cleanup_timeout_remains_unconfirmed_and_cancels_the_request():
    async def exercise():
        cancelled = asyncio.Event()

        async def unanswered(connection, session_id, capabilities):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        class Connection:
            async def close(self):
                pass

        client = AcpSession(lambda event: None, cleanup=unanswered)
        client.connection = Connection()
        client.session_id = "owned"
        receipt = await client.close(timeouts=ShutdownTimeouts(native_cleanup=0.01))
        assert cancelled.is_set()
        assert receipt.owned_work_stopped is False
        assert client.state == "interrupted"

    asyncio.run(exercise())


def test_native_cleanup_can_finish_after_the_old_deadline_and_survives_caller_cancel():
    async def exercise():
        entered = asyncio.Event()
        calls = 0

        async def slow_cleanup(connection, session_id, capabilities):
            nonlocal calls
            calls += 1
            entered.set()
            await asyncio.sleep(5.1)
            return True

        class Connection:
            async def close(self):
                pass

        client = AcpSession(
            lambda event: None,
            cleanup=slow_cleanup,
            shutdown_timeouts=CODEX_SHUTDOWN_TIMEOUTS,
        )
        client.connection = Connection()
        client.session_id = "owned"
        caller = asyncio.create_task(client.close())
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        receipt = await client.close()
        assert receipt.owned_work_stopped is True
        assert client.state == "closed"
        assert calls == 1
        assert await client.close() == receipt

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_shutdown_budgets_must_be_finite_and_positive(value):
    with pytest.raises(ValueError, match="positive and finite"):
        ShutdownTimeouts(native_cleanup=value)
