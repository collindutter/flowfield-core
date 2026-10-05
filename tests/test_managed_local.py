"""Production Local/ACP orchestration with real Git, subprocesses and scoped MCP; no models."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_execution import fixture

from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import TaskPublish
from flowfield.execution_models import RunAction, SettingsEdit
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.integration_models import IntegrationConfig
from flowfield.permission_models import PermissionAnswer
from flowfield.setup_validation import SetupCheckRequest
from flowfield.supervisor import Supervisor

FAKE = Path(__file__).with_name("fake_acp.py")


def configured(tmp_path, monkeypatch, *, count=1, scenario="normal", flags=()):
    execution = fixture(tmp_path, count=count, cap=count)
    repo = tmp_path / "harbor"
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", scenario)
    monkeypatch.setenv("FLOWFIELD_TEST_SECRET", "do-not-persist-host-secrets")
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda directory, env: (
            [sys.executable, str(FAKE), "managed", "cleanup", "close-session", *flags],
            dict(env),
        ),
    )
    service = Supervisor(execution.workspace)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            runtime="local",
            checks=["test -f base.txt"],
            setup_commands=['test -n "$HOME" && test -n "$FLOWFIELD_RUNTIME_DIR"'],
        ),
    )
    workers = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=workers.revision,
            model="test-model",
            effort="low",
            mode="workspace-write",
            max_parallel=count,
        ),
    )
    for task in execution.workspace.tasks("harbor"):
        execution.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                completion="code",
                expected_revision=task.revision,
                expected_decision_sequence=task.decision_sequence,
            ),
        )
    return service, repo, settings


def test_local_workers_validate_and_inspect_with_host_tools(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch, count=2)
    base = baseline(repo)

    async def exercise():
        assert (await service.model_options())[0].modes[0].id == "workspace-write"
        checked = await service.setup_validation.check(
            "harbor", SetupCheckRequest(expected_revision=settings.revision)
        )
        assert checked.status == "passed", checked.problem
        runs = [service.execution.claim("harbor", base, {base: set()}) for _ in range(2)]
        assert all(runs)
        await asyncio.gather(*(service._execute(run, repo) for run in runs))
        for run in runs:
            current = service.execution.get("harbor", run.id)
            assert current.status == "in_review", current.problem
            assert (
                current.runtime == "local"
                and current.agent_settings.choice.mode == "workspace-write"
            )
            metadata = service.execution.local(run.id)
            assert metadata["native_cleanup_confirmed"]
            assert "do-not-persist-host-secrets" not in json.dumps(metadata)
            environment = service.environment(run.id)
            assert (environment.checkout / "result.txt").read_text() == run.id
            assert not (environment.runtime / "python").exists()
            assert current.usage.total_tokens is None
        assert runs[0].id != runs[1].id and baseline(repo) == base
        await asyncio.to_thread(service.results.process, "harbor")
        await asyncio.to_thread(service.results.process, "harbor")
        result = service.results.page("harbor", runs[0].task_id).items[0]
        assert result.status == "ready", result.problem
        inspections = Inspections(service.workspace)
        inspections.configure(
            "harbor",
            InspectionConfig(expected_revision=1, run_command='test -n "$FLOWFIELD_RUNTIME_DIR"'),
        )
        copy = await asyncio.to_thread(
            inspections.prepare,
            "harbor",
            InspectionPrepare(result_id=result.id, expected_revision=result.revision),
        )
        assert copy.status == "ready" and copy.runtime == "local"
        launcher = Path(copy.launcher).read_text()
        assert "do-not-persist-host-secrets" not in launcher and "export HOME=" not in launcher
        assert service.workspace.task("harbor", runs[0].task_id).status != "done"
        await service.close()

    asyncio.run(exercise())


def test_native_permission_is_durable_and_does_not_approve_result(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario="permission")
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.02)
            pending = service.permissions.page("harbor").pending[0]
            assert "command: inspect project" in pending.details
            assert "Before:\nbefore" in pending.details and "After:\nafter" in pending.details
            service.permissions.answer(
                "harbor",
                pending.id,
                PermissionAnswer(expected_revision=pending.revision, option_id="allow"),
            )
            await job
            assert service.execution.get("harbor", run.id).status == "in_review"
            saved = service.permissions.page("harbor").items[0]
            assert saved.released_at and saved.answer == "allow"
            assert baseline(repo) == base
        finally:
            await service.close()
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario,flags", [("disconnect", ()), ("normal", ("cleanup-uncertain",))])
def test_lost_native_cleanup_keeps_capacity_reserved(tmp_path, monkeypatch, scenario, flags):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario=scenario, flags=flags)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    current = service.execution.get("harbor", run.id)
    assert current.status == "uncertain", current.problem
    assert not current.result_commit
    service.execution.restart()
    current = service.execution.get("harbor", run.id)
    recovered = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=current.revision))
    )
    assert recovered.status == "uncertain"
    assert service.execution.occupancy("harbor").uncertain == 1


def test_local_adoption_is_explicit_and_preserves_legacy_settings(tmp_path):
    execution = fixture(tmp_path)
    settings = Supervisor(execution.workspace).integrations.settings("harbor")
    assert settings.runtime == "legacy"
    run = execution.claim("harbor", "a" * 40, {"a" * 40: set()})
    assert run.runtime == "legacy"


def test_answer_continuation_uses_new_local_attempt_and_saved_code(tmp_path, monkeypatch):
    from flowfield.questions import QuestionAnswer, Questions

    service, repo, _ = configured(tmp_path, monkeypatch, scenario="question")
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    paused = service.execution.get("harbor", run.id)
    assert paused.status == "waiting_for_input", paused.problem
    questions = Questions(service.workspace)
    question = questions.get("harbor", paused.question_id)
    questions.answer(
        "harbor",
        question.id,
        QuestionAnswer(expected_revision=question.revision, answer="Use the first option"),
    )
    successor = service.execution.claim("harbor", base, {paused.input_checkpoint: set()})
    assert successor.input_base_commit == paused.input_checkpoint
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "normal")
    asyncio.run(service._execute(successor, repo))
    assert service.execution.get("harbor", successor.id).status == "in_review"
    assert service.environment(successor.id).checkout != service.environment(run.id).checkout
    assert "Use the first option" in service.execution.assignment("harbor", successor.id)["input"]


def test_stop_waiting_permission_revokes_request_and_preserves_work(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario="permission")
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        service.jobs[run.id] = job
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.02)
            current = service.execution.get("harbor", run.id)
            stopped = await service.stop(
                "harbor", run.id, RunAction(expected_revision=current.revision)
            )
            await job
            assert stopped.status == "stopped", stopped.problem
            assert not service.permissions.page("harbor").pending
            assert service.permissions.page("harbor").items[0].status == "cancelled"
            assert (service.environment(run.id).checkout / "result.txt").exists()
            assert service.execution.occupancy("harbor").active == 0
        finally:
            service.jobs.pop(run.id, None)
            await service.close()

    asyncio.run(exercise())


def test_local_commits_capture_original_base_without_moving_shared_branch(tmp_path, monkeypatch):
    from flowfield.adapters.local_execution import LocalHost

    service, repo, _ = configured(tmp_path, monkeypatch)
    base = baseline(repo)
    attempt = LocalHost({}).prepare(service.workspace.directory, repo, "native-commit", base)
    (attempt.checkout / "new.txt").write_text("committed locally")
    git(attempt.checkout, "add", "new.txt")
    git(
        attempt.checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "-m",
        "local",
    )
    (attempt.checkout / "later.txt").write_text("uncommitted")
    result, _ = attempt.snapshot(base)
    assert git(repo, "rev-parse", result + "^").decode().strip() == base
    assert git(repo, "show", result + ":new.txt") == b"committed locally"
    assert git(repo, "show", result + ":later.txt") == b"uncommitted"
    assert baseline(repo) == base


def test_schema32_upgrade_retains_legacy_runtime_and_live_ownership(tmp_path, monkeypatch):
    from flowfield import migrations
    from flowfield.application import Workspace
    from flowfield.execution import Execution

    with monkeypatch.context() as older:
        older.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:-1])
        execution = fixture(tmp_path)
        run = execution.claim("harbor", "a" * 40, {"a" * 40: set()})
        execution.save_local(run.id, {"pid": 1234567, "commands": ["retained-tool"]})
        with execution.workspace.connection(write=True) as db:
            db.execute("UPDATE runs SET data=json_remove(data,'$.runtime','$.applied_agent')")
        identity = execution.workspace.directory
    upgraded = Workspace(identity)
    current = Execution(upgraded).get("harbor", run.id)
    assert current.runtime == "legacy" and current.status == "preparing"
    assert Execution(upgraded).local(run.id) == {"pid": 1234567, "commands": ["retained-tool"]}
    assert Supervisor(upgraded).integrations.settings("harbor").runtime == "legacy"


def test_public_permission_projection_keeps_commands_and_diff_but_not_private_inputs():
    from acp.schema import ToolCallUpdate

    from flowfield.adapters.codex_agent import codex_permission_details

    tool = ToolCallUpdate.model_validate(
        {
            "toolCallId": "edit",
            "title": "Edit files",
            "locations": [{"path": "/project/a"}],
            "content": [
                {"type": "diff", "path": "/project/a", "oldText": "before", "newText": "after"}
            ],
            "rawInput": {
                "command": "pnpm test",
                "cwd": "/project",
                "authorization": "private-token",
            },
            "_meta": {"private": "private-metadata"},
        }
    )
    detail = codex_permission_details(tool)
    assert all(text in detail for text in ("pnpm test", "/project/a", "before", "after"))
    assert "private-token" not in detail and "private-metadata" not in detail
    tool.raw_input = {"command": "x" * 20000}
    assert len(codex_permission_details(tool)) <= 16000
    assert codex_permission_details(tool).endswith("[Details truncated]")


def test_recovery_never_signals_a_saved_local_pid(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    service.execution.save_local(
        run.id,
        {
            "runtime_kind": "local",
            "native_launch_started": True,
            "pid": 12345,
            "process_stamp": "same-time",
        },
    )
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "same-time")

    def unexpected_signal(*args):
        raise AssertionError("A saved PID is not a live owner")

    monkeypatch.setattr("flowfield.supervisor.os.killpg", unexpected_signal)
    service.execution.restart()
    current = service.execution.get("harbor", run.id)
    stopped = asyncio.run(
        service.stop("harbor", run.id, RunAction(expected_revision=current.revision))
    )
    assert stopped.status == "uncertain"


def test_discussion_uses_read_only_native_mode_and_keeps_current_result(tmp_path, monkeypatch):
    from test_replies import message

    from flowfield.replies import Replies

    service, repo, _ = configured(tmp_path, monkeypatch)
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    service.results.process("harbor")
    selected = service.results.page("harbor", run.task_id).items[0]
    Replies(service.workspace).submit(
        "harbor", run.task_id, message(service.workspace, run.task_id)
    )
    reply = service.execution.claim("harbor", base, {})
    assert reply.purpose == "discussion"
    monkeypatch.setenv("FLOWFIELD_TEST_SCENARIO", "discussion")
    asyncio.run(service._execute(reply, repo))
    current = service.execution.get("harbor", reply.id)
    assert current.status == "accepted", current.problem
    assert current.applied_agent.mode == "read-only"
    assert current.agent_settings.choice.mode == "workspace-write"
    assert service.results.page("harbor", run.task_id).items[0] == selected


def test_stop_during_local_setup_stops_owned_command_before_freeing_slot(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch)
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="main",
            checks=["true"],
            setup_commands=["while :; do sleep 1; done"],
            setup_timeout_seconds=60,
        ),
    )
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})

    async def exercise():
        job = asyncio.create_task(service._execute(run, repo))
        service.jobs[run.id] = job
        try:
            async with asyncio.timeout(10):
                while not service.execution.local(run.id).get("setup_process"):
                    await asyncio.sleep(0.02)
            current = service.execution.get("harbor", run.id)
            stopped = await service.stop(
                "harbor", run.id, RunAction(expected_revision=current.revision)
            )
            await job
            assert stopped.status == "stopped", stopped.problem
            assert service.execution.local(run.id)["setup_process"] is None
            assert stopped.started_at is None and stopped.result_commit is None
        finally:
            service.jobs.pop(run.id, None)
            await service.close()

    asyncio.run(exercise())


def test_native_mode_requires_explicit_local_adoption_before_discovery(tmp_path):
    from flowfield.agent_models import AgentChoice
    from flowfield.errors import ApplicationError

    service = Supervisor(fixture(tmp_path).workspace)
    with pytest.raises(ApplicationError, match="Select Local"):
        asyncio.run(
            service.validate_agent_choice(
                AgentChoice(model="test-model", effort="low", mode="workspace-write"), "harbor"
            )
        )


def test_local_setup_failure_explains_host_tools_not_retired_inventory(tmp_path, monkeypatch):
    service, repo, settings = configured(tmp_path, monkeypatch)
    settings = service.integrations.configure(
        "harbor",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="main",
            checks=["flowfield_nonexistent_test_tool_1736254"],
        ),
    )
    value = asyncio.run(
        service.setup_validation.check(
            "harbor", SetupCheckRequest(expected_revision=settings.revision)
        )
    )
    assert value.status == "failed" and "service host" in value.problem
    assert "Environment tools" not in value.problem
    assert baseline(repo) == value.commit


def test_stopping_one_parallel_acp_worker_keeps_the_other_permission_live(tmp_path, monkeypatch):
    service, repo, _ = configured(tmp_path, monkeypatch, count=2, scenario="permission")
    base = baseline(repo)
    runs = [service.execution.claim("harbor", base, {base: set()}) for _ in range(2)]

    async def exercise():
        jobs = [asyncio.create_task(service._execute(run, repo)) for run in runs]
        service.jobs.update({run.id: job for run, job in zip(runs, jobs, strict=True)})
        try:
            async with asyncio.timeout(10):
                while len(service.permissions.page("harbor").pending) < 2:
                    await asyncio.sleep(0.02)
            first = service.execution.get("harbor", runs[0].id)
            await service.stop("harbor", first.id, RunAction(expected_revision=first.revision))
            pending = service.permissions.page("harbor").pending
            assert len(pending) == 1 and pending[0].run_id == runs[1].id
            assert not jobs[1].done()
            service.permissions.answer(
                "harbor",
                pending[0].id,
                PermissionAnswer(expected_revision=pending[0].revision, option_id="allow"),
            )
            await asyncio.gather(*jobs)
            assert service.execution.get("harbor", runs[0].id).status == "stopped"
            assert service.execution.get("harbor", runs[1].id).status == "in_review"
        finally:
            await service.close()
            service.jobs.clear()

    asyncio.run(exercise())
