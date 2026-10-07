"""Initial intent and stages are one write; progress never grants completion."""

import json

import pytest
from project_fixtures import adopt
from pydantic import ValidationError

from flowfield.application import ProjectSetup, TaskCreate, TaskEdit, TaskPreparation, Workspace
from flowfield.errors import ApplicationError
from flowfield.stage_models import Stage, StageChange
from flowfield.stages import Stages


def request(**changes):
    return TaskCreate(
        title="Explain the failure",
        body="Report the cause and a reproducer.",
        stages=[Stage(id="diagnose", title="Diagnose", outcome="Identify the cause")],
        **changes,
    )


def test_stages_required_and_atomic_preparation_rollback(tmp_path):
    with pytest.raises(ValidationError):
        TaskCreate(title="Missing plan")
    with pytest.raises(ValidationError):
        TaskCreate(title="Empty plan", stages=[])
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "project")))
    notices = []
    workspace.on_change = notices.append
    preparation = TaskPreparation(completion="report")
    task = workspace.create_task("project", request(preparation=preparation))
    stages = Stages(workspace)
    initial = stages.get("project", task.id)
    assert len(notices) == 1 and initial.revision == 1
    assert initial.stages[0].status == "planned"
    assert task.publication_status == "published" and task.status == "backlog"
    notices.clear()
    change = StageChange(
        expected_revision=0,
        stages=[Stage(id="diagnose", title="Diagnose", outcome="Explain the new failure")],
        reason="Refined the scope",
    )
    with pytest.raises(ApplicationError):
        workspace.edit_task(
            "project",
            task.id,
            TaskEdit(
                expected_revision=task.revision,
                body="Explain the new failure.",
                stages=change,
                preparation=preparation,
            ),
        )
    assert workspace.task("project", task.id) == task
    assert stages.get("project", task.id) == initial and not notices
    edited = workspace.edit_task(
        "project",
        task.id,
        TaskEdit(
            expected_revision=task.revision,
            body="Explain the new failure.",
            stages=change.model_copy(update={"expected_revision": 1}),
            preparation=preparation,
        ),
    )
    assert len(notices) == 1 and edited.readiness == "ready"
    plan = stages.get("project", task.id)
    assert plan.revision == 2 and plan.agreement_revision == edited.agreement_revision
    assert plan.stages[0].outcome == "Explain the new failure"
    with workspace.connection() as db:
        assert (
            json.loads(db.execute("SELECT data FROM stage_plans WHERE revision=1").fetchone()[0])[
                "stages"
            ][0]["outcome"]
            == "Identify the cause"
        )
