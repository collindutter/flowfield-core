"""Deterministic ACP peer. Commands in its prompt are test data, never model work."""

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONFIG = [
    {
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": "first",
        "options": [{"value": "first", "name": "First"}, {"value": "second", "name": "Second"}],
    }
]


def send(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def reply(request, result):
    send({"id": request["id"], "result": result})


def update(update_type, **data):
    send(
        {
            "method": "session/update",
            "params": {
                "sessionId": "test-session",
                "update": {"sessionUpdate": update_type, **data},
            },
        }
    )


async def main():
    stopped = asyncio.Event()
    pending = {}
    tasks = set()
    servers = []
    session_closed = False

    async def prompt(request):
        control = json.loads(request["params"]["prompt"][0]["text"])
        mode = control.get("mode", "normal")
        if mode == "disconnect":
            os._exit(2)
        if mode == "oversized":
            print("x" * 300000, flush=True)
            return
        if mode == "malformed":
            print("[]", flush=True)
            return
        if mode == "stderr":
            sys.stderr.write("private diagnostic\n" * 20000)
            sys.stderr.flush()
        if mode == "ignore_cancel":
            await asyncio.Event().wait()
        if mode in {"permission", "permission_disconnect"}:
            future = asyncio.get_running_loop().create_future()
            pending["permission"] = future
            send(
                {
                    "id": "permission",
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "test-session",
                        "toolCall": {"toolCallId": "tool-1", "title": "Check"},
                        "options": [
                            {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "deny", "name": "Reject once", "kind": "reject_once"},
                        ],
                    },
                }
            )
            if mode == "permission_disconnect":
                await asyncio.sleep(0.15)
                os._exit(2)
            value = await future
            update("agent_message_chunk", content={"type": "text", "text": json.dumps(value)})
        if mode == "wait":
            update("agent_message_chunk", content={"type": "text", "text": "started"})
            await stopped.wait()
        for server in servers if control.get("calls") else []:
            headers = {item["name"]: item["value"] for item in server["headers"]}
            async with httpx.AsyncClient(headers=headers) as client:
                async with streamable_http_client(server["url"], http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as mcp:
                        await mcp.initialize()
                        catalog = await mcp.list_tools()
                        update(
                            "agent_message_chunk",
                            content={
                                "type": "text",
                                "text": json.dumps(
                                    {"tools": [tool.model_dump() for tool in catalog.tools]}
                                ),
                            },
                        )
                        for call in control["calls"]:
                            result = await mcp.call_tool(call["name"], call.get("arguments", {}))
                            update(
                                "agent_message_chunk",
                                content={
                                    "type": "text",
                                    "text": json.dumps(
                                        {
                                            "call": call["name"],
                                            "result": result.model_dump(mode="json"),
                                        }
                                    ),
                                },
                            )
        send(
            {
                "method": "session/update",
                "params": {
                    "sessionId": "wrong-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "WRONG SESSION"},
                    },
                },
            }
        )
        update("agent_thought_chunk", content={"type": "text", "text": "PRIVATE REASONING"})
        update(
            "agent_thought_chunk",
            content={"type": "text", "text": None, "unexpected": "PRIVATE MALFORMED REASONING"},
        )
        update(
            "tool_call",
            toolCallId="tool-1",
            title="Check",
            kind="read",
            status="completed",
            rawInput={"secret": "PRIVATE TOOL INPUT"},
        )
        update("usage_update", used=100, size=1000)
        update("agent_message_chunk", content={"type": "text", "text": "finished"})
        reply(request, {"stopReason": "cancelled" if stopped.is_set() else "end_turn"})

    while line := await asyncio.to_thread(sys.stdin.readline):
        request = json.loads(line)
        method = request.get("method")
        if method == "initialize":
            if "slow-start" in sys.argv:
                await asyncio.Event().wait()
            reply(
                request,
                {
                    "protocolVersion": 99 if "bad-version" in sys.argv else 1,
                    "agentCapabilities": {
                        "loadSession": "no-load" not in sys.argv,
                        "mcpCapabilities": {"http": "no-http" not in sys.argv},
                        "sessionCapabilities": (
                            {"close": {}} if "close-session" in sys.argv else {}
                        ),
                    },
                },
            )
        elif method in {"session/new", "session/load"}:
            servers = request["params"]["mcpServers"]
            if request["params"].get("sessionId") == "missing":
                send({"id": request["id"], "error": {"code": -32000, "message": "Session missing"}})
            else:
                reply(request, {"sessionId": "test-session", "configOptions": CONFIG})
        elif method == "session/set_config_option":
            if "fallback" not in sys.argv:
                CONFIG[0]["currentValue"] = request["params"]["value"]
            reply(request, {"configOptions": CONFIG})
        elif method == "session/prompt":
            stopped.clear()
            task = asyncio.create_task(prompt(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        elif method == "session/cancel":
            stopped.set()
        elif method == "session/close":
            if "close-failure" in sys.argv:
                send({"id": request["id"], "error": {"code": -32000, "message": "Close failed"}})
            elif "close-hang" not in sys.argv:
                session_closed = True
                reply(request, {})
        elif request.get("id") in pending:
            pending.pop(request["id"]).set_result(request.get("result"))
    if "close-session" in sys.argv and not session_closed:
        sys.exit(3)
    if "slow-exit" in sys.argv:
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
