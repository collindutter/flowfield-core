"""Removing a feature must not silently remove its saved data."""

import sqlite3
from pathlib import Path

import pytest

from flowfield.application import Workspace


@pytest.mark.parametrize("version", list(range(1, 29)))
def test_old_state_is_preserved_without_migration(tmp_path: Path, version: int) -> None:
    database = tmp_path / "workspace.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE saved (body TEXT); INSERT INTO saved VALUES ('Keep me'); "
            f"PRAGMA user_version = {version};"
        )
    original = database.read_bytes()
    with pytest.raises(RuntimeError, match="fresh --data-dir"):
        Workspace(tmp_path)
    assert database.read_bytes() == original
