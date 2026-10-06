"""Retirement preserves evidence without silently dropping prepared constraints."""

import json

from fastapi.testclient import TestClient
from project_fixtures import adopt

from flowfield import migrations
from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskCreate, TaskPreparation, Workspace
from flowfield.reads import ContextReads


def test_project_decisions_are_read_only_history_after_upgrade(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 41)
        )
        workspace = Workspace(directory)
        adopt(workspace, ProjectSetup(path=str(tmp_path / "project")))
        task = workspace.create_task(
            "project",
            TaskCreate(
                title="Inspect exports",
                body="Report findings.",
                preparation=TaskPreparation(expected_decision_sequence=0, completion="report"),
            ),
        )
        with workspace.connection(write=True, project_id="project") as db:
            decision = workspace._insert_activity(
                db,
                "project",
                None,
                "old-choice",
                "decision",
                "Keep exports offline.",
                "human",
                task.created_at,
            )
            row = json.loads(db.execute("SELECT data FROM tasks").fetchone()[0])
            row["publication"]["decision_sequence"] = decision.sequence
            before = json.dumps(row)
            db.execute("UPDATE tasks SET data=?", (before,))
            db.execute("UPDATE task_revisions SET data=? WHERE revision=?", (before, task.revision))
    upgraded = Workspace(directory)
    after = upgraded.task("project", task.id)
    assert after.revision == task.revision + 1
    assert after.body == task.body
    assert after.readiness == "needs_reconciliation"
    assert "Project decisions were retired" in after.reconciliation_reason
    assert after.decision_sequence == 0
    assert upgraded.activity("project").items[0] == decision
    assert not upgraded.activity("project", current_only=True).items
    with upgraded.connection() as db:
        assert (
            db.execute(
                "SELECT data FROM task_revisions WHERE revision=?", (task.revision,)
            ).fetchone()[0]
            == before
        )
    overview = ContextReads(upgraded).overview("project")
    assert "current_project_decisions" not in overview
    assert "decisions" not in overview["links"]
    assert Workspace(directory).task("project", task.id) == after
    with TestClient(create_app(data_dir=directory), base_url="http://127.0.0.1") as client:
        rejected = client.post(
            "/api/projects/project/activity",
            json={"kind": "decision", "body": "New project policy"},
        )
        assert rejected.status_code == 422
        assert (
            client.post(
                "/api/projects/project/activity/old-choice/withdraw", json={"reason": "Remove"}
            ).status_code
            == 404
        )
