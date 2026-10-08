"""Immutable attempt output copied from explicit roots into workspace-owned storage."""

import codecs
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
from uuid import uuid4

from pydantic import BaseModel

from flowfield.errors import ApplicationError

if TYPE_CHECKING:
    from flowfield.application import Workspace

MAX_FILE = 100 * 1024 * 1024
MAX_PROJECT = 1024 * 1024 * 1024
MAX_ARTIFACTS = 1000
CHUNK_SIZE = 1024 * 1024
INLINE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "video/webm", "video/mp4", "text/plain"}
)
TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".log",
        ".csv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".sh",
        ".sql",
        ".xml",
    }
)


class Artifact(BaseModel):
    id: str
    project_id: str
    task_id: str
    run_id: str
    name: str
    title: str
    description: str
    mime: str
    size: int
    created_at: str
    href: str


def _invalid_path() -> ApplicationError:
    return ApplicationError(
        "artifact_path", "Publish a regular file inside an allowed root, without symlinks."
    )


def _absolute(path: Path) -> Path:
    # Do not resolve(): resolving would erase symlinks before the no-follow walk.
    if ".." in path.parts:
        raise _invalid_path()
    return Path(os.path.abspath(path))


@contextmanager
def _open_regular(path: Path) -> Iterator[BinaryIO]:
    """Walk from / using pinned directory descriptors; never follow any symlink."""
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise _invalid_path()
            yield stream
    finally:
        os.close(directory)


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _mime(name: str, header: bytes, utf8: bool) -> str:
    suffix = Path(name).suffix.lower()
    # Active documents are downloads even if misleadingly named or prefixed.
    document = header.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if suffix in {".html", ".htm", ".svg", ".xhtml"} or re.search(
        rb"<(?:!doctype\s+html|html|svg)\b", document
    ):
        return "application/octet-stream"
    signatures = {
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/webp": len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP",
        "video/webm": header.startswith(b"\x1a\x45\xdf\xa3") and b"\x42\x82\x84webm" in header,
        "video/mp4": len(header) >= 16
        and header[4:8] == b"ftyp"
        and 16 <= int.from_bytes(header[:4], "big") <= len(header)
        and header[8:12] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash"},
    }
    expected = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".webm": "video/webm",
        ".mp4": "video/mp4",
    }.get(suffix)
    if expected:
        if not signatures[expected]:
            raise ApplicationError("artifact_type", "The media file has an invalid signature.")
        return expected
    for mime, matches in signatures.items():
        if matches:
            return mime
    if suffix in TEXT_SUFFIXES:
        if not utf8:
            raise ApplicationError("artifact_type", "Text artifacts must contain UTF-8 text.")
        return "text/plain"
    return "application/octet-stream"


class Artifacts:
    def __init__(self, workspace: "Workspace"):
        self.workspace = workspace

    def _run(self, db: sqlite3.Connection, project_id: str, run_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute(
            "SELECT task_id,status FROM runs WHERE project_id=? AND id=?", (project_id, run_id)
        ).fetchone()
        if not row:
            raise ApplicationError("not_found", "Attempt not found in this project.", 404)
        return row

    def publish(
        self,
        project_id: str,
        run_id: str,
        path: Path,
        *,
        roots: tuple[Path, ...],
        title: str,
        description: str = "",
    ) -> Artifact:
        from flowfield.application import now

        source = _absolute(path)
        allowed = tuple(_absolute(root) for root in roots)
        if not any(source != root and source.is_relative_to(root) for root in allowed):
            raise _invalid_path()
        name = source.name
        if not name or len(name) > 200 or any(ord(c) < 32 or c in "\\\x7f" for c in name):
            raise ApplicationError(
                "artifact_name", "Choose a simple filename of at most 200 characters."
            )
        if not title.strip() or len(title) > 200 or len(description) > 8000:
            raise ApplicationError(
                "artifact_metadata",
                "Provide a title (1–200 characters) and a description of at most 8000 characters.",
            )
        identity = uuid4().hex
        temporary = ".pending-" + identity
        directory = None
        published = False
        try:
            directory = os.open(
                self.workspace.directory / "artifacts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            with self.workspace.connection(write=True, notify=False) as db:
                self.workspace._project(db, project_id)
                run = self._run(db, project_id, run_id)
                if run["status"] not in ("preparing", "running", "stopping"):
                    raise ApplicationError(
                        "artifact_inactive", "Only an active attempt can publish artifacts.", 409
                    )
                self.workspace._task(db, project_id, run["task_id"])
                total, count = db.execute(
                    "SELECT coalesce(sum(size),0),count(*) FROM artifacts WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if count >= MAX_ARTIFACTS:
                    raise ApplicationError(
                        "artifact_capacity",
                        "This project's artifact count limit has been reached.",
                        409,
                    )
                with _open_regular(source) as stream:
                    before = os.fstat(stream.fileno())
                    if before.st_size > MAX_FILE:
                        raise ApplicationError(
                            "artifact_size", "Artifacts must be at most 100 MiB."
                        )
                    if total + before.st_size > MAX_PROJECT:
                        raise ApplicationError(
                            "artifact_capacity", "This project's artifact storage is full.", 409
                        )
                    fd = os.open(
                        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory
                    )
                    size, header, utf8 = 0, b"", True
                    decoder = codecs.getincrementaldecoder("utf-8")()
                    with os.fdopen(fd, "wb") as target:
                        while chunk := stream.read(CHUNK_SIZE):
                            size += len(chunk)
                            if size > MAX_FILE:
                                raise ApplicationError(
                                    "artifact_size", "Artifacts must be at most 100 MiB."
                                )
                            if total + size > MAX_PROJECT:
                                raise ApplicationError(
                                    "artifact_capacity",
                                    "This project's artifact storage is full.",
                                    409,
                                )
                            header += chunk[: max(0, 4096 - len(header))]
                            if utf8:
                                try:
                                    text = decoder.decode(chunk)
                                    utf8 = "\x00" not in text
                                except UnicodeError:
                                    utf8 = False
                            target.write(chunk)
                        if utf8:
                            try:
                                decoder.decode(b"", final=True)
                            except UnicodeError:
                                utf8 = False
                        if size != before.st_size or _fingerprint(before) != _fingerprint(
                            os.fstat(stream.fileno())
                        ):
                            raise ApplicationError(
                                "artifact_changed",
                                "The source file changed while being published.",
                                409,
                            )
                        # Re-walk to catch replaced files or newly introduced path symlinks.
                        with _open_regular(source) as current:
                            if _fingerprint(before) != _fingerprint(os.fstat(current.fileno())):
                                raise ApplicationError(
                                    "artifact_changed",
                                    "The source file changed while being published.",
                                    409,
                                )
                        mime = _mime(name, header, utf8)
                        target.flush()
                        os.fchmod(target.fileno(), 0o400)
                        os.fsync(target.fileno())
                    artifact = Artifact(
                        id=identity,
                        project_id=project_id,
                        task_id=run["task_id"],
                        run_id=run_id,
                        name=name,
                        title=title,
                        description=description,
                        mime=mime,
                        size=size,
                        created_at=now(),
                        href=f"/api/projects/{project_id}/artifacts/{identity}",
                    )
                    os.link(temporary, identity, src_dir_fd=directory, dst_dir_fd=directory)
                    published = True
                    os.unlink(temporary, dir_fd=directory)
                    os.fsync(directory)
                    db.execute(
                        "INSERT INTO artifacts (id,project_id,task_id,run_id,name,title,"
                        "description,mime,size,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            artifact.id,
                            artifact.project_id,
                            artifact.task_id,
                            artifact.run_id,
                            artifact.name,
                            artifact.title,
                            artifact.description,
                            artifact.mime,
                            artifact.size,
                            artifact.created_at,
                        ),
                    )
        except BaseException as error:
            if directory is not None:
                if published:
                    os.unlink(identity, dir_fd=directory)
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            if isinstance(error, OSError):
                raise _invalid_path() from error
            raise
        finally:
            if directory is not None:
                os.close(directory)
        if self.workspace.on_change:
            self.workspace.on_change(project_id)
        return artifact

    def list(
        self,
        project_id: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[Artifact]:
        with self.workspace.connection() as db:
            self.workspace._project(db, project_id)
            if run_id is not None:
                run = self._run(db, project_id, run_id)
                if (
                    task_id is not None
                    and self.workspace._task(db, project_id, task_id).id != run["task_id"]
                ):
                    raise ApplicationError("not_found", "Attempt not found for this task.", 404)
            if task_id is not None:
                task_id = self.workspace._task(db, project_id, task_id).id
            rows = db.execute(
                "SELECT * FROM artifacts WHERE project_id=? AND (? IS NULL OR run_id=?) "
                "AND (? IS NULL OR task_id=?) ORDER BY created_at,id",
                (project_id, run_id, run_id, task_id, task_id),
            ).fetchall()
            return [self._artifact(row) for row in rows]

    def _artifact(self, row: sqlite3.Row) -> Artifact:
        return Artifact(
            **dict(row), href=f"/api/projects/{row['project_id']}/artifacts/{row['id']}"
        )

    def get(self, project_id: str, artifact_id: str) -> Artifact:
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT * FROM artifacts WHERE project_id=? AND id=?", (project_id, artifact_id)
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Artifact not found in this project.", 404)
            return self._artifact(row)

    def stored_path(self, artifact: Artifact) -> Path:
        # Downloads only address server-generated IDs, never caller-supplied paths.
        if re.fullmatch(r"[a-f0-9]{32}", artifact.id) is None:
            raise ApplicationError("not_found", "Artifact not found.", 404)
        path = self.workspace.directory / "artifacts" / artifact.id
        try:
            with _open_regular(path) as stream:
                if os.fstat(stream.fileno()).st_size != artifact.size:
                    raise ApplicationError("not_found", "Artifact media is unavailable.", 404)
        except OSError as error:
            raise ApplicationError("not_found", "Artifact media is unavailable.", 404) from error
        return path
