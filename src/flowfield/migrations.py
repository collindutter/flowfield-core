"""Ordered database-only upgrades from the Flowfield schema-44 baseline."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

BASELINE_VERSION = 44


@dataclass(frozen=True)
class Migration:
    version: int
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS: tuple[Migration, ...] = ()


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
