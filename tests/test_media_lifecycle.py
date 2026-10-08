"""Browser ownership and publication follow attempt completion, not native turn completion."""

import asyncio

import pytest
from test_execution import fixture
from test_run_media import Browser
from test_supervisor import FakeWorker

from flowfield.adapters.git_workspace import baseline, git
from flowfield.artifacts import Artifacts
from flowfield.execution_models import RunAction
from flowfield.supervisor import Supervisor


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_supervisor_finalizes_browser_before_attempt_finish(tmp_path, monkeypatch, cleanup_fails):
    class RecordingWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            await self.on_tool("browser_open", {"record_video": True})
            return await super().run(model, effort, prompt, tools)

    monkeypatch.setattr("flowfield.adapters.codex_agent.CodexAgent", RecordingWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture")
    execution = fixture(tmp_path)
    repo = tmp_path / "harbor"
    git(repo, "init")
    (repo / "README.md").write_text("Browser test")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    base = baseline(repo)
    run = execution.claim("harbor", base, {base: set()})
    assert run
    service = Supervisor(execution.workspace)
    browser = Browser()
    browser.fail_close = cleanup_fails
    service.browsers = browser
    asyncio.run(service._execute(run, repo))
    current = execution.get("harbor", run.id)
    assert current.status == ("uncertain" if cleanup_fails else "in_review")
    metadata = execution.local(run.id)
    assert metadata["native_cleanup_confirmed"] is True
    assert metadata["browser_cleanup_confirmed"] is not cleanup_fails
    artifacts = Artifacts(execution.workspace).list("harbor", run_id=run.id)
    assert len(artifacts) == (0 if cleanup_fails else 1)


def test_native_cleanup_receipt_does_not_clear_lost_browser_ownership(tmp_path):
    execution = fixture(tmp_path)
    base = "a" * 40
    run = execution.claim("harbor", base, {base: set()})
    assert run
    execution.started("harbor", run.id)
    execution.save_local(
        run.id,
        {
            "runtime_kind": "local",
            "native_launch_started": True,
            "native_cleanup_confirmed": True,
            "browser_launch_started": True,
            "browser_cleanup_confirmed": False,
        },
    )
    run = execution.finish("harbor", run.id, "uncertain")
    service = Supervisor(execution.workspace)
    stopped = asyncio.run(service.stop("harbor", run.id, RunAction(expected_revision=run.revision)))
    assert stopped.status == "uncertain"
