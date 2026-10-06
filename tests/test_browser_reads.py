from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, task_request

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskEdit, TaskProgress, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError


def test_cards_and_current_details_never_hydrate_historical_text(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(
        project.id, task_request(title="Large description", body="x" * 200_000)
    )
    for index in range(6):
        task = workspace.edit_task(
            project.id,
            task.id,
            TaskEdit(
                expected_revision=task.revision,
                body=str(index) * 200_000,
            ),
        )
    blocked = workspace.create_task(
        project.id, task_request(title="Blocked", dependencies=[task.id])
    )
    task = workspace.edit_task(
        project.id, task.id, TaskEdit(expected_revision=task.revision, archived=True)
    )
    archived_at = task.archived_at
    task = workspace.edit_task(
        project.id, task.id, TaskEdit(expected_revision=task.revision, title="Edited after archive")
    )
    full = workspace.board(project.id)
    reads = BrowserReads(workspace)
    # Browser projections must not delegate to the full record/history reader.
    with patch.object(workspace, "_task", side_effect=AssertionError("Full task hydrated")):
        board = reads.board(project.id)
        assert len(board.model_dump_json()) < 5000
        assert len(full.model_dump_json()) > 1_000_000
        for card in board.tasks:
            original = next(t for t in full.tasks if t.id == card.id)
            assert card.state is not None
            assert card.model_dump(exclude={"state"}) == {
                key: original.model_dump()[key] for key in card.model_dump() if key != "state"
            }
        detail = reads.task(project.id, task.key)
        assert detail.body == task.body
        assert "revisions" not in detail.model_dump()
        assert detail.archived_at == archived_at
        assert detail.dependents[0].id == blocked.id
        assert reads.revision(project.id, task.key, 1).body == "x" * 200_000
        with pytest.raises(ApplicationError, match="revision not found"):
            reads.revision(project.id, task.id, 999)


def test_state_entry_and_rearchive_metadata_match_full_records(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(project.id, task_request(title="Task"))
    task = workspace.record_progress(
        project.id,
        task.id,
        TaskProgress(expected_revision=task.revision, status="done", completion="report"),
    )
    entered = task.status_changed_at
    for archived in [True, False, True]:
        task = workspace.edit_task(
            project.id, task.id, TaskEdit(expected_revision=task.revision, archived=archived)
        )
        card = BrowserReads(workspace).board(project.id).tasks[0]
        assert card.status_changed_at == entered
        assert card.archived_at == task.archived_at
        assert card.archived_by == task.archived_by
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/projects/{project.id}/view/board").status_code == 200
        detail = client.get(f"/api/projects/{project.id}/view/tasks/{task.key}").json()
        assert "body" in detail and "revisions" not in detail
        edited = client.put(
            f"/api/projects/{project.id}/view/tasks/{task.key}",
            json={
                "expected_revision": task.revision,
                "title": "Browser edit",
            },
        )
        assert edited.status_code == 200
        assert edited.json()["revision"] == task.revision + 1
        assert "revisions" not in edited.json()
        assert (
            client.get(f"/api/projects/{project.id}/view/tasks/{task.key}/revisions/1").json()[
                "revision"
            ]
            == 1
        )
        assert (
            client.get(
                f"/api/projects/{project.id}/view/tasks/{task.key}/revisions/999"
            ).status_code
            == 404
        )


def test_archive_availability_shares_write_policy_and_rechecks_races(tmp_path: Path) -> None:
    from flowfield.questions import QuestionCreate, Questions

    workspace = Workspace(tmp_path / "state")
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    task = workspace.create_task(project.id, task_request(title="Archive safely"))
    reads = BrowserReads(workspace)
    assert reads.task(project.id, task.id).archive_blocker is None
    # Even a nonblocking project question affecting this task prevents archiving.
    Questions(workspace).ask(
        project.id,
        QuestionCreate(
            question="Keep the finding?",
            context="Consider archive",
            recommendation="Keep it",
            affected_task_ids=[task.id],
        ),
    )
    blocked = reads.task(project.id, task.id)
    assert blocked.archive_blocker == "Resolve pending questions before archiving."
    with pytest.raises(ApplicationError) as error:
        workspace.edit_task(
            project.id, task.id, TaskEdit(expected_revision=task.revision, archived=True)
        )
    assert error.value.message == blocked.archive_blocker
    active = workspace.create_task(project.id, task_request(title="Active task"))
    active = workspace.record_progress(
        project.id, active.id, TaskProgress(expected_revision=active.revision, status="in_progress")
    )
    assert "Reconcile active work" in (reads.task(project.id, active.id).archive_blocker or "")
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        edited = client.put(
            f"/api/projects/{project.id}/view/tasks/{active.id}",
            json={"expected_revision": active.revision, "title": "Still active"},
        )
        assert edited.status_code == 200
        assert edited.json()["archive_blocker"] == reads.task(project.id, active.id).archive_blocker
