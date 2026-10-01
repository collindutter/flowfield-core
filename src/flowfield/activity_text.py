"""Independent storage and display bounds for public worker output."""

import re

OMISSION = "\n… middle omitted …\n"


def retain(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = (limit - len(OMISSION)) // 2
    return text[:head] + OMISSION + text[-(limit - head - len(OMISSION)) :]


def preview(text: str, kind: str) -> str:
    if kind == "command" and ("\n" in text or len(text) > 200):
        lines = text.splitlines()
        end = (
            lines[-1]
            if lines and lines[-1].startswith(("Exit code:", "Command interrupted"))
            else ""
        )
        source = lines[:-1] if end else lines
        if len(source) == 1 and len(source[0]) <= 200:
            return text
        return retain(source[0], 160) + "\n[Command source collapsed]" + ("\n" + end if end else "")
    # Code blocks are source evidence, not prose to replay through the default log.
    compact = re.sub(
        r"(?m)^\s*(```|~~~)[^\n]*\n(.*?)(?:^\s*\1[^\n]*$|\Z)",
        "[Code block collapsed]",
        text,
        flags=re.DOTALL,
    )
    lines = compact.splitlines(keepends=True)
    if len(lines) > 12:
        compact = "".join(lines[:6]) + OMISSION + "".join(lines[-6:])
    return retain(compact, 900)
