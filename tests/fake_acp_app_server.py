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
            result = {"config": {}, "layers": []}
        elif method == "model/list":
            result = {"data": [model], "nextCursor": None}
        elif method == "skills/list":
            result = {"data": []}
        elif method == "thread/start":
            result = {
                "thread": {"id": "test-thread", "turns": []},
                "model": "test-model",
                "reasoningEffort": "low",
                "modelProvider": "openai",
                "serviceTier": None,
                "cwd": request["params"]["cwd"],
                "approvalPolicy": "never",
            }
        elif method == "turn/start":
            result = {
                "turn": {"id": "test-turn", "items": [], "status": "inProgress", "error": None}
            }
        elif method == "mcpServerStatus/list":
            result = {"data": [], "nextCursor": None}
        emit({"id": request["id"], "result": result})
        if method == "turn/start":
            emit(
                {
                    "method": "turn/started",
                    "params": {"threadId": "test-thread", "turn": result["turn"]},
                }
            )
            emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "test-thread",
                        "turnId": "test-turn",
                        "itemId": "message",
                        "delta": "Offline proof",
                    },
                }
            )
            if scenario == "cancel":
                continue
            emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "test-thread",
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
