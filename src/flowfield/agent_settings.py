"""One resolution path; existing worker defaults and queue revisions retain their owner."""

import sqlite3
from uuid import uuid4

from flowfield.agent_models import (
    AgentChoice,
    AgentRole,
    AgentSettingsEdit,
    AgentSettingsView,
    EffectiveAgent,
)
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution_models import WorkerSettings


class AgentSettings:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _read(
        self, db: sqlite3.Connection, project: str, role: AgentRole, scope: str = ""
    ) -> AgentSettingsView:
        self.workspace._project(db, project)
        if scope and role == "worker":
            scope = self.workspace._task(db, project, scope).id
        if role == "worker" and not scope:
            row = db.execute(
                "SELECT data FROM worker_settings WHERE project_id=?", (project,)
            ).fetchone()
            current = (
                WorkerSettings.model_validate_json(row[0])
                if row
                else WorkerSettings(project_id=project)
            )
            return AgentSettingsView(
                revision=current.revision,
                selection=AgentChoice(model=current.model, effort=current.effort, mode=current.mode)
                if current.model and current.effort
                else None,
            )
        if self.workspace.schema_version < 32:
            return AgentSettingsView()  # Baseline construction for migration verification.
        row = db.execute(
            "SELECT revision,selection FROM agent_settings WHERE project_id=? AND role=? "
            "AND scope=?",
            (project, role, scope),
        ).fetchone()
        if role == "coordinator" and scope and row is None:
            raise ApplicationError("conversation_missing", "Conversation settings not found.", 404)
        return (
            AgentSettingsView(
                revision=row[0],
                selection=AgentChoice.model_validate_json(row[1]) if row[1] else None,
            )
            if row
            else AgentSettingsView()
        )

    def resolve(
        self, db: sqlite3.Connection, project: str, role: AgentRole, scope: str = ""
    ) -> AgentSettingsView:
        defaults = self._read(db, project, role)
        override = self._read(db, project, role, scope) if scope else None
        selected = override.selection if override and override.selection else defaults.selection
        result = (override or defaults).model_copy()
        if selected:
            result.effective = EffectiveAgent(
                choice=selected,
                source="override" if override and override.selection else "project",
                default_revision=defaults.revision,
                override_revision=override.revision if override else None,
            )
        return result

    def get(self, project: str, role: AgentRole, scope: str = "") -> AgentSettingsView:
        with self.workspace.connection() as db:
            return self.resolve(db, project, role, scope)

    def edit(
        self, project: str, role: AgentRole, request: AgentSettingsEdit, scope: str = ""
    ) -> AgentSettingsView:
        if role == "coordinator" and request.selection and request.selection.mode is not None:
            raise ApplicationError(
                "coordinator_read_only",
                "Coordinator Chat requires read-only filesystem access.",
                409,
            )
        if role == "worker" and not scope:
            raise ApplicationError(
                "worker_defaults", "Use worker settings to edit project defaults."
            )
        with self.workspace.connection(write=True, project_id=project) as db:
            if role == "worker" and scope:
                scope = self.workspace._task(db, project, scope).id
            current = self._read(db, project, role, scope)
            self.workspace._current(current.revision, request.expected_revision)
            db.execute(
                "INSERT INTO agent_settings VALUES (?,?,?,?,?) "
                "ON CONFLICT(project_id,role,scope) DO UPDATE SET "
                "revision=excluded.revision,selection=excluded.selection",
                (
                    project,
                    role,
                    scope,
                    current.revision + 1,
                    request.selection.model_dump_json() if request.selection else None,
                ),
            )
            return self.resolve(db, project, role, scope)

    def new_conversation(self, project: str) -> str:
        """Reserve settings identity for a service-owned conversation; launches nothing."""
        scope = uuid4().hex
        with self.workspace.connection(write=True, project_id=project) as db:
            self.workspace._project(db, project)
            db.execute(
                "INSERT INTO agent_settings VALUES (?,'coordinator',?,1,NULL)", (project, scope)
            )
        return scope
