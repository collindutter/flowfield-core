import json

from typer.testing import CliRunner

from flowfield import __version__
from flowfield.cli import app

runner = CliRunner()


def test_cli_help_and_versions() -> None:
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"version": __version__}


def test_serve_rejects_invalid_port() -> None:
    assert runner.invoke(app, ["serve", "--port", "0"]).exit_code == 2


def test_unknown_command_fails() -> None:
    assert runner.invoke(app, ["checkout"]).exit_code == 2


def test_guidance_install_has_outcome_files_and_next_steps(monkeypatch):
    from flowfield.client import Client

    payload = {
        "revision": "a" * 64,
        "status": "installed",
        "message": "Project guidance installed.",
        "changed_files": ["AGENTS.md", ".agents/skills/flowfield-coordinator/SKILL.md"],
        "notices": ["Review additional instruction files: src/AGENTS.override.md."],
        "next_steps": ["Review and commit guidance.", "Start a fresh Codex conversation."],
        "instruction_files": ["AGENTS.md", "src/AGENTS.override.md"],
        "baseline": "not_committed",
        "baseline_ref": "HEAD",
    }
    monkeypatch.setattr(Client, "request", lambda *args, **kwargs: payload)
    result = runner.invoke(app, ["project", "guidance", "install", "--project", "harbor"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Project guidance installed.\n\nChanged files:\n- AGENTS.md")
    assert "\nReview:\n- Review additional" in result.output
    assert "\nNext:\n- Review and commit" in result.output
    assert "Worker baseline:" not in result.output and "Instruction files:" not in result.output
    shown = runner.invoke(app, ["project", "guidance", "show", "--project", "harbor"])
    assert "Worker baseline:" in shown.output and "Instruction files:" in shown.output


def test_result_action_renders_a_bounded_mutation_receipt(monkeypatch) -> None:
    from flowfield.client import Client

    calls = []

    def request(self, method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {
            "saved": True,
            "id": "result-one",
            "task_key": "HAR-1",
            "version": 2,
            "revision": 3,
            "status": "preparing",
            "candidate_commit": None,
        }

    monkeypatch.setattr(Client, "request", request)
    result = runner.invoke(
        app,
        [
            "task",
            "results",
            "prepare",
            "result-one",
            "--project",
            "harbor",
            "--expected-revision",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Result v2" in result.output and "pending" in result.output
    assert calls[0][1].endswith("/results/result-one/prepare")


def test_managed_question_cli_displays_reservation_without_application_step(monkeypatch):
    from flowfield.client import Client

    monkeypatch.setattr(
        Client,
        "request",
        lambda *args, **kwargs: {
            "id": "scope",
            "task_key": "HAR-1",
            "status": "assigned",
            "question": "Which records?",
            "answer": "Favourites",
            "delivery": {"state": "Resuming", "message": "Preparing work with your saved answer."},
        },
    )
    result = runner.invoke(app, ["inbox", "show", "scope", "--project", "harbor"])
    assert result.exit_code == 0, result.stdout
    assert "Resuming" in result.stdout and "Favourites" in result.stdout
    assert "Awaiting application" not in result.stdout
