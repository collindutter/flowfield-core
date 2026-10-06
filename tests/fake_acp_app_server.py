"""Offline Codex app-server peer for probing a separately installed ACP bridge."""

import json
import os
import sys
import time
from pathlib import Path


def emit(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def main():
    record = Path(os.environ["FLOWFIELD_TEST_RPC_RECORD"])
    scenario = os.environ.get("FLOWFIELD_TEST_SCENARIO", "complete")
    active = False
    threads = set()
    background = scenario in {"background", "cleanup-refused"}
    model = {
        "id": "test-model",
        "model": "test-model",
        "displayName": "Test model",
        "description": "Offline test",
        "defaultReasoningEffort": "low",
        "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "Low"}],
        "inputModalities": ["text"],
        "isDefault": True,
        "serviceTiers": [],
        "additionalSpeedTiers": [],
        "hidden": False,
    }
    for line in sys.stdin:
        request = json.loads(line)
        with record.open("a") as output:
            output.write(json.dumps(request) + "\n")
        if "id" not in request:
            continue
        method = request["method"]
        result = {}
        if method == "initialize":
            result = {"userAgent": "fake-codex", "codexHome": os.environ["CODEX_HOME"]}
        elif method == "account/read":
            result = {"account": {"type": "apiKey"}, "requiresOpenaiAuth": False}
        elif method == "config/read":
            result = {
                "config": {"mcp_servers": {"flowfield": {"url": "http://127.0.0.1:1/standalone"}}},
                "layers": [],
            }
        elif method == "model/list":
            result = {"data": [model], "nextCursor": None}
        elif method == "skills/list":
            result = {"data": []}
        elif method in {"thread/start", "thread/resume"}:
            thread_id = "title-thread" if request["params"].get("ephemeral") else "test-thread"
            threads.add(thread_id)
            if method == "thread/resume":
                assert request["params"]["threadId"] == "test-thread"
                assert request["params"]["excludeTurns"] is True
            result = {
                "thread": {"id": thread_id, "turns": []},
                "model": "test-model",
                "reasoningEffort": "low",
                "modelProvider": "openai",
                "serviceTier": None,
                "cwd": request["params"]["cwd"],
                "approvalPolicy": "never",
            }
        elif method == "turn/start":
            active = True
            if scenario == "resume" and request["params"]["threadId"] == "test-thread":
                history = record.with_suffix(".history.json")
                turns = json.loads(history.read_text()) if history.exists() else []
                turns.append(request["params"]["input"])
                history.write_text(json.dumps(turns))
            result = {
                "turn": {"id": "test-turn", "items": [], "status": "inProgress", "error": None}
            }
        elif method == "mcpServerStatus/list":
            result = {"data": [], "nextCursor": None}
        elif method == "thread/loaded/list":
            result = {"data": sorted(threads), "nextCursor": None}
        elif method == "thread/goal/get":
            result = {"goal": None}
        elif method == "thread/backgroundTerminals/list":
            result = {
                "data": [{"processId": "command-1"}] if background else [],
                "nextCursor": None,
            }
        elif method == "thread/backgroundTerminals/terminate":
            if scenario == "cleanup-refused":
                result = {"terminated": False}
            else:
                background = False
                result = {"terminated": True}
        elif method == "thread/read":
            result = {
                "thread": {
                    "id": request["params"]["threadId"],
                    "ephemeral": False,
                    "status": {"type": "active" if active else "idle"},
                }
            }
        emit({"id": request["id"], "result": result})
        if method == "thread/compact/start":
            for event, status in (("turn/started", "inProgress"), ("turn/completed", "completed")):
                emit(
                    {
                        "method": event,
                        "params": {
                            "threadId": request["params"]["threadId"],
                            "turn": {
                                "id": "compact-turn",
                                "items": [],
                                "status": status,
                                "error": None,
                            },
                        },
                    }
                )
        if method == "turn/start":
            thread_id = request["params"]["threadId"]
            emit(
                {
                    "method": "turn/started",
                    "params": {"threadId": thread_id, "turn": result["turn"]},
                }
            )
            emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": thread_id,
                        "turnId": "test-turn",
                        "itemId": "message",
                        "delta": (
                            f"Offline native history: {len(turns)} turns"
                            if scenario == "resume" and thread_id == "test-thread"
                            else "Offline proof"
                        ),
                    },
                }
            )
            if scenario == "cancel":
                continue
            active = False
            emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {
                            "id": "test-turn",
                            "items": [],
                            "status": "completed",
                            "error": None,
                        },
                    },
                }
            )
        elif method == "turn/interrupt":
            active = False
            emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "test-thread",
                        "turn": {
                            "id": "test-turn",
                            "items": [],
                            "status": "interrupted",
                            "error": None,
                        },
                    },
                }
            )
    if scenario == "slow-shutdown":
        # The released bridge terminates its native child after two seconds.
        # Simulate unfinished native cleanup; this is not a native Codex model test.
        time.sleep(30)
    with record.open("a") as output:
        output.write(json.dumps({"probe_native_cleanup_completed": True}) + "\n")


if __name__ == "__main__":
    main()
