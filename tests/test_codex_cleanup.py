import asyncio
import sys

import pytest
from test_acp_session import FAKE

from flowfield.adapters.acp_session import AcpSession
from flowfield.adapters.codex_cleanup import CAPABILITY, quiesce, require_cleanup
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
        receipt = await client.close(timeout=0.01)
        assert cancelled.is_set()
        assert receipt.owned_work_stopped is False
        assert client.state == "interrupted"

    asyncio.run(exercise())
