"""Native Pi adapter contract using a deterministic, owned RPC subprocess."""

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest
from acp.schema import HttpMcpServer

from flowfield.adapters import pi_agent
from flowfield.adapters.pi_agent import FULL_ACCESS, PiAgent
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption, SettingsEdit, WorkerSettings
from flowfield.run_activity import ActivityUpdate


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    executable = tmp_path / "pi"
    executable.write_text(Path(__file__).with_name("fake_pi.py").read_text())
    executable.chmod(0o700)
    native = tmp_path / "native"
    native.mkdir()
    (native / "auth.json").write_text('{"example": "original credential"}')
    (native / "models.json").write_text('{"providers": {}}')
    (native / "settings.json").write_text('{"packages": ["evil"], "extensions": ["evil.ts"]}')
    (native / "mcp.json").write_text('{"mcpServers": {"ambient": {"command": "evil"}}}')
    (native / "AGENTS.md").write_text("native instructions")
    (native / "skills").mkdir()
    (tmp_path / ".pi").mkdir()
    (tmp_path / ".pi/settings.json").write_text('{"packages": ["evil"]}')
    (tmp_path / ".pi/mcp.json").write_text('{"mcpServers": {"ambient": {"command": "evil"}}}')
    environment = {
        **os.environ,
        "PI_PATH": str(executable),
        "PI_TEST_LOG": str(tmp_path / "rpc.log"),
        "PI_CODING_AGENT_DIR": str(native),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pi_agent, "STOP_TIMEOUT", 0.1)
    return environment


def agent(tmp_path: Path, runtime: dict[str, str], mode: str = "normal") -> PiAgent:
    return PiAgent(tmp_path / "data", tmp_path, {**runtime, "FAKE_PI": mode})


def records(runtime: dict[str, str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(runtime["PI_TEST_LOG"]).read_text().splitlines()]


def choice(**updates: Any) -> AgentChoice:
    return AgentChoice(
        harness="pi", model="example/reasoning", effort="high", mode=FULL_ACCESS.id, fast=False
    ).model_copy(update=updates)


def test_preflight_and_legacy_models(tmp_path: Path, runtime: dict[str, str]) -> None:
    value = pi_agent.status(tmp_path, runtime)
    assert value["harness"] == "pi" and value["pi_available"] and value["available"]
    assert "unsandboxed" in value["message"]
    with pytest.raises(ApplicationError, match="Install Earendil Pi") as error:
        pi_agent.status(tmp_path, {"PATH": "", "PI_PATH": "/missing/pi"})
    assert error.value.code == "pi_missing"
    assert AgentChoice(model="old", effort="low").harness == "codex"
    assert ModelOption(id="old", name="Old", efforts=["low"]).harness == "codex"
    assert WorkerSettings(project_id="legacy").harness == "codex"
    assert SettingsEdit(expected_revision=1, model="old", effort="low").harness == "codex"
    assert WorkerSettings(project_id="pi-project", harness="pi").harness == "pi"
    assert (
        SettingsEdit(
            expected_revision=1, model="example/reasoning", effort="high", harness="pi"
        ).harness
        == "pi"
    )


def test_native_model_discovery(tmp_path: Path, runtime: dict[str, str]) -> None:
    options = asyncio.run(pi_agent.model_options(tmp_path))
    assert [m.id for m in options] == ["example/reasoning", "router/nested/model"]
    assert options[0].efforts == ["off", "low", "high", "max"]
    assert options[1].efforts == ["off"]
    assert all(m.harness == "pi" and not m.fast and m.modes == [FULL_ACCESS] for m in options)
    assert not any(record.get("type") == "prompt" for record in records(runtime))


def test_scoped_mcp_overlay(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime)
        server = HttpMcpServer(
            type="http",
            name="flowfield",
            url="http://localhost/mcp",
            headers=[{"name": "Authorization", "value": "Bearer turn-secret"}],
        )
        await client.start([server])
        launch = records(runtime)[0]
        argv = launch["argv"]
        overlay = Path(launch["overlay"])
        assert "--no-extensions" in argv and "--no-approve" in argv
        assert "builtin:mcp" in argv and "--no-mcp" not in argv
        assert "mcp__*" in argv[argv.index("--tools") + 1].split(",")
        assert "turn-secret" not in str(argv)
        extension = (overlay / "scoped-mcp.ts").read_text()
        assert 'registerMcpServer("flowfield",' in extension and "Bearer turn-secret" in extension
        assert '"exposure": "direct"' in extension
        assert (overlay / "auth.json").resolve() == Path(
            runtime["PI_CODING_AGENT_DIR"]
        ) / "auth.json"
        assert (overlay / "models.json").is_symlink()
        assert (overlay / "AGENTS.md").read_text() == "native instructions"
        assert (overlay / "skills").is_symlink()
        assert not (overlay / "mcp.json").exists()
        assert "packages" not in (overlay / "settings.json").read_text()
        await client.close()
        assert client.cleanup_confirmed and not overlay.exists()
        assert (
            "original credential" in Path(runtime["PI_CODING_AGENT_DIR"], "auth.json").read_text()
        )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        {"mode": None},
        {"mode": "sandbox"},
        {"fast": True},
        {"effort": "minimal"},
        {"model": "missing"},
        {"harness": "codex"},
    ],
)
def test_settings_reject_unsupported(
    tmp_path: Path, runtime: dict[str, str], invalid: dict[str, Any]
) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime)
        try:
            await client.start([])
            with pytest.raises(ApplicationError) as error:
                await client.configure(choice(**invalid))
            assert error.value.code == "agent_choice_unavailable"
        finally:
            await client.close()

    asyncio.run(exercise())


def test_settings_detect_native_clamping(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, "clamped")
        try:
            await client.start([])
            with pytest.raises(ApplicationError):
                await client.configure(choice())
        finally:
            await client.close()

    asyncio.run(exercise())


def test_stream_waits_for_settled_and_excludes_private_data(
    tmp_path: Path, runtime: dict[str, str]
) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime)
        updates: list[ActivityUpdate] = []
        client.on_activity = updates.append
        try:
            await client.start([])
            assert (await client.configure(choice())).fast is False
            turn = asyncio.create_task(
                client.prompt(
                    "hello",
                    None,
                    attachments=[
                        {
                            "name": "note",
                            "mime": "text/plain",
                            "data": base64.b64encode(b"file text").decode(),
                        },
                        {"name": "picture", "mime": "image/png", "data": "aW1hZ2U="},
                    ],
                )
            )
            while not any(update.kind == "tool" or update.kind == "command" for update in updates):
                await asyncio.sleep(0.001)
            assert not turn.done()
            assert await turn == {"status": "completed"}
            assert any(update.text == "Public\u2028text" for update in updates)
            assert any(update.text == "bash · completed" for update in updates)
            assert updates[-1].context and updates[-1].context.used == 10
            projection = str([update.model_dump() for update in updates])
            assert "private" not in projection and "secret" not in projection
            request = next(r for r in records(runtime) if r.get("type") == "prompt")
            assert (
                "file text" in request["message"]
                and request["images"][0]["mimeType"] == "image/png"
            )
        finally:
            await client.close()
        assert client.cleanup_confirmed
        assert client.process and client.process.returncode == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["error", "rejected", "exit"])
def test_turn_failure_mapping(tmp_path: Path, runtime: dict[str, str], mode: str) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, mode)
        try:
            await client.start([])
            with pytest.raises(ApplicationError) as error:
                await client.prompt("hello", None)
            assert error.value.code == "pi_prompt_failed"
            assert "secret" not in error.value.message
        finally:
            await client.close()
        if mode == "exit":
            assert not client.cleanup_confirmed

    asyncio.run(exercise())


@pytest.mark.parametrize("mode,expected", [("aborted", "stopped"), ("handled", "completed")])
def test_native_abort_and_handled(
    tmp_path: Path, runtime: dict[str, str], mode: str, expected: str
) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, mode)
        try:
            await client.start([])
            assert await client.prompt("hello", None) == {"status": expected}
        finally:
            await client.close()

    asyncio.run(exercise())


def test_persistent_native_resume_without_replay(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        first = agent(tmp_path, runtime)
        await first.start([], persistent=True)
        identity = first.session.session_id
        assert identity
        await first.close()
        # An empty session is durable before any model turn; no prompt replay.
        resumed = agent(tmp_path, runtime)
        await resumed.start([], resume=identity, persistent=True)
        assert resumed.session.session_id == identity
        assert not any(r.get("type") == "prompt" for r in records(runtime))
        assert await resumed.prompt("first turn", None) == {"status": "completed"}
        await resumed.close()
        path = tmp_path / "data/harnesses/pi/sessions" / (identity + ".jsonl")
        saved = path.read_text()
        again = agent(tmp_path, runtime)
        await again.start([], resume=identity, persistent=True)
        assert path.read_text() == saved
        await again.close()
        missing = agent(tmp_path, runtime)
        path.unlink()
        with pytest.raises(ApplicationError) as error:
            await missing.start([], resume=identity, persistent=True)
        assert error.value.code == "agent_resume_failed"

    asyncio.run(exercise())


def test_resume_identity_mismatch(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        first = agent(tmp_path, runtime)
        await first.start([], persistent=True)
        await first.close()
        second = agent(tmp_path, runtime, "bad_resume")
        with pytest.raises(ApplicationError) as error:
            await second.start([], resume=first.session.session_id, persistent=True)
        assert error.value.code == "agent_resume_failed"
        assert second.process and second.process.returncode == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["bad_wire", "extension_error"])
def test_start_failure_cleans_up(tmp_path: Path, runtime: dict[str, str], mode: str) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, mode)
        with pytest.raises(ApplicationError) as error:
            await client.start([])
        assert error.value.code == "pi_start_failed"
        assert client.process and client.process.returncode is not None
        assert client._temporary is None

    asyncio.run(exercise())


def test_cancelled_turn_forced_cleanup_stays_uncertain(
    tmp_path: Path, runtime: dict[str, str]
) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, "hang")
        await client.start([])
        turn = asyncio.create_task(client.prompt("hello", None))
        while not any(r.get("type") == "prompt" for r in records(runtime)):
            await asyncio.sleep(0.001)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert not client.cleanup_confirmed
        assert client.process and client.process.returncode is not None
        assert not await client.stop()
        assert client._temporary is None

    asyncio.run(exercise())


def test_cancelled_start_adopts_and_closes_process(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, "slow_start")
        launch = asyncio.create_task(client.start([]))
        while not client.process:
            await asyncio.sleep(0.001)
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        assert client.cleanup_confirmed
        assert client.process.returncode is not None

    asyncio.run(exercise())


def test_commands_and_image_capabilities(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime)
        try:
            await client.start([])
            assert [c.name for c in await client.command_options()] == ["compact", "status"]
            assert await client.command_prompt("hello", has_history=False, attachments=[]) is None
            for text in ["/new", "/compact argument", "/compact"]:
                with pytest.raises(ApplicationError):
                    await client.command_prompt(text, has_history=False, attachments=[])
            assert (
                await client.command_prompt("/compact", has_history=True, attachments=[])
                == "/compact"
            )
            assert await client.prompt("/compact", None) == {"status": "completed"}
            assert await client.prompt("/status", None) == {"status": "completed"}
            assert not any(r.get("type") == "prompt" for r in records(runtime))
            await client.configure(choice(model="router/nested/model", effort="off"))
            with pytest.raises(ApplicationError) as error:
                client.validate_attachments([{"mime": "image/png"}])
            assert error.value.code == "images_unavailable"
        finally:
            await client.close()
        assert await client.prompt("hello", None) == {"status": "stopped"}

    asyncio.run(exercise())


def test_command_discovery_helper(tmp_path: Path, runtime: dict[str, str]) -> None:
    result = asyncio.run(pi_agent.command_options(tmp_path, tmp_path, choice()))
    assert [c.name for c in result] == ["compact", "status"]


@pytest.mark.parametrize("damage", ['{"broken":', '{"type":"message"}'])
def test_resume_rejects_corrupt_or_truncated_history(
    tmp_path: Path, runtime: dict[str, str], damage: str
) -> None:
    async def exercise() -> None:
        first = agent(tmp_path, runtime)
        await first.start([], persistent=True)
        identity = first.session.session_id
        await first.close()
        path = tmp_path / "data/harnesses/pi/sessions" / (str(identity) + ".jsonl")
        with path.open("a") as output:
            output.write(damage)
        original = path.read_text()
        second = agent(tmp_path, runtime)
        with pytest.raises(ApplicationError) as error:
            await second.start([], resume=identity, persistent=True)
        assert error.value.code == "agent_resume_failed"
        assert path.read_text() == original and second.process is None

    asyncio.run(exercise())


def test_native_acknowledged_stop_finishes_turn(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, "wait_abort")
        await client.start([])
        turn = asyncio.create_task(client.prompt("hello", None))
        while not any(r.get("type") == "prompt" for r in records(runtime)):
            await asyncio.sleep(0.001)
        assert await client.stop()
        assert await turn == {"status": "stopped"}
        types = [r.get("type") for r in records(runtime)]
        assert types.index("clear_queue") < types.index("abort")

    asyncio.run(exercise())


def test_nonzero_shutdown_after_turn_is_uncertain(tmp_path: Path, runtime: dict[str, str]) -> None:
    async def exercise() -> None:
        client = agent(tmp_path, runtime, "bad_shutdown")
        await client.start([])
        assert await client.prompt("hello", None) == {"status": "completed"}
        assert not await client.stop()
        assert client.process and client.process.returncode == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("harness", ["future-agent", "x" * 64, "", "x" * 65, "Upper", "_bad"])
def test_harness_fields_are_bounded_strings(harness: str) -> None:
    records = [
        (AgentChoice, {"model": "model", "effort": "off"}),
        (ModelOption, {"id": "model", "name": "Model", "efforts": ["off"]}),
        (WorkerSettings, {"project_id": "project"}),
        (SettingsEdit, {"expected_revision": 1, "model": "model", "effort": "off"}),
    ]
    for record_type, values in records:
        if harness in {"future-agent", "x" * 64}:
            assert record_type.model_validate({**values, "harness": harness}).harness == harness
        else:
            with pytest.raises(ValueError):
                record_type.model_validate({**values, "harness": harness})


def test_path_preflight_without_override(tmp_path: Path, runtime: dict[str, str]) -> None:
    value = pi_agent.status(tmp_path, {"PATH": str(tmp_path)})
    assert value["executable"] == runtime["PI_PATH"]
