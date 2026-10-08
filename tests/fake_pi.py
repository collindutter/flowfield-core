#!/usr/bin/env python3
"""Deterministic native Pi RPC double. Never makes a model/network request."""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
mode = os.environ.get("FAKE_PI", "normal")
log = Path(os.environ["PI_TEST_LOG"])
log.write_text(json.dumps({"argv": args, "overlay": os.environ["PI_CODING_AGENT_DIR"]}) + "\n")
models = [
    {"provider": "example", "id": "reasoning", "name": "Reasoning", "input": ["text", "image"]},
    {"provider": "router", "id": "nested/model", "name": "Plain", "input": ["text"]},
]
model = models[0]
effort = "medium"
session_file = args[args.index("--session") + 1] if "--session" in args else None
identity = (
    json.loads(Path(session_file).read_text().splitlines()[0])["id"]
    if session_file
    else "ephemeral"
)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def response(command, data=None, success=True):
    emit(
        {
            "type": "response",
            "id": command["id"],
            "command": command["type"],
            "success": success,
            "data": data,
            "error": "secret diagnostic" if not success else None,
        }
    )


for line in sys.stdin:
    command = json.loads(line)
    with log.open("a") as output:
        output.write(json.dumps(command) + "\n")
    kind = command["type"]
    if kind == "get_state":
        if mode == "slow_start":
            time.sleep(60)
        if mode == "bad_wire":
            print("not json", flush=True)
            continue
        if mode == "extension_error":
            emit({"type": "extension_error", "error": "credential private"})
        response(
            command,
            {
                "model": model,
                "thinkingLevel": effort,
                "sessionId": "wrong" if mode == "bad_resume" else identity,
                "sessionFile": session_file,
            },
        )
    elif kind == "get_available_models":
        response(command, {"models": models})
    elif kind == "set_model":
        model = next(
            (
                m
                for m in models
                if m["provider"] == command["provider"] and m["id"] == command["modelId"]
            ),
            None,
        )
        response(command, model, model is not None)
    elif kind == "get_available_thinking_levels":
        response(
            command,
            {"levels": ["off", "low", "high", "max"] if model["id"] == "reasoning" else ["off"]},
        )
    elif kind == "set_thinking_level":
        if mode != "clamped":
            effort = command["level"]
        response(command)
    elif kind == "get_session_stats":
        response(command, {"contextUsage": {"tokens": 10, "contextWindow": 100}})
    elif kind == "prompt":
        if mode == "rejected":
            response(command, success=False)
            continue
        response(command, {"disposition": "handled" if mode == "handled" else "started"})
        if mode == "handled":
            continue
        if mode == "exit":
            sys.exit(1)
        if mode in {"hang", "wait_abort"}:
            continue
        emit({"type": "message_start", "message": {"role": "assistant"}})
        emit(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "private reasoning"},
            }
        )
        emit(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "contentIndex": 0,
                    "delta": "Public\u2028text",
                },
            }
        )
        emit(
            {
                "type": "tool_execution_start",
                "toolCallId": "call",
                "toolName": "bash",
                "args": {"command": "secret input"},
            }
        )
        emit(
            {
                "type": "tool_execution_update",
                "toolCallId": "call",
                "toolName": "bash",
                "partialResult": {"content": [{"text": "secret output"}]},
            }
        )
        emit(
            {
                "type": "tool_execution_end",
                "toolCallId": "call",
                "toolName": "bash",
                "isError": False,
            }
        )
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Public\u2028text"},
                {
                    "type": "thinking",
                    "thinking": "private reasoning",
                    "thinkingSignature": "private",
                },
            ],
            "stopReason": "error" if mode == "error" else "stop",
            "errorMessage": "secret failure",
        }
        emit({"type": "message_end", "message": message})
        emit({"type": "agent_end", "willRetry": False})
        # Acceptance and even agent_end are not completion. The client must wait.
        time.sleep(0.05)
        if session_file:
            with Path(session_file).open("a") as output:
                output.write(json.dumps({"type": "message", "message": message}) + "\n")
        emit({"type": "agent_settled", "aborted": mode == "aborted"})
    elif kind == "abort" and mode == "hang":
        # Simulates an unresponsive runtime after potentially detached tools.
        continue
    elif kind == "abort" and mode == "wait_abort":
        emit({"type": "agent_settled", "aborted": True})
        response(command)
    else:
        response(command)

if mode == "bad_shutdown":
    sys.exit(2)
