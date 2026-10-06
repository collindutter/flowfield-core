"""Current Local configuration migrates without rewriting historical execution evidence."""

import asyncio
import json
from pathlib import Path

import pytest
from test_managed_local import configured
from test_results import current, fixture

from flowfield.execution_models import RunAction
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare


def make_legacy(service, *, inventory=False):
    with service.workspace.connection(write=True, project_id="harbor") as db:
        raw = json.loads(
            db.execute(
                "SELECT data FROM integration_settings WHERE project_id='harbor'"
            ).fetchone()[0]
        )
        raw["runtime"] = "legacy"
        if inventory:
            raw["environment"] = {
                "tools": {"old": "/retired/machine/tool"},
                "read_paths": [],
                "variables": {"OLD": "retained"},
            }
        db.execute(
            "UPDATE integration_settings SET data=? WHERE project_id='harbor'", (json.dumps(raw),)
        )
    return service.integrations.settings("harbor")


@pytest.mark.parametrize("override", [True, False])
def test_schema34_upgrades_local_and_unifies_chat_without_rewriting_history(
    tmp_path, monkeypatch, override
):
    from flowfield import migrations
    from flowfield.agent_models import AgentChoice, AgentSettingsEdit
    from flowfield.agent_settings import AgentSettings
    from flowfield.application import Workspace, now
    from flowfield.coordinator_models import CoordinatorSend
    from flowfield.coordinator_store import CoordinatorStore
    from flowfield.execution import Execution
    from flowfield.integration import Integrations

    with monkeypatch.context() as older:
        older.setattr(
            migrations, "MIGRATIONS", [m for m in migrations.MIGRATIONS if m.version <= 34]
        )
        service, repo, run = fixture(tmp_path)
        old = make_legacy(service, inventory=True)
        settings = AgentSettings(service.workspace)
        settings.edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=1, selection=AgentChoice(model="default", effort="low")
            ),
        )
        store = CoordinatorStore(service.workspace)
        first = store.new("harbor")
        turn, _ = store.reserve(
            "harbor", first.id, CoordinatorSend(id="old-message-123456", text="Keep this")
        )
        with service.workspace.connection(write=True) as db:
            turn.status = "completed"
            turn.native_started = True
            store._save(db, turn)
            db.execute(
                "INSERT INTO coordinator_conversations(id,project_id,created_at) "
                "VALUES ('second','harbor',?)",
                (now(),),
            )
            db.execute(
                "INSERT INTO agent_settings VALUES ('harbor','coordinator','second',3,?)",
                (
                    AgentChoice(model="selected", effort="high").model_dump_json()
                    if override
                    else None,
                ),
            )
            db.execute(
                "UPDATE runs SET data=json_set(data,'$.runtime','legacy') WHERE id=?", (run.id,)
            )
        second, _ = store.reserve(
            "harbor", "second", CoordinatorSend(id="second-message-123456", text="Keep this too")
        )
        with service.workspace.connection(write=True) as db:
            second.status = "completed"
            store._save(db, second)
        before = Execution(service.workspace).get("harbor", run.id)
    upgraded = Workspace(service.workspace.directory)
    current_settings = Integrations(upgraded).settings("harbor")
    assert current_settings.runtime == "local" and current_settings.revision == old.revision + 1
    assert current_settings.environment == old.environment
    assert Execution(upgraded).get("harbor", run.id) == before
    assert AgentSettings(upgraded).get("harbor", "coordinator").selection.model == (
        "selected" if override else "default"
    )
    restored = CoordinatorStore(upgraded)
    assert restored.get("harbor", turn.id) == turn
    assert [item.id for item in restored.page("harbor").items] == [turn.id, second.id]
    assert restored.page("harbor").active is None
    assert restored.new("harbor").id == "second"
    # Reopening is idempotent; messages and original native/turn settings survive.
    assert Integrations(Workspace(upgraded.directory)).settings("harbor") == current_settings


def test_historical_workspaces_and_saved_inspections_remain_readable(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    inspections = Inspections(service.workspace)
    inspections.configure("harbor", InspectionConfig(expected_revision=1, run_command="echo saved"))
    copy = inspections.prepare(
        "harbor", InspectionPrepare(result_id=version.id, expected_revision=version.revision)
    )
    launcher = Path(copy.launcher).read_bytes()
    old = service.integrations.settings("harbor")
    # A historical record keeps its runtime meaning and already-created launcher.
    copy.runtime = "legacy"
    inspections._save(copy)
    saved = service.execution.local(run.id)
    if not saved:
        saved = {
            "root": str(tmp_path / "historical"),
            "checkout": str(tmp_path / "historical/worktree"),
            "runtime": str(tmp_path / "historical/runtime"),
            "common_git": str(repo / ".git"),
        }
    saved.pop("runtime_kind", None)
    saved["python_runtime"] = "/retained/original/runtime"
    service.execution.save_local(run.id, saved)
    location = service.location("harbor", run.id)
    assert location.workspace == saved["checkout"] and "runtime/python/bin" in location.try_command
    assert inspections.get("harbor", copy.id).status == "ready"
    assert inspections.get("harbor", copy.id).instructions_changed
    assert Path(copy.launcher).read_bytes() == launcher
    fresh = inspections.prepare(
        "harbor",
        InspectionPrepare(result_id=version.id, expected_revision=version.revision, new_copy=True),
    )
    assert fresh.runtime == "local" and fresh.id != copy.id
    assert service.integrations.settings("harbor") == old
    assert service.execution.local(run.id) == saved


def test_new_validation_uses_local_and_captured_result_survives(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    assert result.status == "ready", result.problem
    assert service.execution.get("harbor", run.id).result_commit == run.result_commit


def test_unconfirmed_historical_command_keeps_occupancy(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch)
    make_legacy(service)
    base = service.integrations.head("harbor")
    run = service.execution.claim("harbor", base, {})
    service.execution.save_local(run.id, {"commands": ["historical-command"]})
    service.execution.restart()
    active = service.execution.get("harbor", run.id)
    stopped = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=active.revision))
    )
    assert stopped.status == "uncertain"
    assert service.execution.occupancy("harbor").uncertain == 1
    assert service.execution.local(run.id)["commands"] == ["historical-command"]
