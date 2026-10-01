"""Model-free setup lifecycle. Fake command execution does not prove sandbox isolation."""

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_guidance import setup

from flowfield.adapters.local_environment import baseline, git
from flowfield.adapters.toolchain import validate_tools
from flowfield.environment_models import EnvironmentConfig
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.setup_validation import SetupCheckRequest, SetupValidation


class CommandHarness:
    binary = Path("/test/codex/bin/codex")
    cleanup_confirmed = True
    calls: list[str] = []
    started = None
    resume = None

    def __init__(self, cwd, config=None):
        self.cwd, self.config = cwd, config
        self.process = None
        self.on_commands = None

    async def start(self):
        self.process = SimpleNamespace(pid=os.getpid())

    async def command(self, command, timeout_ms):
        self.calls.append(command)
        if self.started:
            self.started.set()
            await self.resume.wait()
        result = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=self.cwd,
            env=self.config["shell_environment_policy.set"],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
        return {"exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    async def close(self):
        pass

    async def stop(self):
        return self.cleanup_confirmed


async def boundary(*args):
    pass


def configured(tmp_path, monkeypatch, **changes):
    root, guidance = setup(tmp_path)
    git(root, "init", "-b", "main")
    (root / "base.txt").write_text("base")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    service = SetupValidation(guidance.workspace)
    settings = Integrations(guidance.workspace).configure(
        "project",
        IntegrationConfig(
            expected_revision=1,
            target_branch="delivery",
            create_from="HEAD",
            checks=changes.pop("checks", ["test -f base.txt"]),
            **changes,
        ),
    )
    monkeypatch.setattr("flowfield.setup_validation.CodexWorker", CommandHarness)
    monkeypatch.setattr("flowfield.setup_validation.preflight", boundary)
    monkeypatch.setattr(CommandHarness, "calls", [])
    return root, service, settings


def test_setup_runs_declared_tools_and_retains_evidence_without_enabling_queue(
    tmp_path, monkeypatch
):
    tool = tmp_path / "project-compiler"
    tool.write_text("#!/bin/sh\nprintf 'custom tool works\\n'\n")
    tool.chmod(0o755)
    config = EnvironmentConfig(
        tools={"compiler": str(tool)}, variables={"CACHE_ROOT": "$RUNTIME/cache"}
    )
    root, service, settings = configured(
        tmp_path,
        monkeypatch,
        environment=config,
        setup_commands=['compiler; test "$CACHE_ROOT" = "${HOME%/home}/cache"'],
    )
    head = baseline(root)
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "passed" and not value.stale
    assert value.setup[0].output == "custom tool works\n"
    assert baseline(root) == head and value.pid is None
    assert SetupValidation(service.workspace).get("project").status == "passed"
    with service.workspace.connection() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    Integrations(service.workspace).configure(
        "project",
        IntegrationConfig(
            expected_revision=settings.revision, target_branch="delivery", checks=["false"]
        ),
    )
    assert service.get("project").stale
    with pytest.raises(ApplicationError):
        asyncio.run(
            service.check("project", SetupCheckRequest(expected_revision=settings.revision))
        )


def test_failed_setup_skips_checks_and_changed_source_cannot_pass(tmp_path, monkeypatch):
    root, service, settings = configured(tmp_path, monkeypatch, setup_commands=["exit 7"])
    request = SetupCheckRequest(expected_revision=settings.revision)
    value = asyncio.run(service.check("project", request))
    assert value.status == "failed" and value.setup[0].exit_code == 7 and not value.checks
    assert "Setup command failed (exit 7): exit 7" in value.problem
    settings = Integrations(service.workspace).configure(
        "project",
        IntegrationConfig(
            expected_revision=settings.revision,
            target_branch="delivery",
            checks=["echo changed > base.txt"],
        ),
    )
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "failed" and "changed source" in value.problem
    assert (root / "base.txt").read_text() == "base"
    assert (Path(value.workspace) / "base.txt").read_text() == "changed\n"


def test_checkout_readiness_refreshes_without_repeating_commands(tmp_path, monkeypatch):
    root, service, settings = configured(tmp_path, monkeypatch)
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "passed" and "Switch the project checkout" in value.checkout_problem
    calls = list(CommandHarness.calls)
    git(root, "switch", "delivery")
    assert service.get("project").checkout_problem is None
    (root / "human.txt").write_text("Preserve my work")
    assert "local edits or untracked" in service.get("project").checkout_problem
    assert service.get("project").status == "passed"
    assert (root / "human.txt").read_text() == "Preserve my work"
    assert CommandHarness.calls == calls


def test_managed_library_failure_names_remedy_without_changing_permissions(tmp_path, monkeypatch):
    root, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=["printf 'Library not loaded: /example/libthing.dylib' >&2; exit 126"],
    )
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "failed" and not value.checks
    assert "runtime library could not load" in value.problem
    assert "Host-shell success" in value.problem
    assert "/example/libthing.dylib" in value.setup[0].output
    assert Integrations(service.workspace).settings("project") == settings


def test_duplicate_validation_and_midflight_settings_change(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)

    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(CommandHarness, "started", started)
        monkeypatch.setattr(CommandHarness, "resume", resume)
        request = SetupCheckRequest(expected_revision=settings.revision)
        job = asyncio.create_task(service.check("project", request))
        await asyncio.wait_for(started.wait(), 10)
        with pytest.raises(ApplicationError, match="already running"):
            await service.check("project", request)
        Integrations(service.workspace).configure(
            "project",
            IntegrationConfig(
                expected_revision=settings.revision, target_branch="delivery", checks=["false"]
            ),
        )
        resume.set()
        value = await job
        assert value.status == "passed" and value.stale
        assert CommandHarness.calls == ["test -f base.txt"]

    asyncio.run(scenario())


def test_interrupted_command_evidence_blocks_duplicate_execution(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    value.status = "checking"
    value.pid = 12345
    value.commands = ["unknown-command"]
    service._save(value)
    restarted = SetupValidation(service.workspace)
    assert restarted.get("project").status == "uncertain"
    with pytest.raises(ApplicationError, match="interrupted"):
        asyncio.run(
            restarted.check("project", SetupCheckRequest(expected_revision=settings.revision))
        )


def test_environment_preserves_boundary_and_reports_missing_tools(tmp_path):
    for variables in (
        {"PATH": "/host"},
        {"HOME": "/host"},
        {"HTTPS_PROXY": ""},
        {"LD_PRELOAD": "x"},
    ):
        with pytest.raises(ValidationError):
            EnvironmentConfig(variables=variables)
    with pytest.raises(ValidationError):
        EnvironmentConfig(tools={"../cargo": "/bin/sh"})
    with pytest.raises(ApplicationError, match="unavailable"):
        validate_tools(EnvironmentConfig(tools={"missing": str(tmp_path / "missing")}), [])
    protected = tmp_path / "state"
    protected.mkdir()
    for path in [tmp_path, protected]:
        with pytest.raises(ApplicationError, match="protected"):
            validate_tools(EnvironmentConfig(read_paths=[str(path)]), [protected])


def test_cli_check_changes_preserve_machine_environment(monkeypatch):
    from typer.testing import CliRunner

    from flowfield.cli import app

    current = {
        "revision": 2,
        "target_branch": "delivery",
        "checks": ["old"],
        "environment": {"tools": {"compiler": "/bin/sh"}, "read_paths": [], "variables": {}},
    }
    writes = []

    def request(self, method, path, data=None):
        if method == "PUT":
            writes.append(data)
        return current

    monkeypatch.setattr("flowfield.client.Client.request", request)
    result = CliRunner().invoke(
        app,
        [
            "project",
            "integration",
            "configure",
            "--project",
            "example",
            "--target",
            "delivery",
            "--check",
            "new",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert writes[0]["environment"] == current["environment"]
    assert writes[0]["checks"] == ["new"]
