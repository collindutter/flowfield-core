import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, task_request

from flowfield.api import create_app
from flowfield.application import ProjectSetup, Workspace
from flowfield.notification_api import NotificationService
from flowfield.notifications import (
    RETAINED_NOTICES,
    Notifications,
    NotificationSettings,
    OperationNotice,
)
from flowfield.questions import QuestionCreate, Questions, QuestionWithdraw


def operation(key="test", message="A useful error"):
    return OperationNotice(key=key, title="Action failed", message=message, href="/", action="Open")


def test_shared_dismissal_and_reload_with_safe_clear_boundary(tmp_path):
    workspace = Workspace(tmp_path)
    first = Notifications(workspace)
    one = first.operation(operation()).items[0]
    assert Notifications(Workspace(tmp_path)).page().items == [one]
    first.dismiss(ids=[one.id])
    assert first.operation(operation()).items == []
    two = first.operation(operation(message="Different error")).items[0]
    three = first.operation(operation(key="third")).items[0]
    first.dismiss(ids=[], through=two.id)
    assert first.page().items == [three]
    assert Notifications(Workspace(tmp_path)).page().items == [three]


def test_delivery_claims_are_durable_and_exclusive_across_clients(tmp_path):
    workspace = Workspace(tmp_path)
    notices = Notifications(workspace)
    notices.operation(operation("baseline"))
    assert notices.claim_browser(deliver=True) == []
    notices.configure(NotificationSettings(browser_enabled=True))
    assert notices.claim_browser(deliver=True) == []
    expected = notices.operation(operation("new")).items[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(
            pool.map(lambda _: Notifications(workspace).claim_browser(deliver=True), range(4))
        )
    assert [item for claim in claims for item in claim] == [expected]
    assert Notifications(Workspace(tmp_path)).claim_browser(deliver=True) == []
    notices.operation(operation("focused"))
    assert notices.claim_browser(deliver=False) == []
    assert notices.claim_browser(deliver=True) == []
    notices.operation(operation("dismissed"))
    notices.dismiss(ids=[], through=notices.page().through)
    assert notices.claim_browser(deliver=True) == []


def test_retention_bounds_content_and_delivery_rows(tmp_path):
    workspace = Workspace(tmp_path)
    notices = Notifications(workspace)
    notices.configure(NotificationSettings(browser_enabled=True))
    for n in range(RETAINED_NOTICES + 20):
        notices.operation(operation(str(n)))
        notices.claim_browser(deliver=True)
    with workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM notifications").fetchone()[0] == RETAINED_NOTICES
        assert (
            db.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0]
            == RETAINED_NOTICES
        )


def test_attention_is_persistent_without_desktop_opt_in_and_resolves_from_work(tmp_path):
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "repo"), id="project"))
    workspace.create_task("project", task_request(id="task", title="Work"))
    questions = Questions(workspace)
    q = questions.ask(
        "project",
        QuestionCreate(
            id="choice",
            task_id="task",
            question="Which?",
            context="Context",
            recommendation="Small",
            blocking_scope="Scope",
        ),
    )
    notices = Notifications(workspace)
    notices.sync_attention()
    saved = notices.page().items[0]
    assert saved.source == "attention" and "choice" in saved.key
    notices.dismiss(ids=[saved.id])
    notices.sync_attention()
    assert not notices.page().items
    questions.withdraw(
        "project", q.id, QuestionWithdraw(expected_revision=q.revision, reason="Resolved")
    )
    notices.sync_attention()
    with workspace.connection() as db:
        assert db.execute(
            "SELECT resolved_at FROM notifications WHERE id=?", (saved.id,)
        ).fetchone()[0]


def test_api_persistence_settings_dismissal_and_input_validation(tmp_path):
    for restart in range(2):
        app = create_app(data_dir=tmp_path)
        with TestClient(app, base_url="http://localhost") as client:
            if not restart:
                result = client.post("/api/notifications/operations", json=operation().model_dump())
                assert result.status_code == 200
                assert (
                    client.put(
                        "/api/notifications/settings", json={"browser_enabled": True}
                    ).status_code
                    == 200
                )
                bad = operation().model_dump() | {"href": "javascript:alert(1)"}
                assert client.post("/api/notifications/operations", json=bad).status_code == 422
            notices = client.get("/api/notifications").json()
            assert len(notices["items"]) == 1
            assert client.get("/api/notifications/settings").json()["browser_enabled"]
            if restart:
                assert (
                    client.post(
                        "/api/notifications/dismiss", json={"through": notices["through"]}
                    ).json()["items"]
                    == []
                )


def test_cancelling_close_joins_notification_thread_before_releasing_owner(tmp_path, monkeypatch):
    from threading import Event

    entered, release = Event(), Event()

    async def scenario():
        service = NotificationService(Workspace(tmp_path))
        await service.start()

        def sync():
            entered.set()
            assert release.wait(5)

        monkeypatch.setattr(service.notifications, "sync_attention", sync)
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
