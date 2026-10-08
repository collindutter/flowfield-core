"""Harness settings, catalog isolation and coordinator dispatch without model calls."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_execution import fixture
from typer.testing import CliRunner

from flowfield.adapters.agents import HARNESSES, Harness, HarnessInfo, create_agent
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.api import create_app
from flowfield.cli import app
from flowfield.coordinator_models import CoordinatorSend
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, NativeMode, SettingsEdit
from flowfield.run_activity import ActivityUpdate
from flowfield.supervisor import Supervisor


def test_registered_harness_needs_no_service_branch(tmp_path, monkeypatch):
    sentinel = object()

    async def models(directory):
        return [ModelOption(id="custom-model", name="Custom", efforts=["off"])]

    monkeypatch.setitem(
        HARNESSES,
        "another-agent",
        Harness(
            info=HarnessInfo(id="another-agent", name="Another agent", description="Test"),
            create=lambda *args: sentinel,
            models=models,
            status=lambda *args: {"available": True},
        ),
    )
    choice = AgentChoice(harness="another-agent", model="custom-model", effort="off")
    assert create_agent(choice, tmp_path, tmp_path, {}) is sentinel
    with TestClient(
        create_app(data_dir=tmp_path / "catalog-state"), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/api/agent-harnesses")
        assert any(item["id"] == "another-agent" and item["available"] for item in response.json())
    listed = CliRunner().invoke(app, ["harness", "list", "--json"])
    assert listed.exit_code == 0 and "another-agent" in listed.stdout
    service = Supervisor(fixture(tmp_path).workspace)

    async def exercise():
        catalog = await service.model_options(harness="another-agent")
        assert catalog[0].harness == "another-agent"
        with pytest.raises(ApplicationError, match="Unknown harness"):
            await service.model_options(harness="not-registered")
        await service.close()

    asyncio.run(exercise())


def test_catalogs_are_cached_and_validated_per_harness(tmp_path, monkeypatch):
    calls = []

    async def discover(directory, harness):
        calls.append(harness)
        return [
            ModelOption(
                harness=harness,
                id="shared-name",
                name="Test",
                efforts=["low"],
                modes=[
                    NativeMode(id="full-access" if harness == "pi" else "read-only", name="Mode")
                ],
            )
        ]

    monkeypatch.setattr("flowfield.supervisor.model_options", discover)
    service = Supervisor(fixture(tmp_path).workspace)

    async def exercise():
        await asyncio.gather(
            service.model_options(harness="pi"),
            service.model_options(harness="pi"),
            service.model_options(),
        )
        assert sorted(calls) == ["codex", "pi"]
        await service.validate_agent_choice(
            AgentChoice(harness="pi", model="shared-name", effort="low", mode="full-access")
        )
        for mode in (None, "read-only"):
            with pytest.raises(ApplicationError, match="selected harness"):
                await service.validate_agent_choice(
                    AgentChoice(harness="pi", model="shared-name", effort="low", mode=mode)
                )
        await service.model_options(harness="pi", refresh=True)
        assert calls.count("pi") == 2 and calls.count("codex") == 1
        await service.close()

    asyncio.run(exercise())


def test_worker_defaults_preserve_harness_and_overrides(tmp_path):
    execution = fixture(tmp_path)
    current = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=current.revision,
            harness="pi",
            model="provider/model",
            effort="low",
            mode="full-access",
        ),
    )
    settings = AgentSettings(execution.workspace)
    assert settings.get("harbor", "worker", "task-0").effective.choice.harness == "pi"
    override = settings.edit(
        "harbor",
        "worker",
        AgentSettingsEdit(
            expected_revision=1,
            selection=AgentChoice(model="codex-model", effort="high", mode="read-only"),
        ),
        "task-0",
    )
    assert override.effective.choice.harness == "codex"
    restored = settings.edit(
        "harbor", "worker", AgentSettingsEdit(expected_revision=override.revision), "task-0"
    )
    assert restored.effective.choice.harness == "pi"
    assert execution.settings("harbor").harness == "pi"


def test_coordinator_dispatches_pi_and_resumes_native_session(tmp_path, monkeypatch):
    started = []

    class FakePi:
        cleanup_confirmed = True

        def __init__(self, directory, cwd, environment):
            self.session = SimpleNamespace(session_id="pi-session")
            self.on_activity = None

        async def start(self, servers, *, resume=None, persistent=False):
            assert persistent and len(servers) == 1
            started.append(resume)

        async def configure(self, choice):
            assert choice.harness == "pi"
            return choice

        def validate_attachments(self, attachments):
            assert not attachments

        async def command_prompt(self, text, **kwargs):
            return None

        async def prompt(self, text, on_permission, **kwargs):
            self.on_activity(ActivityUpdate(key="reply", kind="agent", text="Pi reply"))
            return {"status": "completed"}

        async def close(self):
            pass

    monkeypatch.setattr("flowfield.adapters.pi_agent.PiAgent", FakePi)
    execution = fixture(tmp_path)
    service = Supervisor(execution.workspace)
    AgentSettings(execution.workspace).edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=1,
            selection=AgentChoice(
                harness="pi", model="provider/model", effort="low", mode="full-access"
            ),
        ),
    )
    conversation = service.coordinator.store.new("harbor")

    async def exercise():
        for expected in ("new", "resumed"):
            turn = service.coordinator.send(
                "harbor", conversation.id, CoordinatorSend(id=uuid4().hex, text="Hello")
            )
            await service.coordinator.jobs[turn.id]
            saved = service.coordinator.store.get("harbor", turn.id)
            assert saved.status == "completed", saved.notice
            assert saved.session == expected
            assert saved.applied.choice.harness == "pi"
            assert any(item.text == "Pi reply" for item in saved.activity.items)
        await service.close()

    asyncio.run(exercise())
    assert started == [None, "pi-session"]
