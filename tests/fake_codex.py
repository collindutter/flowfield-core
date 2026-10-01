"""Configuration CLI fixture. Never invokes a harness or reads real user settings."""

import json
import os
import sys
from pathlib import Path

state = Path(os.environ["FLOWFIELD_TEST_CODEX_STATE"])
data = json.loads(state.read_text())
args = sys.argv[1:]
if data.get("fail"):
    print("private harness diagnostic", file=sys.stderr)
    sys.exit(1)
if args == ["mcp", "list", "--json"]:
    print(json.dumps(list(data["servers"].values())))
elif args[:2] == ["mcp", "add"]:
    assert args[3] == "--url"
    data["servers"][args[2]] = {
        "name": args[2],
        "enabled": True,
        "transport": {"type": "streamable_http", "url": args[4]},
    }
    data["writes"] += 1
    state.write_text(json.dumps(data))
elif args[:2] == ["mcp", "remove"]:
    del data["servers"][args[2]]
    data["writes"] += 1
    state.write_text(json.dumps(data))
else:
    sys.exit(2)
