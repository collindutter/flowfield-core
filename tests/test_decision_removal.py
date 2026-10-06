"""Removed Decisions are deleted, including search and frozen assignment copies."""

import json

from fastapi.testclient import TestClient
from project_fixtures import adopt, task_request

from flowfield import application, migrations
from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.search import Search


def test_upgrade_deletes_decisions_without_removing_tasks_or_answers(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 41)
        )
        old.setattr(
            application,
            "SCHEMA",
            application.SCHEMA.replace(
                "kind IN ('note', 'handoff', 'event')",
                "kind IN ('note', 'decision', 'handoff', 'event')",
            ).replace(
                "supersedes TEXT UNIQUE REFERENCES activity(id),",
                "withdraws TEXT UNIQUE REFERENCES activity(id), "
                "supersedes TEXT UNIQUE REFERENCES activity(id),",
            ),
        )
        workspace = Workspace(directory)
        adopt(workspace, ProjectSetup(path=str(tmp_path / "project")))
        task = workspace.create_task(
            "project", task_request(title="Inspect exports", body="Report findings.")
        )
        with workspace.connection(write=True, project_id="project") as db:
            for identity, scope, kind, body in [
                ("project-choice", None, "decision", "Removed project choice"),
                ("task-choice", task.id, "decision", "Removed task choice"),
                ("observation", task.id, "note", "Keep this observation"),
            ]:
                db.execute(
                    "INSERT INTO activity(id,project_id,task_id,kind,body,author,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (identity, "project", scope, kind, body, "human", task.created_at),
                )
            db.execute(
                "INSERT INTO activity(id,project_id,task_id,kind,body,author,created_at,withdraws) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "withdrawal",
                    "project",
                    task.id,
                    "event",
                    "Removed withdrawal",
                    "human",
                    task.created_at,
                    "task-choice",
                ),
            )
    upgraded = Workspace(directory)
    assert upgraded.task("project", task.id).body == task.body
    assert not Search(upgraded).page("project", "Removed", history="all")["items"]
    assert Search(upgraded).page("project", "observation")["items"]
    with upgraded.connection() as db:
        assert not db.execute("SELECT 1 FROM activity WHERE kind='decision'").fetchone()
        assert "withdraws" not in {row[1] for row in db.execute("PRAGMA table_info(activity)")}
    assert Workspace(directory).task("project", task.id) == upgraded.task("project", task.id)
    with TestClient(create_app(data_dir=directory), base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/api/projects/project/activity",
                json={"task_id": task.id, "kind": "decision", "body": "Removed feature"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/projects/project/activity/task-choice/withdraw", json={"reason": "Remove"}
            ).status_code
            == 404
        )
        assert "decision_sequence" not in json.dumps(
            client.get(f"/api/projects/project/tasks/{task.id}").json()
        )


def test_upgrade_removes_frozen_decision_context(tmp_path, monkeypatch):
    from test_execution import BASE, fixture

    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 43)
        )
        execution = fixture(tmp_path)
        run = execution.claim("harbor", BASE, {BASE: set()})
        assert run
        with execution.workspace.connection(write=True) as db:
            assignment = execution.assignment("harbor", run.id)
            assignment["decisions"] = '[{"body":"Remove this choice"}]'
            agreement = json.loads(assignment["agreement"])
            agreement["decision_sequence"] = 3
            assignment["agreement"] = json.dumps(agreement)
            db.execute(
                "UPDATE runs SET assignment=?,data=json_set(data,'$.decision_sequence',3) "
                "WHERE id=?",
                (json.dumps(assignment), run.id),
            )
            db.execute("UPDATE tasks SET data=json_set(data,'$.publication.decision_sequence',3)")
    upgraded = Workspace(execution.workspace.directory)
    with upgraded.connection() as db:
        row = db.execute("SELECT data,assignment FROM runs WHERE id=?", (run.id,)).fetchone()
        assert "decision_sequence" not in row[0] + row[1]
        assert "Remove this choice" not in row[1]
        assert '"decisions"' not in row[1]
        assert "decision_sequence" not in db.execute("SELECT data FROM tasks LIMIT 1").fetchone()[0]
