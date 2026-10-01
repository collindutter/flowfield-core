"""Observed file summaries from captured Git trees, never inferred from command text."""

import json
from pathlib import Path

from flowfield.adapters.local_environment import git


def captured_changes(checkout: Path, before: str, after: str) -> str:
    rows = git(
        checkout,
        "diff",
        "--numstat",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        before,
        after,
        "--",
    ).split(b"\0")
    files = [row.split(b"\t", 2) for row in rows if row]
    if not files:
        return "Captured snapshot: no file changes."
    lines = ["Captured file changes:"]
    for added, removed, path in files[:50]:
        name = json.dumps(path.decode("utf-8", errors="replace"), ensure_ascii=False)
        change = (
            f"+{int(added)} / −{int(removed)} lines"
            if added.isdigit() and removed.isdigit()
            else "binary change"
        )
        lines.append(f"{name[:300]}: {change}")
    if len(files) > 50:
        lines.append(f"… {len(files) - 50} more files; see Code changes.")
    return "\n".join(lines)
