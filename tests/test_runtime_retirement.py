"""Retired execution cannot silently inherit host access; saved evidence remains usable."""

import asyncio
import json
from pathlib import Path

import pytest
from test_managed_local import configured
from test_results import current, fixture

from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit, RunAction
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.integration_models import IntegrationConfig
from flowfield.setup_validation import SetupCheckRequest


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


def test_legacy_queue_and_setup_require_adoption_without_allocating_work(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch)
    old = make_legacy(service, inventory=True)

    async def exercise():
        with pytest.raises(ApplicationError, match="Select Local"):
            await service.setup_validation.check(
                "harbor", SetupCheckRequest(expected_revision=old.revision)
            )
        assert service.setup_validation.get("harbor") is None
        await service.start()
        try:
            settings = service.execution.settings("harbor")
            service.execution.queue(
                "harbor", QueueEdit(expected_revision=settings.revision, enabled=True)
            )
            async with asyncio.timeout(5):
                while not service.execution.settings("harbor").problem:
                    await asyncio.sleep(0.05)
            assert "Select Local" in service.execution.settings("harbor").problem
            assert service.execution.page("harbor").items == []
            assert not (service.workspace.directory / "local-attempts").exists()
        finally:
            await service.close()

    asyncio.run(exercise())
    saved = service.integrations.configure(
        "harbor",
        IntegrationConfig(expected_revision=old.revision, target_branch="main", checks=["true"]),
    )
    assert saved.runtime == "legacy" and saved.environment == old.environment
    adopted = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local", expected_revision=saved.revision, target_branch="main", checks=["true"]
        ),
    )
    assert adopted.runtime == "local" and adopted.environment == old.environment


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
    old = make_legacy(service)
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
    assert (
        inspections.prepare(
            "harbor", InspectionPrepare(result_id=version.id, expected_revision=version.revision)
        ).id
        == copy.id
    )
    assert Path(copy.launcher).read_bytes() == launcher
    with pytest.raises(ApplicationError, match="Select Local"):
        inspections.prepare(
            "harbor",
            InspectionPrepare(
                result_id=version.id, expected_revision=version.revision, new_copy=True
            ),
        )
    assert service.integrations.settings("harbor") == old
    assert service.execution.local(run.id) == saved


def test_new_validation_is_blocked_but_captured_result_survives(tmp_path):
    service, repo, run = fixture(tmp_path)
    make_legacy(service)
    service.results.process("harbor")
    result = current(service)
    assert result.status == "blocked" and "Select Local" in result.problem
    assert service.execution.get("harbor", run.id).result_commit == run.result_commit
    assert not (service.workspace.directory / "integration-work/local-attempts").exists()


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
