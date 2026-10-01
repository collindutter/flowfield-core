"""Activity is durable evidence, separate from current task agreement and progress."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, existing_directory

from flowfield.activity import ActivityCreate
from flowfield.api import create_app
from flowfield.application import (
    ProjectSetup,
    TaskCreate,
    TaskEdit,
    TaskPriority,
    TaskProgress,
    Workspace,
)
from flowfield.errors import ApplicationError


def workspace(tmp_path: Path) -> Workspace:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    service.create_task("harbor", TaskCreate(id="one", title="Export CSV"))
    service.create_task("harbor", TaskCreate(id="two", title="Download CSV"))
    return service


def test_activity_does_not_change_agreement_and_important_events_are_atomic(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    original = service.task("harbor", "one")
    note = ActivityCreate(id="retry-note", task_id="HAR-1", body="**Finding:** reuse serializer.")
    entry = service.add_activity("harbor", note)
    assert service.add_activity("harbor", note) == entry
    with pytest.raises(ApplicationError, match="already used"):
        service.add_activity("harbor", note.model_copy(update={"body": "Different"}))
    task = service.task("harbor", "one")
    assert task.latest_update == entry
    assert task.revisions == original.revisions and task.updated_at == original.updated_at
    assert task.body == original.body and task.status == original.status
    service.edit_task("harbor", "one", TaskEdit(expected_revision=1, body="Agreed requirement"))
    service.prioritize_task("harbor", "one", TaskPriority(expected_revision=2, status="backlog"))
    # Description edits are recorded; ordering within a column is not.
    assert len(service.activity("harbor", task_id="one").items) == 3
    assert service.activity("harbor", task_id="one").items[0].body == "Description updated."
    service.record_progress(
        "harbor", "one", TaskProgress(expected_revision=3, status="in_progress")
    )
    assert service.activity("harbor", task_id="one").items[0].body == "Backlog → In progress."
    snapshot = service.activity("harbor", task_id="one")
    with pytest.raises(ApplicationError):
        service.edit_task("harbor", "one", TaskEdit(expected_revision=4, dependencies=["missing"]))
    assert service.activity("harbor", task_id="one") == snapshot
    service.record_progress(
        "harbor", "one", TaskProgress(expected_revision=4, status="done", completion="report")
    )
    service.edit_task("harbor", "one", TaskEdit(expected_revision=5, archived=True))
    service.add_activity(
        "harbor", ActivityCreate(task_id="one", body="Archived result remains useful.")
    )
    assert service.activity("harbor", task_id="one").items[1].body == "Task archived."
    assert Workspace(tmp_path / "state").activity("harbor", task_id="HAR-1") == service.activity(
        "harbor", task_id="one"
    )


def test_decision_scope_replacement_conflicts_and_current_reads(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    old = service.add_activity(
        "harbor", ActivityCreate(task_id="HAR-1", kind="decision", body="Visible columns only.")
    )
    project = service.add_activity(
        "harbor", ActivityCreate(kind="decision", body="Exports work offline.")
    )
    for scope in [None, "two"]:
        with pytest.raises(ApplicationError, match="same scope"):
            service.add_activity(
                "harbor",
                ActivityCreate(
                    task_id=scope, kind="decision", body="Invalid replacement", supersedes=old.id
                ),
            )

    def replace(body: str):
        try:
            return service.add_activity(
                "harbor",
                ActivityCreate(task_id="one", kind="decision", body=body, supersedes=old.id),
            )
        except ApplicationError as error:
            assert error.code == "decision_conflict"
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(replace, ["All columns, for fidelity.", "Selected columns, for privacy."])
        )
    winners = [result for result in results if result]
    assert len(winners) == 1
    current = winners[0]
    assert service.activity("harbor", task_id="HAR-1", current_only=True).items == [current]
    decisions = service.activity("harbor", task_id="one", kind="decision").items
    assert len(decisions) == 2 and decisions[1].superseded_by == current.id
    assert service.activity("harbor", current_only=True).items == [project]
    assert service.task("harbor", "one").body == ""  # Recording is not applying.
    adopt(service, ProjectSetup(path=str(tmp_path / "other")))
    with pytest.raises(ApplicationError, match="not found"):
        service.activity_entry("other", old.id)
    with pytest.raises(ApplicationError, match="not found"):
        service.activity("other", task_id="HAR-1")


def test_activity_pagination_is_stable_across_new_entries(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for number in range(5):
        service.add_activity("harbor", ActivityCreate(task_id="one", body=f"Finding {number}"))
    first = service.activity("harbor", task_id="one", limit=2)
    assert first.next_cursor
    service.add_activity("harbor", ActivityCreate(task_id="one", body="New while reading"))
    entries = first.items[:]
    cursor = first.next_cursor
    while cursor:
        page = service.activity("harbor", task_id="one", limit=2, before=cursor)
        entries.extend(page.items)
        cursor = page.next_cursor
    assert len(entries) == 6 and len({entry.id for entry in entries}) == 6
    assert [entry.body for entry in entries[:2]] == ["Finding 4", "Finding 3"]
    assert not any(entry.body == "New while reading" for entry in entries)
    with pytest.raises(ApplicationError):
        service.activity("harbor", limit=101)


def test_http_activity_validates_and_preserves_scope(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        client.post(
            "/api/projects/initialize", json={"path": existing_directory(str(tmp_path / "harbor"))}
        )
        client.post("/api/projects/harbor/tasks", json={"title": "CSV"})
        endpoint = "/api/projects/harbor/activity"
        for entry in [
            {"body": "project note"},
            {"task_id": "HAR-1", "body": "  "},
            {"task_id": "HAR-1", "kind": "event", "body": "Fake completion"},
            {"task_id": "HAR-1", "body": "Note", "supersedes": "old"},
        ]:
            assert client.post(endpoint, json=entry).status_code == 422
        response = client.post(
            endpoint,
            json={
                "task_id": "HAR-1",
                "kind": "decision",
                "body": "# Export\n\nInclude **all columns**.",
            },
        )
        assert response.status_code == 201
        entry = response.json()
        assert client.get(endpoint + "/" + entry["id"]).json() == entry
        assert client.get(endpoint, params={"task_id": "HAR-1", "current_only": True}).json()[
            "items"
        ] == [entry]
        assert client.get(endpoint).json()["items"] == []
        assert client.put(endpoint + "/" + entry["id"], json={"body": "rewrite"}).status_code == 404


def test_edit_events_record_changes_but_not_noops(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    task = service.create_task("harbor", TaskCreate(title="Next", status="up_next"))
    assert service.activity("harbor", task_id=task.id).items[0].body == "Task created in Up next."
    task = service.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=1, body="Done when clear.")
    )
    service.edit_task("harbor", task.id, TaskEdit(expected_revision=2, body=task.body))
    events = service.activity("harbor", task_id=task.id).items
    assert len(events) == 2 and events[0].task_revision == 2
    service.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=2, title="Clear next step", task_type="investigation"),
    )
    assert (
        service.activity("harbor", task_id=task.id).items[0].body
        == "Title updated.\n\nType updated."
    )


def test_withdrawal_preserves_decision_and_serializes_against_replacement(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from flowfield.activity import DecisionWithdraw

    service = workspace(tmp_path)
    old = service.add_activity("harbor", ActivityCreate(kind="decision", body="Offline only"))
    request = DecisionWithdraw(id="withdraw-offline", reason="No longer a requirement")
    withdrawn = service.withdraw_decision("harbor", old.id, request)
    assert service.withdraw_decision("harbor", old.id, request) == withdrawn
    original = service.activity_entry("harbor", old.id)
    assert original.body == "Offline only" and original.withdrawn_by == withdrawn.id
    assert original.superseded_by is None and withdrawn.withdraws == old.id
    assert not service.activity("harbor", current_only=True).items
    assert service.task("harbor", "HAR-1").revision == 1
    with pytest.raises(ApplicationError):
        service.add_activity(
            "harbor", ActivityCreate(kind="decision", body="Online", supersedes=old.id)
        )
    with pytest.raises(ApplicationError):
        service.withdraw_decision("elsewhere", old.id, DecisionWithdraw(reason="Wrong project"))
    raced = service.add_activity("harbor", ActivityCreate(kind="decision", body="Limit exports"))

    def change(withdraw: bool):
        try:
            if withdraw:
                service.withdraw_decision("harbor", raced.id, DecisionWithdraw(reason="No limit"))
            else:
                service.add_activity(
                    "harbor",
                    ActivityCreate(kind="decision", body="Higher limit", supersedes=raced.id),
                )
            return True
        except ApplicationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(change, [True, False])) == 1
    restored = Workspace(tmp_path / "state").activity_entry("harbor", old.id)
    assert restored.withdrawn_by == withdrawn.id
