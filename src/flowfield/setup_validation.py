"""Explicit, model-free setup check using the same command boundary as managed workers."""

import asyncio
import fcntl
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from flowfield.adapters import git_checkout
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.codex_environment import configuration, preflight, run_checks
from flowfield.adapters.codex_worker import CodexWorker
from flowfield.adapters.local_environment import LocalEnvironment
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import CheckResult, Record
from flowfield.integration import Integrations


class SetupCheckRequest(Record):
    expected_revision: int = Field(ge=1)


class SetupCheck(Record):
    project_id: str
    settings_revision: int
    id: str
    commit: str
    created_at: str
    status: Literal["checking", "passed", "failed", "uncertain"] = "checking"
    stale: bool = False
    problem: str | None = None
    checkout_problem: str | None = None
    workspace: str | None = None
    setup: list[CheckResult] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    # Process transport remains local diagnostic evidence; never passed to a model.
    pid: int | None = None
    commands: list[str] = Field(default_factory=list)


class SetupValidation:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.jobs: set[asyncio.Task[object]] = set()
        self.active: set[str] = set()
        self.closing = False

    def _path(self, project_id: str) -> Path:
        self.workspace.project(project_id)
        return self.workspace.directory / f"setup-{project_id}.json"

    def _save(self, value: SetupCheck) -> None:
        path = self._path(value.project_id)
        temporary = path.with_suffix(".pending")
        with temporary.open("w") as stream:
            stream.write(value.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def get(self, project_id: str) -> SetupCheck | None:
        path = self._path(project_id)
        if not path.exists():
            return None
        value = SetupCheck.model_validate_json(path.read_text())
        settings = Integrations(self.workspace).settings(project_id)
        # Current checkout readiness is independent of historical command evidence.
        # Reuse delivery's read-only guards; this is not an authorization to deliver.
        repo = Path(self.workspace.project(project_id).path)
        try:
            branch = settings.target_branch or ""
            git_checkout.verify(
                repo, branch, gitops.target(repo, branch), git_checkout.binding(repo)
            )
            value.checkout_problem = None
        except ApplicationError as error:
            value.checkout_problem = error.message
        value.stale = settings.revision != value.settings_revision
        if not value.stale:
            try:
                head = gitops.target(
                    Path(self.workspace.project(project_id).path), settings.target_branch or ""
                )
                value.stale = head != value.commit
            except ApplicationError:
                value.stale = True
        if value.status == "checking" and project_id not in self.active:
            value.status = "uncertain" if value.pid or value.commands else "failed"
            value.problem = (
                (
                    "Setup validation was interrupted. Preserved files and process "
                    "evidence must be checked before repeating commands."
                )
                if value.status == "uncertain"
                else "Setup validation was interrupted before execution; run it again."
            )
        return value

    async def check(self, project_id: str, request: SetupCheckRequest) -> SetupCheck:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        task = asyncio.current_task()
        assert task
        self.jobs.add(task)
        try:
            with self._path(project_id).with_suffix(".lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ApplicationError(
                        "setup_busy", "Setup validation is already running.", 409
                    ) from error
                previous = self.get(project_id)
                if previous and previous.status == "uncertain":
                    raise ApplicationError(
                        "setup_uncertain",
                        previous.problem or "Check the previous process before retrying.",
                        409,
                    )
                settings = Integrations(self.workspace).settings(project_id)
                self.workspace._current(settings.revision, request.expected_revision)
                if not settings.target_branch or not settings.checks:
                    raise ApplicationError(
                        "setup_missing", "Choose a destination and validation commands first.", 409
                    )
                repo = Path(self.workspace.project(project_id).path)
                commit = gitops.target(repo, settings.target_branch)
                value = SetupCheck(
                    project_id=project_id,
                    id=uuid4().hex,
                    settings_revision=settings.revision,
                    commit=commit,
                    created_at=now(),
                )
                self.active.add(project_id)
                self._save(value)
                client: CodexWorker | None = None
                try:
                    preparing = asyncio.create_task(
                        asyncio.to_thread(
                            LocalEnvironment.prepare,
                            self.workspace.directory / "setup-work",
                            repo,
                            value.id,
                            commit,
                            settings.environment,
                            [
                                self.workspace.directory,
                                *[Path(p.path) for p in self.workspace.projects()],
                            ],
                        )
                    )
                    try:
                        environment = await asyncio.shield(preparing)
                    except asyncio.CancelledError:
                        # A Python thread cannot be cancelled. Retain its completed copy
                        # before releasing ownership or allowing another validation.
                        environment = await preparing
                        value.workspace = str(environment.checkout)
                        raise
                    value.workspace = str(environment.checkout)
                    provisional = CodexWorker(environment.checkout)
                    client = CodexWorker(
                        environment.checkout, configuration(environment, provisional.binary)
                    )
                    await client.start()
                    assert client.process
                    value.pid = client.process.pid

                    def save_commands(commands: list[str]) -> None:
                        value.commands = commands
                        self._save(value)

                    client.on_commands = save_commands
                    self._save(value)
                    await preflight(
                        client,
                        environment,
                        self.workspace.directory,
                        [Path(p.path) for p in self.workspace.projects()],
                    )
                    value.setup = await run_checks(
                        client, settings.setup_commands, settings.setup_timeout_seconds
                    )
                    self._save(value)
                    if not any(c.exit_code for c in value.setup):
                        value.checks = await run_checks(
                            client, settings.checks, settings.check_timeout_seconds
                        )
                    clean = await asyncio.to_thread(gitops.unchanged, environment, commit)
                    passed = clean and not any(c.exit_code for c in [*value.setup, *value.checks])
                    value.status = "passed" if passed else "failed"
                    value.problem = (
                        None
                        if passed
                        else (
                            "Setup or validation changed source files; inspect the preserved copy."
                            if not clean
                            else command_problem(value.setup, value.checks)
                        )
                    )
                except asyncio.CancelledError:
                    value.status = "failed"
                    value.problem = "Setup validation stopped; files preserved."
                    raise
                except (ApplicationError, OSError) as error:
                    value.status = "failed"
                    value.problem = (
                        error.message
                        if isinstance(error, ApplicationError)
                        else (
                            "Setup could not run; inspect the local tool configuration and "
                            "preserved files."
                        )
                    )
                finally:
                    if client:
                        await client.stop()
                        if not client.cleanup_confirmed:
                            value.status = "uncertain"
                            value.problem = (
                                "Command cleanup is uncertain. Inspect preserved processes before "
                                "repeating validation."
                            )
                        else:
                            value.pid = None
                            value.commands = []
                    self._save(value)
                    self.active.discard(project_id)
                return self.get(project_id) or value
        finally:
            self.jobs.discard(task)

    async def close(self) -> None:
        self.closing = True
        jobs = list(self.jobs)
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


def command_problem(setup: list[CheckResult], checks: list[CheckResult]) -> str:
    """Name the observed failure and give bounded, evidence-based next steps."""
    phase, failed = next(
        (phase, check)
        for phase, reports in (("Setup", setup), ("Validation", checks))
        for check in reports
        if check.exit_code
    )
    output = failed.output.lower()
    detail = f"{phase} command failed (exit {failed.exit_code}): {failed.command[:200]}. "
    if "library not loaded" in output or "error while loading shared libraries" in output:
        return detail + (
            "The executable's runtime library could not load. Check the library path in "
            "the command output and the declared tool/read paths, or choose an executable "
            "whose dependencies are accessible. Host-shell success does not validate the "
            "managed environment. Save settings and validate again."
        )
    if "command not found" in output or failed.exit_code == 127:
        return detail + (
            "Check the command name and declare its executable in Environment tools. "
            "Save settings and validate again."
        )
    if "permission denied" in output or "operation not permitted" in output:
        return detail + (
            "Check the denied path in the output against the declared tool/read paths. "
            "Use only the access needed by the command; protected state remains excluded. "
            "Save settings and validate again."
        )
    return detail + "Review the command output, correct the command or project, and validate again."
