"""Bounded browser projections of a captured code result."""

from typing import Literal

from flowfield.execution_models import Record

FileChange = Literal["added", "deleted", "modified", "renamed", "copied", "type_changed"]


class ChangedFile(Record):
    id: int
    old_path: str | None
    new_path: str | None
    change: FileChange
    old_mode: str
    new_mode: str


class ChangedFiles(Record):
    files: list[ChangedFile]
    total_files: int
    next_offset: int | None
    base_commit: str
    result_commit: str


class FilePatch(Record):
    file: ChangedFile
    text: str | None = None
    binary: bool = False
    omitted_reason: str | None = None
    base_commit: str
    result_commit: str
