"""Ordered database-only upgrades from the Flowfield schema-44 baseline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

BASELINE_VERSION = 44


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


def artifact_storage(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE artifacts ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), "
        "task_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id), "
        "name TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, "
        "mime TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0 AND size <= 104857600), "
        "created_at TEXT NOT NULL, "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id))"
    )
    db.execute("CREATE INDEX artifacts_run ON artifacts(project_id,run_id,created_at,id)")
    db.execute("CREATE INDEX artifacts_task ON artifacts(project_id,task_id,created_at,id)")


MIGRATIONS: tuple[Migration, ...] = (Migration(45, artifact_storage),)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
