"""Publication records an informed check of an assignment, never permission to run."""

import sqlite3
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from flowfield.application import TaskRevision

PublicationStatus = Literal["draft", "published", "needs_reconciliation"]
Readiness = Literal["ready", "blocked", "draft", "needs_reconciliation"]


class Publication(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    agreement_revision: int
    decision_sequence: int
    task_revision: int
    author: str
    created_at: str
    completion: Literal["code", "report"] = "code"
    target_branch: str | None = None


def decision_sequence(db: sqlite3.Connection, project_id: str, task_id: str) -> int:
    """Include replacements and withdrawals; notes never invalidate publication."""
    return int(
        db.execute(
            "SELECT coalesce(max(sequence), 0) FROM activity WHERE project_id=? "
            "AND task_id=? AND (kind='decision' OR withdraws IS NOT NULL)",
            (project_id, task_id),
        ).fetchone()[0]
    )


def publication_status(task: "TaskRevision", decisions: int) -> PublicationStatus:
    check = task.publication
    if check is None:
        return "draft"
    if check.agreement_revision != task.agreement_revision or check.decision_sequence != decisions:
        return "draft" if task.status in ("backlog", "up_next") else "needs_reconciliation"
    return "published"


def stages_need_reconciliation(
    db: sqlite3.Connection, project_id: str, task_id: str, agreement_revision: int
) -> bool:
    row = db.execute(
        "SELECT json_extract(data,'$.agreement_revision'), json_array_length(data,'$.stages') "
        "FROM stage_plans WHERE project_id=? AND task_id=? ORDER BY revision DESC LIMIT 1",
        (project_id, task_id),
    ).fetchone()
    return bool(row and row[1] and row[0] != agreement_revision)


def readiness(
    task: "TaskRevision",
    publication: PublicationStatus,
    *,
    blocked: bool,
    stages_stale: bool = False,
) -> Readiness:
    if task.reconciliation_reason:
        return "needs_reconciliation"
    if publication == "needs_reconciliation" and task.status not in ("backlog", "up_next"):
        return "needs_reconciliation"
    if stages_stale and not task.archived and task.status != "done":
        return "needs_reconciliation"
    if blocked:
        return "blocked"
    if task.status in ("backlog", "up_next") and publication != "published":
        return "draft"
    return "ready"


def preparation_issue(
    db: sqlite3.Connection,
    task: "TaskRevision",
    decisions: int,
    *,
    include_draft_hint: bool = True,
) -> str | None:
    """Human next action without exposing the snapshot protocol or implying agent wake-up."""
    # Browser labels own the generic draft hint; concrete blockers stay visible everywhere.
    if task.archived:
        return None
    if task.reconciliation_reason:
        return task.reconciliation_reason
    if task.status != "done" and stages_need_reconciliation(
        db, task.project_id, task.id, task.agreement_revision
    ):
        return (
            "Stages describe an older agreement. Resume your coordinator to reconcile the stages."
        )
    state = publication_status(task, decisions)
    if state == "needs_reconciliation":
        return "Decisions changed. Resume your coordinator to reconcile this work."
    if state == "published" or task.status not in ("backlog", "up_next"):
        return None
    has_description = db.execute(
        "SELECT length(trim(json_extract(data, '$.body'), char(9)||char(10)||char(13)||' ')) "
        "FROM tasks WHERE project_id=? AND id=?",
        (task.project_id, task.id),
    ).fetchone()[0]
    if not has_description:
        return "Describe the requested outcome with your coordinator."
    if task.publication and task.publication.decision_sequence != decisions:
        return "Decisions changed. Resume your coordinator to reconcile this work."
    if task.publication:
        return "Requirements changed. Resume your coordinator to prepare this work."
    return "Resume your coordinator to prepare the agreed work." if include_draft_hint else None
