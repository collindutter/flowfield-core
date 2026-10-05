"""Read immutable Git comparisons by complete file, never arbitrary text slices."""

import os
from dataclasses import dataclass
from pathlib import Path

from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.review_models import ChangedFile, ChangedFiles, FileChange, FilePatch

MAX_BLOB_BYTES = 256_000
MAX_PATCH_BYTES = 192_000
MAX_PATCH_LINES = 4000
MAX_LINE_BYTES = 8000
_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color", "--find-renames", "-l1000")


@dataclass
class Entry:
    file: ChangedFile
    old_oid: str
    new_oid: str
    paths: list[bytes]


def entries(repository: Path, base: str, result: str) -> list[Entry]:
    raw = git(repository, "diff", *_OPTIONS, "--raw", "--no-abbrev", "-z", base, result, "--")
    parts = iter(raw.split(b"\0")[:-1])
    files: list[Entry] = []
    changes: dict[str, FileChange] = {
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
    }
    for header in parts:
        old_mode, new_mode, old_oid, new_oid, status = header.decode("ascii").split()
        old_path = next(parts)
        new_path = next(parts) if status[0] in "RC" else old_path
        file = ChangedFile(
            id=len(files),
            old_path=None if status[0] == "A" else old_path.decode(errors="replace"),
            new_path=None if status[0] == "D" else new_path.decode(errors="replace"),
            change=changes.get(status[0], "modified"),
            old_mode=old_mode.removeprefix(":"),
            new_mode=new_mode,
        )
        files.append(Entry(file, old_oid, new_oid, list(dict.fromkeys([old_path, new_path]))))
    return files


def changed_files(
    repository: Path, base: str, result: str, offset: int, limit: int
) -> ChangedFiles:
    files = entries(repository, base, result)
    end = min(len(files), offset + limit)
    return ChangedFiles(
        files=[entry.file for entry in files[offset:end]],
        total_files=len(files),
        next_offset=end if end < len(files) else None,
        base_commit=base,
        result_commit=result,
    )


def file_patch(repository: Path, base: str, result: str, index: int) -> FilePatch:
    files = entries(repository, base, result)
    if index < 0 or index >= len(files):
        raise ApplicationError("file_not_found", "This file is not in the captured result.", 404)
    entry = files[index]
    patch = FilePatch(file=entry.file, base_commit=base, result_commit=result)
    if "160000" in (entry.file.old_mode, entry.file.new_mode):
        patch.omitted_reason = "Submodule reference changed. Inspect the captured commits in Git."
        return patch
    for oid in (entry.old_oid, entry.new_oid):
        if set(oid) == {"0"}:
            continue
        if int(git(repository, "cat-file", "-s", oid)) > MAX_BLOB_BYTES:
            patch.omitted_reason = (
                "File exceeds the 256 KB preview limit. Inspect the captured commits in Git."
            )
            return patch
        content = git(repository, "cat-file", "blob", oid)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            patch.binary = True
        if b"\0" in content:
            patch.binary = True
        if patch.binary:
            patch.omitted_reason = "Binary or non-UTF-8 file. No text preview is available."
            return patch
    content = git(
        repository,
        "diff",
        *_OPTIONS,
        "--full-index",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--unified=3",
        base,
        result,
        "--",
        *(f":(literal){os.fsdecode(path)}" for path in entry.paths),
    )
    lines = content.splitlines()
    if (
        len(content) > MAX_PATCH_BYTES
        or len(lines) > MAX_PATCH_LINES
        or any(len(line) > MAX_LINE_BYTES for line in lines)
    ):
        patch.omitted_reason = (
            "Diff exceeds the preview limit (192 KB, 4,000 lines or an 8 KB line). "
            "Inspect it in Git."
        )
    elif any(line.startswith(b"Binary files ") for line in lines):
        patch.binary = True
        patch.omitted_reason = "Git marks this file as binary. No text preview is available."
    else:
        patch.text = content.decode("utf-8", errors="replace")
    return patch
