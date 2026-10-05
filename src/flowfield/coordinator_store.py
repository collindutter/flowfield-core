"""Transactional chat ownership, bounded public output and retained history."""

import sqlite3
from uuid import uuid4

from flowfield.agent_settings import AgentSettings
from flowfield.application import Workspace, now
from flowfield.coordinator_models import (
    CoordinatorConversation,
    CoordinatorHistory,
    CoordinatorPage,
    CoordinatorSend,
    CoordinatorTurn,
)
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.run_activity import ActivityUpdate, update_activity


class CoordinatorStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @staticmethod
    def _conversation(
        db: sqlite3.Connection, project: str, identity: str
    ) -> CoordinatorConversation:
        row = db.execute(
            "SELECT id,number,created_at FROM coordinator_conversations "
            "WHERE project_id=? AND id=?",
            (project, identity),
        ).fetchone()
        if row is None:
            raise ApplicationError("conversation_missing", "Conversation not found.", 404)
        return CoordinatorConversation(id=row[0], number=row[1], created_at=row[2])

    @staticmethod
    def _get(db: sqlite3.Connection, project: str, identity: str) -> CoordinatorTurn:
        row = db.execute(
            "SELECT data FROM coordinator_turns WHERE project_id=? AND id=?", (project, identity)
        ).fetchone()
        if row is None:
            raise ApplicationError("turn_missing", "Coordinator turn not found.", 404)
        return CoordinatorTurn.model_validate_json(row[0])

    @staticmethod
    def _save(db: sqlite3.Connection, turn: CoordinatorTurn) -> None:
        db.execute(
            "UPDATE coordinator_turns SET status=?,data=? WHERE id=?",
            (turn.status, turn.model_dump_json(), turn.id),
        )

    @staticmethod
    def _active(db: sqlite3.Connection, project: str) -> CoordinatorTurn | None:
        row = db.execute(
            "SELECT data FROM coordinator_turns WHERE project_id=? "
            "AND status IN ('starting','running','stopping','uncertain')",
            (project,),
        ).fetchone()
        return CoordinatorTurn.model_validate_json(row[0]) if row else None

    def history(self, project: str, before: int | None = None) -> CoordinatorHistory:
        with self.workspace.connection() as db:
            self.workspace._project(db, project)
            rows = db.execute(
                "SELECT id,number,created_at FROM coordinator_conversations WHERE project_id=? "
                "AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT 21",
                (project, before, before),
            ).fetchall()
            items = [
                CoordinatorConversation(id=r[0], number=r[1], created_at=r[2]) for r in rows[:20]
            ]
            return CoordinatorHistory(
                items=items, next_before=items[-1].number if len(rows) > 20 else None
            )

    def new(self, project: str) -> CoordinatorConversation:
        with self.workspace.connection(write=True, project_id=project) as db:
            self.workspace._project(db, project)
            if self._active(db, project):
                raise ApplicationError(
                    "coordinator_busy", "Finish or stop the current turn first.", 409
                )
            identity = uuid4().hex
            db.execute(
                "INSERT INTO coordinator_conversations(id,project_id,created_at) VALUES (?,?,?)",
                (identity, project, now()),
            )
            db.execute(
                "INSERT INTO agent_settings VALUES (?,'coordinator',?,1,NULL)", (project, identity)
            )
            return self._conversation(db, project, identity)

    def page(self, project: str, conversation: str, before: int | None = None) -> CoordinatorPage:
        with self.workspace.connection() as db:
            owner = self._conversation(db, project, conversation)
            rows = db.execute(
                "SELECT data FROM coordinator_turns WHERE conversation_id=? "
                "AND (? IS NULL OR number<?) ORDER BY number DESC LIMIT 21",
                (conversation, before, before),
            ).fetchall()
            items = [CoordinatorTurn.model_validate_json(row[0]) for row in rows[:20]]
            return CoordinatorPage(
                conversation=owner,
                items=list(reversed(items)),
                next_before=items[-1].number if len(rows) > 20 else None,
                active=self._active(db, project),
            )

    def reserve(
        self, project: str, conversation: str, request: CoordinatorSend, *, available: bool = True
    ) -> tuple[CoordinatorTurn, bool]:
        with self.workspace.connection(write=True, project_id=project) as db:
            self._conversation(db, project, conversation)
            row = db.execute(
                "SELECT project_id FROM coordinator_turns WHERE id=?", (request.id,)
            ).fetchone()
            if row:
                if row[0] != project:
                    raise ApplicationError(
                        "message_conflict", "Message identity is already used.", 409
                    )
                old = self._get(db, project, request.id)
                if old.text != request.text or old.conversation_id != conversation:
                    raise ApplicationError(
                        "message_conflict", "This message identity has different content.", 409
                    )
                return old, False
            if not available:
                raise ApplicationError(
                    "coordinator_capacity",
                    "All coordinator slots are busy. Try again shortly.",
                    409,
                )
            if self._active(db, project):
                raise ApplicationError(
                    "coordinator_busy", "The coordinator already has an active turn.", 409
                )
            latest = db.execute(
                "SELECT id FROM coordinator_conversations WHERE project_id=? "
                "ORDER BY number DESC LIMIT 1",
                (project,),
            ).fetchone()
            if latest[0] != conversation:
                raise ApplicationError(
                    "conversation_closed", "Send messages in the current conversation.", 409
                )
            if not request.text.strip():
                raise ApplicationError("empty_message", "Write a message first.")
            Integrations(self.workspace)._settings(db, project).require_local()
            settings = (
                AgentSettings(self.workspace)
                .resolve(db, project, "coordinator", conversation)
                .effective
            )
            if settings is None:
                raise ApplicationError(
                    "coordinator_settings",
                    "Choose a coordinator model and effort in settings first.",
                    409,
                )
            if settings.choice.mode is not None:
                raise ApplicationError(
                    "coordinator_read_only",
                    "Save coordinator model and effort again; file access is fixed to read-only.",
                    409,
                )
            turn = CoordinatorTurn(
                id=request.id,
                project_id=project,
                conversation_id=conversation,
                text=request.text,
                settings=settings,
                created_at=now(),
            )
            result = db.execute(
                "INSERT INTO coordinator_turns(id,project_id,conversation_id,status,data) "
                "VALUES (?,?,?,?,?)",
                (turn.id, project, conversation, turn.status, turn.model_dump_json()),
            )
            turn.number = result.lastrowid or 0
            self._save(db, turn)
            return turn, True

    def get(self, project: str, identity: str) -> CoordinatorTurn:
        with self.workspace.connection() as db:
            return self._get(db, project, identity)

    def write(self, project: str, identity: str, updates: list[ActivityUpdate]) -> None:
        with self.workspace.connection(write=True, notify=False) as db:
            turn = self._get(db, project, identity)
            if turn.status not in ("starting", "running", "stopping"):
                return
            update_activity(turn.activity, updates)
            self._save(db, turn)

    def restart(self) -> None:
        with self.workspace.connection(write=True) as db:
            rows = db.execute(
                "SELECT data FROM coordinator_turns "
                "WHERE status IN ('starting','running','stopping')"
            ).fetchall()
            for row in rows:
                turn = CoordinatorTurn.model_validate_json(row[0])
                turn.status = "uncertain" if turn.native_started else "interrupted"
                turn.notice = "The service restarted during this turn. Nothing was replayed. " + (
                    "Stop any remaining coordinator process on the host, then confirm it stopped."
                    if turn.native_started
                    else "Send a new message to continue."
                )
                self._save(db, turn)

    def confirm_stopped(self, project: str, identity: str) -> CoordinatorTurn:
        with self.workspace.connection(write=True, project_id=project) as db:
            turn = self._get(db, project, identity)
            if turn.status != "uncertain":
                raise ApplicationError("turn_changed", "This turn no longer needs recovery.", 409)
            turn.status = "interrupted"
            turn.notice = "You confirmed the coordinator stopped. Nothing was replayed."
            self._save(db, turn)
            return turn
