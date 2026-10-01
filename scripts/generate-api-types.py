"""Generate checked-in browser wire types without starting a service or opening state."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from flowfield.api import create_app

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
target = root / "web/src/api-schema.ts"
with tempfile.TemporaryDirectory(prefix="flowfield-contracts-") as directory:
    schema = Path(directory) / "openapi.json"
    output = Path(directory) / "api-schema.ts"
    schema.write_text(json.dumps(create_app().openapi()), encoding="utf-8")
    subprocess.run(
        [
            "pnpm",
            "--dir",
            str(root / "web"),
            "exec",
            "openapi-typescript",
            str(schema),
            "--output",
            str(output),
        ],
        check=True,
    )
    subprocess.run(
        ["pnpm", "--dir", str(root / "web"), "exec", "prettier", "--write", str(output)],
        check=True,
    )
    generated = output.read_text(encoding="utf-8")
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != generated:
            raise SystemExit("Browser API types are stale. Run make api-types.")
    else:
        target.write_text(generated, encoding="utf-8")
