"""Model-free Local setup lifecycle; no harness or model is involved."""

import asyncio
from pathlib import Path

import pytest
from test_guidance import setup

from flowfield.adapters import local_checks
from flowfield.adapters.git_workspace import git
from flowfield.errors import ApplicationError
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.setup_validation import SetupCheckRequest, SetupValidation


def configured(tmp_path, monkeypatch, **changes):
    root, guidance = setup(tmp_path)
    git(root, "init", "-b", "main")
    (root / "base.txt").write_text("base")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@invalid", "commit", "-m", "base")
    service = SetupValidation(guidance.workspace)
    settings = Integrations(guidance.workspace).configure(
        "project",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="delivery",
            create_from="HEAD",
            checks=changes.pop("checks", ["test -f base.txt"]),
            **changes,
        ),
    )
    return root, service, settings


def test_setup_uses_host_tools_and_retains_evidence_without_enabling_queue(tmp_path, monkeypatch):
    tools = tmp_path / "host-bin"
    tools.mkdir()
    tool = tools / "compiler"
    tool.write_text("#!/bin/sh\nprintf 'custom tool works\\n'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + ":" + __import__("os").environ["PATH"])
    root, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=['compiler; test -n "$FLOWFIELD_RUNTIME_DIR"'],
    )
    before = git(root, "rev-parse", "HEAD")
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "passed", value.problem
    assert "custom tool works" in value.setup[0].output
    assert Path(value.workspace).is_dir() and not value.stale
    assert git(root, "rev-parse", "HEAD") == before
    from flowfield.execution import Execution

    assert not Execution(service.workspace).settings("project").enabled
    assert "Switch the project checkout" in value.checkout_problem
    git(root, "switch", "delivery")
    assert service.get("project").checkout_problem is None
    (root / "human.txt").write_text("Preserve my work")
    assert "local edits or untracked" in service.get("project").checkout_problem
    assert service.get("project").status == "passed"


def test_library_failure_names_host_remedy_without_changing_settings(tmp_path, monkeypatch):
    _, service, settings = configured(
        tmp_path,
        monkeypatch,
        setup_commands=["printf 'Library not loaded: /example/libthing.dylib' >&2; exit 126"],
    )
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    assert value.status == "failed" and not value.checks
    assert "library dependencies on the service host" in value.problem
    assert "/example/libthing.dylib" in value.setup[0].output
    assert Integrations(service.workspace).settings("project") == settings


def test_duplicate_validation_and_midflight_settings_change(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)
    actual = local_checks.run_checks

    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()

        async def controlled(*args, **kwargs):
            started.set()
            await resume.wait()
            return await actual(*args, **kwargs)

        monkeypatch.setattr(local_checks, "run_checks", controlled)
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

    asyncio.run(scenario())


def test_interrupted_legacy_command_evidence_blocks_duplicate_execution(tmp_path, monkeypatch):
    _, service, settings = configured(tmp_path, monkeypatch)
    value = asyncio.run(
        service.check("project", SetupCheckRequest(expected_revision=settings.revision))
    )
    value.status, value.pid, value.commands = "checking", 12345, ["unknown-command"]
    service._save(value)
    restarted = SetupValidation(service.workspace)
    assert restarted.get("project").status == "uncertain"
    with pytest.raises(ApplicationError, match="interrupted"):
        asyncio.run(
            restarted.check("project", SetupCheckRequest(expected_revision=settings.revision))
        )


def test_cli_check_changes_omit_retired_inventory(monkeypatch):
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
    assert "environment" not in writes[0]
    assert writes[0]["runtime"] is None and writes[0]["checks"] == ["new"]
