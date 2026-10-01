"""Adapter race tests with a fake transport, never a live model."""

import asyncio

from flowfield.adapters.codex_worker import CodexWorker


def test_notifications_cannot_replace_owned_turn_or_usage(tmp_path, monkeypatch):
    monkeypatch.setattr("flowfield.adapters.codex_worker.shutil.which", lambda _: "/fixture/codex")

    async def exercise():
        clients = [CodexWorker(tmp_path / str(index)) for index in range(2)]
        usage = [[], []]
        for index, client in enumerate(clients):
            client.thread_id = f"thread-{index}"
            client.completed = asyncio.get_running_loop().create_future()
            client.on_usage = usage[index].append
            client._event("turn/started", {"threadId": client.thread_id, "turn": {"id": "owned"}})
        # Deliberately reuse a turn id: ownership must include the thread.
        for client in clients:
            client._event("turn/started", {"threadId": "thread-0", "turn": {"id": "unrelated"}})
            client._event(
                "thread/tokenUsage/updated",
                {
                    "threadId": "thread-0",
                    "turnId": "owned",
                    "tokenUsage": {"total": {"totalTokens": 12}},
                },
            )
            client._event(
                "thread/tokenUsage/updated",
                {
                    "threadId": client.thread_id,
                    "turnId": "old",
                    "tokenUsage": {"total": {"totalTokens": 999}},
                },
            )
            client._event(
                "turn/completed",
                {"threadId": "thread-0", "turn": {"id": "owned", "status": "completed"}},
            )
            assert client.turn_id == "owned"
        assert [item.total_tokens for item in usage[0]] == [12]
        assert usage[1] == []
        assert clients[0].completed.done() and not clients[1].completed.done()
        clients[1]._event(
            "turn/completed", {"threadId": "thread-1", "turn": {"id": "old", "status": "completed"}}
        )
        assert not clients[1].completed.done()
        clients[1]._event(
            "turn/completed",
            {"threadId": "thread-1", "turn": {"id": "owned", "status": "completed"}},
        )
        assert clients[1].completed.done()

    asyncio.run(exercise())


def test_stop_waits_for_terminal_command_response(tmp_path, monkeypatch):
    monkeypatch.setattr("flowfield.adapters.codex_worker.shutil.which", lambda _: "/fixture/codex")

    async def exercise():
        client = CodexWorker(tmp_path)
        entered, terminal = asyncio.Event(), asyncio.Event()
        observations = []
        client.on_commands = observations.append

        async def rpc(method, params=None, **kwargs):
            if method == "command/exec":
                entered.set()
                await terminal.wait()
                return {"exitCode": 137, "stdout": "", "stderr": ""}
            assert method == "command/exec/terminate"
            asyncio.get_running_loop().call_later(0.02, terminal.set)
            return {}

        async def close():
            assert terminal.is_set() and not client.commands

        client.rpc, client.close = rpc, close
        command = asyncio.create_task(client.command("long-running fixture"))
        await entered.wait()
        assert await client.stop()
        assert (await command)["exitCode"] == 137
        assert len(observations[0]) == 1 and observations[-1] == []
        assert await client.stop()  # Already stopped is harmless.

    asyncio.run(exercise())


def test_streamed_commands_keep_bounded_worker_output(tmp_path, monkeypatch):
    import base64

    monkeypatch.setattr("flowfield.adapters.codex_worker.shutil.which", lambda _: "/fixture/codex")

    async def exercise():
        client = CodexWorker(tmp_path)
        events = []
        client.on_activity = events.append

        async def rpc(method, params=None, **kwargs):
            assert method == "command/exec" and params["streamStdoutStderr"]
            client._event(
                "command/exec/outputDelta",
                {
                    "processId": params["processId"],
                    "stream": "stdout",
                    "deltaBase64": base64.b64encode(b"tests passed\n").decode(),
                    "capReached": False,
                },
            )
            return {"exitCode": 0, "stdout": "", "stderr": ""}

        client.rpc = rpc
        result = await client.command("test")
        assert result["stdout"] == "tests passed\n"
        assert any(e.kind == "output" and e.text == result["stdout"] for e in events)
        assert not client.activity.output and not client.commands

    asyncio.run(exercise())
