import asyncio
import json
from pathlib import Path

import pytest
from project_fixtures import adopt

from flowfield.application import ProjectSetup, TaskCreate, TaskEdit, Workspace
from flowfield.changes import Changes
from flowfield.errors import ApplicationError


def test_change_scopes_coalesce_and_reconnect() -> None:
    async def check() -> None:
        changes = Changes()
        events = changes.events()
        assert json.loads(str((await anext(events))["data"])) == {"projects": None}
        changes.publish("alpha")
        changes.publish("beta")
        changes.publish("alpha")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"projects": ["alpha", "beta"]}
        changes.publish("alpha")
        changes.publish()
        changes.publish("beta")
        await asyncio.sleep(0)
        assert json.loads(str((await anext(events))["data"])) == {"projects": None}
        await events.aclose()
        assert not changes.subscribers
        reconnected = changes.events()
        assert json.loads(str((await anext(reconnected))["data"])) == {"projects": None}
        await reconnected.aclose()

    asyncio.run(check())


def test_only_committed_writes_publish_project(tmp_path: Path) -> None:
    published: list[str | None] = []
    workspace = Workspace(tmp_path / "state", on_change=published.append)
    project = adopt(workspace, ProjectSetup(path=str(tmp_path / "repo")))
    assert published == [None]
    published.clear()
    task = workspace.create_task(project.id, TaskCreate(title="Task"))
    assert published == [project.id]
    published.clear()
    with pytest.raises(ApplicationError):
        workspace.edit_task(project.id, task.id, TaskEdit(expected_revision=999, title="Stale"))
    assert not published
