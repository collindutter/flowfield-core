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
def persistent_notifications(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, data TEXT NOT NULL, "
        "created_at TEXT NOT NULL, dismissed_at TEXT, resolved_at TEXT)"
    )
    db.execute(
        "CREATE TABLE notification_deliveries (notification_id INTEGER NOT NULL "
        "REFERENCES notifications(id) ON DELETE CASCADE, channel TEXT NOT NULL, "
        "claimed_at TEXT NOT NULL, PRIMARY KEY(notification_id, channel))"
    )
    db.execute("CREATE TABLE notification_state (name TEXT PRIMARY KEY, data TEXT NOT NULL)")


def agent_preferences_and_permissions(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE agent_settings (project_id TEXT NOT NULL REFERENCES projects(id), "
        "role TEXT NOT NULL CHECK(role IN ('worker','coordinator')), scope TEXT NOT NULL, "
        "revision INTEGER NOT NULL, selection TEXT, PRIMARY KEY(project_id,role,scope))"
    )
    db.execute(
        "CREATE TABLE agent_permissions (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), "
        "task_id TEXT, binding TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, "
        "FOREIGN KEY(project_id,task_id) REFERENCES tasks(project_id,id))"
    )
    db.execute("CREATE INDEX permissions_project ON agent_permissions(project_id,task_id,number)")
    db.execute("CREATE INDEX permissions_binding ON agent_permissions(binding,status)")


def explicit_local_runtime(db: sqlite3.Connection) -> None:
    # Existing settings and attempts never imply consent to host inheritance.
    for table in ("integration_settings", "runs", "inspections"):
        db.execute(
            f"UPDATE {table} SET data=json_set(data,'$.runtime','legacy') "
            "WHERE json_type(data,'$.runtime') IS NULL"
        )


def coordinator_chat(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE coordinator_conversations (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), "
        "created_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE coordinator_turns (number INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL REFERENCES projects(id), "
        "conversation_id TEXT NOT NULL REFERENCES coordinator_conversations(id), "
        "status TEXT NOT NULL, data TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX coordinator_projects ON coordinator_conversations(project_id,number)")
    db.execute("CREATE INDEX coordinator_history ON coordinator_turns(conversation_id,number)")
    db.execute(
        "CREATE UNIQUE INDEX coordinator_active ON coordinator_turns(project_id) "
        "WHERE status IN ('starting','running','stopping','uncertain')"
    )


def project_coordinator_and_local_default(db: sqlite3.Connection) -> None:
    # Current configuration now uses the only supported environment automatically.
    # Frozen runs/inspection copies keep their recorded runtime.
    projects = db.execute(
        "SELECT project_id FROM integration_settings "
        "WHERE json_extract(data,'$.runtime') IS NOT 'local'"
    ).fetchall()
    for (project,) in projects:
        db.execute(
            "UPDATE integration_settings SET data=json_set(data,'$.runtime','local',"
            "'$.revision',coalesce(json_extract(data,'$.revision'),1)+1) WHERE project_id=?",
            (project,),
        )
        db.execute(
            "UPDATE runs SET data=json_set(data,'$.code_available',json('false'),"
            "'$.revision',json_extract(data,'$.revision')+1) WHERE project_id=? "
            "AND status='accepted' AND json_extract(data,'$.code_available')=1",
            (project,),
        )
    # Preserve every message, turn setting and original conversation identity.
    # Only the mutable coordinator selection moves to its single project owner.
    for (project,) in db.execute("SELECT id FROM projects").fetchall():
        selected = db.execute(
            "SELECT a.selection FROM coordinator_conversations c LEFT JOIN agent_settings a "
            "ON c.id=a.scope AND c.project_id=a.project_id AND a.role='coordinator' "
            "WHERE c.project_id=? "
            "ORDER BY c.number DESC LIMIT 1",
            (project,),
        ).fetchone()
        if selected and selected[0]:
            db.execute(
                "INSERT INTO agent_settings VALUES (?,'coordinator','',1,?) "
                "ON CONFLICT(project_id,role,scope) DO UPDATE SET "
                "selection=excluded.selection,revision=agent_settings.revision+1",
                (project, selected[0]),
            )
    db.execute("CREATE INDEX coordinator_project_messages ON coordinator_turns(project_id,number)")


MIGRATIONS = (
    Migration(30, storage_identity),
    Migration(31, persistent_notifications),
    Migration(32, agent_preferences_and_permissions),
    Migration(33, explicit_local_runtime),
    Migration(34, coordinator_chat),
    Migration(35, project_coordinator_and_local_default),
)


def current_version() -> int:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions))):
        raise RuntimeError("Database migrations must be contiguous and ordered.")
    return versions[-1] if versions else BASELINE_VERSION
