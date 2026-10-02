"""Ordered database-only upgrades from the immutable Flowfield schema-29 baseline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

BASELINE_VERSION = 29


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


def storage_identity(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE storage_metadata ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id TEXT NOT NULL, "
        "revision INTEGER NOT NULL CHECK (revision >= 0))"
    )
    db.execute("INSERT INTO storage_metadata VALUES (1, ?, 0)", (uuid4().hex,))
    db.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
        "applied_at TEXT NOT NULL, app_version TEXT NOT NULL, backup TEXT)"
    )


# Never edit an applied migration or the baseline SCHEMA constants. Append a migration.
# Use execute/executemany; the runner owns the transaction and schema version.
MIGRATIONS = (Migration(30, storage_identity),)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
