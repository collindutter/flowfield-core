"""Current Local configuration migrates without rewriting historical execution evidence."""

import asyncio
import json
from pathlib import Path

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
