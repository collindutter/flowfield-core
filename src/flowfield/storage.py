"""Exclusive upgrades and bounded, verified pre-upgrade database recovery snapshots."""

import fcntl
import hashlib
import os
import re
import shlex
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from flowfield import __version__, migrations
from flowfield.errors import ApplicationError

DATABASE = "workspace.sqlite3"
BACKUP_LIMIT = 3
BACKUP_TIMEOUT = 60.0


class Backup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: int = 1
    source_directory: str
    created_at: str
    schema_version: int
    workspace_id: str | None
    revision: int | None
    sha256: str
    baseline_digest: str | None = None


def acquire_lock(directory: Path, name: str, *, shared: bool = False) -> BinaryIO:
    lock = (directory / name).open("ab")
    try:
        fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except BaseException as error:
        lock.close()
        if isinstance(error, BlockingIOError):
            raise ApplicationError(
                "storage_busy",
                "Another Flowfield service or storage operation owns this "
                "workspace. Stop the service and retry.",
                409,
            ) from error
        raise
    return lock


@contextmanager
def maintenance(directory: Path) -> Iterator[None]:
    # The service uses this same execution lock, including during restart recovery.
    with acquire_lock(directory, ".execution.lock"), acquire_lock(directory, ".initialize.lock"):
        yield


def connect(database: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if database.is_symlink():
        raise ApplicationError("invalid_database", "Database must not be a symlink.")
    db = sqlite3.connect(
        database.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        timeout=5,
        isolation_level=None,
    )
    db.execute("PRAGMA foreign_keys = ON")
    return db


def version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def require_supported(value: int) -> None:
    latest = migrations.current_version()
    if not migrations.BASELINE_VERSION <= value <= latest:
        detail = (
            "Install a newer Flowfield build."
            if value > latest
            else ("Use its matching build or a fresh --data-dir.")
        )
        raise ApplicationError(
            "unsupported_schema",
            f"Unsupported database version {value}; this build supports "
            f"{migrations.BASELINE_VERSION}–{latest}. {detail} Existing data is preserved.",
            409,
        )


def require_current(db: sqlite3.Connection, expected: int) -> None:
    actual = version(db)
    if actual != expected:
        raise ApplicationError(
            "schema_changed",
            f"Database schema is {actual}; this workspace opened schema "
            f"{expected}. Reopen it with a compatible Flowfield build.",
            409,
        )


def integrity(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ApplicationError("invalid_database", "Database integrity check failed.", 409)
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ApplicationError("invalid_database", "Database contains broken references.", 409)


def identity(db: sqlite3.Connection) -> tuple[str | None, int | None]:
    if version(db) == migrations.BASELINE_VERSION:
        return None, None
    row = db.execute("SELECT workspace_id, revision FROM storage_metadata WHERE id=1").fetchone()
    if row is None:
        raise ApplicationError("invalid_database", "Database storage identity is missing.", 409)
    return str(row[0]), int(row[1])


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def baseline_digest(db: sqlite3.Connection) -> str:
    result = hashlib.sha256()
    for line in db.iterdump():
        result.update(line.encode())
        result.update(b"\n")
    return result.hexdigest()


def copy_database(source: sqlite3.Connection, target: Path) -> None:
    deadline = time.monotonic() + BACKUP_TIMEOUT

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise ApplicationError("backup_timeout", "Database backup timed out; retry when idle.")

    with target.open("xb"):
        target.chmod(0o600)
    with closing(sqlite3.connect(target, isolation_level=None)) as destination:
        source.backup(destination, pages=256, progress=progress, sleep=0.05)
        destination.execute("PRAGMA journal_mode = DELETE")
        integrity(destination)
    with target.open("rb") as stream:
        os.fsync(stream.fileno())


def sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(directory: Path, *, reason: str) -> str:
    backups = directory / "backups"
    if backups.is_symlink():
        raise ApplicationError("invalid_backup", "The backups directory must not be a symlink.")
    backups.mkdir(mode=0o700, exist_ok=True)
    name = f"{reason}-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
    temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=backups))
    try:
        with closing(connect(directory / DATABASE, readonly=True)) as source:
            copy_database(source, temporary / DATABASE)
        with closing(connect(temporary / DATABASE, readonly=True)) as saved:
            saved_version = version(saved)
            workspace_id, revision = identity(saved)
            info = Backup(
                source_directory=str(directory),
                created_at=datetime.now(UTC).isoformat(),
                schema_version=saved_version,
                workspace_id=workspace_id,
                revision=revision,
                sha256=digest(temporary / DATABASE),
                baseline_digest=baseline_digest(saved) if saved_version == 29 else None,
            )
        manifest = temporary / "manifest.json"
        with manifest.open("x") as stream:
            manifest.chmod(0o600)
            stream.write(info.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(temporary)
        temporary.rename(backups / name)
        sync_directory(backups)
        return name
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def backup_path(directory: Path, name: str) -> Path:
    if re.fullmatch(r"(?:upgrade|recovery)-\d{8}T\d{12}Z-[0-9a-f]{8}", name) is None:
        raise ApplicationError(
            "invalid_backup", "Choose a backup ID from `flowfield storage backups`."
        )
    path = directory / "backups" / name
    if any(p.is_symlink() for p in (path.parent, path, path / DATABASE, path / "manifest.json")):
        raise ApplicationError("invalid_backup", "Backup paths must not be symlinks.")
    return path


def read_backup(directory: Path, name: str) -> Backup:
    path = backup_path(directory, name)
    try:
        info = Backup.model_validate_json((path / "manifest.json").read_text())
        if info.format != 1 or info.source_directory != str(directory):
            raise ValueError("Backup belongs to another workspace directory or format")
        if digest(path / DATABASE) != info.sha256:
            raise ValueError("Backup checksum does not match")
        return info
    except (OSError, ValueError) as error:
        raise ApplicationError("invalid_backup", f"Cannot use backup {name}: {error}") from error


def backup_names(directory: Path) -> list[str]:
    root = directory / "backups"
    if not root.exists():
        return []
    if root.is_symlink():
        raise ApplicationError("invalid_backup", "The backups directory must not be a symlink.")
    return sorted(
        (
            p.name
            for p in root.iterdir()
            if re.fullmatch(r"(?:upgrade|recovery)-\d{8}T\d{12}Z-[0-9a-f]{8}", p.name)
        ),
        key=lambda name: name.split("-", 1)[1],
        reverse=True,
    )


def prune_backups(directory: Path, *, keep: set[str]) -> None:
    names = backup_names(directory)
    retained = set(keep)
    for name in names:
        if len(retained) < BACKUP_LIMIT:
            retained.add(name)
    for name in names:
        if name not in retained:
            path = backup_path(directory, name)
            shutil.rmtree(path)


def discard_incomplete_copies(directory: Path) -> None:
    """Only unpublished files owned by this format, with maintenance ownership held."""
    root = directory / "backups"
    if root.is_symlink():
        raise ApplicationError("invalid_backup", "The backups directory must not be a symlink.")
    if root.exists():
        for path in root.iterdir():
            if (
                re.fullmatch(r"\.pending-[a-z0-9_]{8}", path.name)
                and not path.is_symlink()
                and path.is_dir()
            ):
                shutil.rmtree(path)
    for path in directory.iterdir():
        if re.fullmatch(r"\.restore-[0-9a-f]{32}\.sqlite3(?:-(?:journal|wal|shm))?", path.name):
            path.unlink()


def migration_authorizer(
    action: int, arg1: str | None, arg2: str | None, database: str | None, trigger: str | None
) -> int:
    if action in (
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def validate_baseline(db: sqlite3.Connection, schema: str) -> None:
    query = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
    )
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(schema)
        if db.execute(query).fetchall() != expected.execute(query).fetchall():
            raise ApplicationError(
                "invalid_database",
                "Schema 29 does not match the Flowfield baseline. Existing data is preserved.",
                409,
            )


def initialize(directory: Path, schema: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = directory / DATABASE
    try:
        owner = acquire_lock(directory, ".execution.lock")
    except ApplicationError as busy:
        # Opening the current schema needs no maintenance. This also supports
        # cooperating application owners; starting a second Supervisor still fails.
        if not database.exists():
            raise
        with (
            acquire_lock(directory, ".initialize.lock", shared=True),
            closing(connect(database, readonly=True)) as db,
        ):
            if version(db) != migrations.current_version():
                raise busy
            identity(db)
        return
    with owner, acquire_lock(directory, ".initialize.lock"):
        if database.is_symlink():
            raise ApplicationError("invalid_database", "Workspace database must not be a symlink.")
        if not database.exists():
            database.touch(mode=0o600)
        with closing(sqlite3.connect(database, timeout=5, isolation_level=None)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            original = version(db)
            latest = migrations.current_version()
            if original:
                require_supported(original)
            elif db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                raise ApplicationError(
                    "unsupported_schema",
                    "Unversioned nonempty database; existing data is preserved.",
                    409,
                )
            discard_incomplete_copies(directory)
            if original == latest:
                identity(db)
                return
            backup: str | None = None
            try:
                if original:
                    db.execute("BEGIN IMMEDIATE")
                    integrity(db)
                    if original == migrations.BASELINE_VERSION:
                        validate_baseline(db, schema)
                    backup = snapshot(directory, reason="upgrade")
                    prune_backups(directory, keep={backup})
                else:
                    # executescript's implicit commit precedes our explicit BEGIN.
                    db.executescript("BEGIN IMMEDIATE;" + schema)
                for migration in migrations.MIGRATIONS:
                    if migration.version <= (original or migrations.BASELINE_VERSION):
                        continue
                    db.set_authorizer(migration_authorizer)
                    try:
                        migration.apply(db)
                    finally:
                        db.set_authorizer(None)
                    db.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                        (migration.version, datetime.now(UTC).isoformat(), __version__, backup),
                    )
                    db.execute(f"PRAGMA user_version = {migration.version}")
                integrity(db)
                db.commit()
            except BaseException as error:
                db.rollback()
                if isinstance(error, Exception):
                    recovery = (
                        (
                            f" Backup: {backup}. Inspect with flowfield --data-dir "
                            f"{shlex.quote(str(directory))} storage backups. "
                            f"Restore with flowfield --data-dir {shlex.quote(str(directory))} "
                            f"storage restore {backup} --confirm."
                        )
                        if backup
                        else ""
                    )
                    raise ApplicationError(
                        "migration_failed",
                        f"Database upgrade failed; changes were rolled back. {error}.{recovery}",
                    ) from error
                raise


def status(directory: Path) -> dict[str, Any]:
    if not (directory / DATABASE).exists():
        return {
            "database": str(directory / DATABASE),
            "schema_version": None,
            "supported_version": migrations.current_version(),
            "migration_required": False,
        }
    with (
        acquire_lock(directory, ".initialize.lock", shared=True),
        closing(connect(directory / DATABASE, readonly=True)) as db,
    ):
        actual = version(db)
    return {
        "database": str(directory / DATABASE),
        "schema_version": actual,
        "supported_version": migrations.current_version(),
        "migration_required": migrations.BASELINE_VERSION <= actual < migrations.current_version(),
    }


def backups(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    with acquire_lock(directory, ".initialize.lock", shared=True):
        return [
            {"id": name, **read_backup(directory, name).model_dump()}
            for name in backup_names(directory)
        ]


def restore(directory: Path, name: str) -> dict[str, str]:
    if not directory.is_dir():
        raise ApplicationError("missing_workspace", "Workspace directory does not exist.")
    with maintenance(directory):
        info = read_backup(directory, name)
        require_supported(info.schema_version)
        path = backup_path(directory, name) / DATABASE
        with closing(connect(path, readonly=True)) as saved:
            integrity(saved)
            if version(saved) != info.schema_version or identity(saved) != (
                info.workspace_id,
                info.revision,
            ):
                raise ApplicationError(
                    "invalid_backup", "Backup metadata does not match its database."
                )
        with closing(connect(directory / DATABASE)) as db:
            current = version(db)
            require_supported(current)
            workspace_id, revision = identity(db)
            if current == 29:
                safe = info.schema_version == 29 and baseline_digest(db) == info.baseline_digest
            elif info.schema_version == 29:
                first = db.execute(
                    "SELECT backup FROM schema_migrations WHERE version=30"
                ).fetchone()
                safe = revision == 0 and first is not None and first[0] == name
            else:
                safe = workspace_id == info.workspace_id and revision == info.revision
            if not safe:
                raise ApplicationError(
                    "restore_conflict",
                    "Workspace changed since this backup "
                    "or the backup belongs to another workspace. Restore refused.",
                    409,
                )
            safety = snapshot(directory, reason="recovery")
            prune_backups(directory, keep={name, safety})
            # Retire WAL before replacing the main file; never mix old WAL with a restore.
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] not in (0,):
                raise ApplicationError("storage_busy", "Database is busy; restore refused.", 409)
            db.execute("PRAGMA journal_mode = DELETE")
        temporary = directory / f".restore-{uuid4().hex}.sqlite3"
        try:
            with closing(connect(path, readonly=True)) as saved:
                copy_database(saved, temporary)
            os.replace(temporary, directory / DATABASE)
            sync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "restored": name,
            "safety_backup": safety,
            "message": "Pre-upgrade database restored. Start Flowfield to retry the upgrade.",
        }
