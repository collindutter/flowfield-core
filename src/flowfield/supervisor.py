"""Service-owned scheduling and the narrow worker bridge. No background planning model."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from pydantic import Field

from flowfield.adapters import git_integration as gitops
from flowfield.adapters.codex_environment import configuration, preflight, run_checks
from flowfield.adapters.codex_worker import CodexWorker
from flowfield.adapters.local_environment import LocalEnvironment, contains, git
from flowfield.adapters.toolchain import toolchain
from flowfield.application import Workspace
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import (
    ModelOption,
    Record,
    Run,
    RunAction,
    RunLocation,
    RunStatus,
    SettingsEdit,
    WorkerResult,
    WorkerSettings,
    WorkerSubmission,
)
from flowfield.integration import Integrations
from flowfield.questions import QuestionCreate
from flowfield.results import Results
from flowfield.run_activity import ActivityRecorder, ActivityUpdate
from flowfield.setup_validation import SetupValidation
from flowfield.stage_models import StageUpdate
from flowfield.stages import Stages
from flowfield.storage import acquire_lock
from flowfield.worker_context import brief_context


class Command(Record):
    command: str = Field(min_length=1, max_length=30000)
    timeout_seconds: int = Field(default=120, ge=1, le=900)


class ReadContext(Record):
    section: str
    offset: int = Field(default=0, ge=0)


class SearchContext(Record):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class WorkerQuestion(Record):
    question: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=8000)
    recommendation: str = Field(min_length=1, max_length=8000)


def worker_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "inputSchema": model.model_json_schema(),
        }
        for name, description, model in (
            (
                "run_command",
                (
                    "Run a shell command in this attempt's prepared environment. "
                    "Inspect files here. Only when your assignment authorizes changes, "
                    "edit files, install project dependencies and run tests. "
                    "Public network is available; private/local services and unrelated "
                    "state remain inaccessible. Returns bounded output."
                ),
                Command,
            ),
            (
                "read_context",
                (
                    "Read a 6000-character page of the frozen assignment section. "
                    "Available sections are listed in the initial brief."
                ),
                ReadContext,
            ),
            (
                "search_context",
                "Search only the frozen assignment. Returns snippets and section references; "
                "read_context retrieves complete evidence. Cannot discover later project changes.",
                SearchContext,
            ),
            (
                "update_stages",
                "Report broad work phases, not file edits or implementation checklists. "
                "Keep human approval/integration outside agent stages. Copy every existing "
                "stage id and outcome verbatim; change status and put evidence in reason. "
                "You may add stages, but cannot reword or remove existing outcomes. "
                "Stages never grant approval or complete the task. "
                "Use the frozen stages revision first, then the revision returned by this tool.",
                StageUpdate,
            ),
            (
                "ask_question",
                (
                    "Ask a blocking human question on this task, then end your turn. "
                    "The service preserves your work and continues with the saved answer "
                    "in a fresh attempt when the queue and assignment allow it."
                ),
                WorkerQuestion,
            ),
            (
                "submit_result",
                (
                    "Submit this attempt's summary, checks actually run and "
                    "limitations. Explicitly choose complete or partial against the entire "
                    "agreed outcome and name remaining_work for partial progress. Before a "
                    "complete result, update any unfinished stages to reflect actual progress. "
                    "Code still requires human approval; complete unchanged-tree reports finish "
                    "on delivery."
                ),
                WorkerSubmission,
            ),
        )
    ]


def process_stamp(pid: int) -> str:
    value = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5
    )
    return value.stdout.strip() if value.returncode == 0 else ""


class WorkerCommands(Protocol):
    async def command(self, script: str, *, timeout_ms: int) -> dict[str, Any]: ...


class WorkerBridge:
    """Fixed server-side run identity; worker input cannot select another task/project."""

    def __init__(self, execution: Execution, run: Run, client: WorkerCommands):
        self.execution, self.run, self.client = execution, run, client
        self.result: WorkerResult | None = None
        self.question_id: str | None = None

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.execution.worker_access(self.run.project_id, self.run.id)
        if self.question_id or self.result:
            raise ApplicationError(
                "report_closed",
                "The report is saved; end this turn. Further work needs a new attempt.",
            )
        activity = getattr(self.client, "on_activity", None)
        titles = {
            "read_context": "Reading task context",
            "search_context": "Searching task context",
            "update_stages": "Updating progress",
            "ask_question": "Asking a question",
            "submit_result": "Submitting an outcome",
        }
        if activity and name in titles:
            from uuid import uuid4

            activity(ActivityUpdate(key=uuid4().hex, kind="tool", text=titles[name]))
        if name == "run_command":
            command = Command.model_validate(arguments)
            return json.dumps(
                await self.client.command(
                    command.command, timeout_ms=command.timeout_seconds * 1000
                )
            )
        if name == "update_stages":
            if self.run.purpose == "discussion":
                raise ApplicationError(
                    "discussion_readonly", "This turn can only answer the message."
                )
            return (
                Stages(self.execution.workspace)
                .update(
                    self.run.project_id,
                    self.run.task_id,
                    StageUpdate.model_validate(arguments),
                    run_id=self.run.id,
                )
                .model_dump_json()
            )
        if name == "read_context":
            request = ReadContext.model_validate(arguments)
            text = self.execution.context_section(self.run.project_id, self.run.id, request.section)
            if request.offset > len(text):
                raise ApplicationError("invalid_offset", "Offset exceeds source length.")
            end = min(len(text), request.offset + 6000)
            return json.dumps(
                {
                    "text": text[request.offset : end],
                    "total_chars": len(text),
                    "next_offset": end if end < len(text) else None,
                }
            )
        if name == "search_context":
            from flowfield.search import assignment_search

            query = SearchContext.model_validate(arguments)
            return json.dumps(
                assignment_search(
                    self.execution.assignment(self.run.project_id, self.run.id),
                    query.query,
                    query.limit,
                )
            )
        if name == "ask_question":
            if self.run.purpose == "discussion":
                raise ApplicationError(
                    "discussion_readonly", "Return your clarification in the reply."
                )
            request_question = WorkerQuestion.model_validate(arguments)
            question = self.execution.ask_question(
                self.run.project_id,
                self.run.id,
                QuestionCreate(
                    **request_question.model_dump(),
                    task_id=self.run.task_id,
                    blocking_scope=("The worker needs this answer before continuing the task."),
                    author=f"worker:{self.run.id[:8]}",
                ),
            )
            self.question_id = question.id
            return (
                "Question recorded in Needs you. End your turn so the service can preserve "
                "your work. A saved answer will be supplied to an eligible fresh attempt."
            )
        if name == "submit_result":
            result = WorkerSubmission.model_validate(arguments)
            self.execution.report_result(self.run.project_id, self.run.id, result)
            self.result = result
            if self.run.purpose == "discussion":
                return "Reply received. End your turn so the service can deliver your answer."
            return (
                "Result received for this attempt. End your turn so the service "
                "can stop execution and capture code for review."
            )
        raise ApplicationError(
            "worker_operation_denied", "This operation is outside the worker's task scope.", 403
        )


class Supervisor:
    def __init__(self, workspace: Workspace):
        self.workspace, self.execution = workspace, Execution(workspace)
        self.integrations = Integrations(workspace)
        self.results = Results(workspace)
        self.setup_validation = SetupValidation(workspace)
        self.delivery_jobs: dict[str, asyncio.Task[None]] = {}
        self.clients: dict[str, CodexWorker] = {}
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.loop_task: asyncio.Task[None] | None = None
        self.lock: BinaryIO | None = None
        self.closing = False

    async def start(self) -> None:
        self.lock = acquire_lock(self.workspace.directory, ".execution.lock")
        try:
            # A different build may have upgraded between Workspace construction and start.
            # connection() rechecks the schema under the shared maintenance lock.
            with self.workspace.connection():
                pass
            self.execution.restart()
            recovery = asyncio.create_task(asyncio.to_thread(self.integrations.restart))
            try:
                await asyncio.shield(recovery)
            except asyncio.CancelledError:
                # Cancellation must not release ownership while recovery's thread still writes.
                while not recovery.done():
                    try:
                        await asyncio.shield(recovery)
                    except asyncio.CancelledError:
                        pass
                recovery.result()
                raise
            self.loop_task = asyncio.create_task(self._schedule())
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    async def model_options(self) -> list[ModelOption]:
        client = CodexWorker(self.workspace.directory)
        try:
            await client.start()
            return await client.models()
        finally:
            await client.close()

    async def configure(self, project_id: str, request: SettingsEdit) -> WorkerSettings:
        models = await self.model_options()
        if not any(item.id == request.model and request.effort in item.efforts for item in models):
            raise ApplicationError(
                "model_unavailable",
                "Choose a model and effort returned by this Codex installation.",
                409,
            )
        return self.execution.configure(project_id, request)

    def _available(self, project_id: str, repository: Path, head: str) -> dict[str, set[str]]:
        with self.workspace.connection() as db:
            followups = []
            for row in db.execute(
                "SELECT data FROM work_runs r WHERE project_id=? "
                "AND status IN ('changes_requested','failed','stopped','waiting_for_input') "
                "AND NOT EXISTS (SELECT 1 FROM work_runs newer WHERE newer.project_id=r.project_id "
                "AND newer.task_id=r.task_id AND newer.number>r.number)",
                (project_id,),
            ):
                previous = Run.model_validate_json(row[0])
                correction = previous.next_correction
                if previous.status != "changes_requested":
                    correction = previous.correction
                followups.extend(
                    [
                        previous.input_checkpoint,
                        previous.input_base_commit,
                        correction.base_commit if correction else previous.result_commit,
                    ]
                )
        commits = self.integrations.available(project_id, head)
        return {
            base: {commit for commit in commits if commit and contains(repository, commit, base)}
            for base in {head, *followups}
            if base
        }

    async def _schedule(self) -> None:
        while not self.closing:
            for project in self.workspace.projects():
                try:
                    if project.id not in self.delivery_jobs:
                        delivery = asyncio.create_task(
                            asyncio.to_thread(self.results.process, project.id)
                        )
                        self.delivery_jobs[project.id] = delivery
                        delivery.add_done_callback(partial(self._delivery_finished, project.id))
                    await asyncio.to_thread(self.integrations.refresh_availability, project.id)
                    if not self.execution.settings(project.id).enabled:
                        continue
                    repository = Path(project.path)
                    head = await asyncio.to_thread(self.integrations.head, project.id)
                    available = await asyncio.to_thread(
                        self._available, project.id, repository, head
                    )
                    run = self.execution.claim(project.id, head, available)
                    self.execution.queue_problem(project.id, None)
                    if run:
                        task = asyncio.create_task(self._execute(run, repository))
                        self.jobs[run.id] = task

                        task.add_done_callback(partial(self._job_finished, run.id))
                except ApplicationError as error:
                    self.execution.queue_problem(project.id, error.message)
                except Exception:
                    self.execution.queue_problem(
                        project.id,
                        (
                            "Scheduling failed. Pause the queue and inspect the local service "
                            "before retrying."
                        ),
                    )
            await asyncio.sleep(0.5)

    def _job_finished(self, identity: str, task: asyncio.Task[None]) -> None:
        self.jobs.pop(identity, None)

    def _delivery_finished(self, identity: str, task: asyncio.Task[None]) -> None:
        self.delivery_jobs.pop(identity, None)
        if not task.cancelled() and task.exception():
            self.execution.queue_problem(
                identity,
                "Result processing failed; inspect the service and preserved result.",
            )

    async def _execute(self, run: Run, repository: Path) -> None:
        client: CodexWorker | None = None
        activity: ActivityRecorder | None = None
        terminal: RunStatus = "failed"
        problem: str | None = None
        result: WorkerResult | None = None
        commit: str | None = None
        input_checkpoint: str | None = None
        starting_commit = run.input_base_commit or run.base_commit
        try:
            sections = self.execution.assignment(run.project_id, run.id)
            for prerequisite in json.loads(sections["prerequisites"]):
                if prerequisite["commit"] and not await asyncio.to_thread(
                    contains, repository, prerequisite["commit"], starting_commit
                ):
                    raise ApplicationError(
                        "baseline_missing_prerequisite",
                        "This retry's code baseline lacks a prerequisite result. "
                        "Integrate the required code before starting a fresh attempt.",
                    )
            environment = await asyncio.to_thread(
                LocalEnvironment.prepare,
                self.workspace.directory,
                repository,
                run.id,
                starting_commit,
                run.environment,
                [self.workspace.directory, *[Path(p.path) for p in self.workspace.projects()]],
            )
            self.execution.save_local(
                run.id,
                {
                    "root": str(environment.root),
                    "checkout": str(environment.checkout),
                    "runtime": str(environment.runtime),
                    "common_git": str(environment.common_git),
                    "python_runtime": str(environment.python_runtime),
                },
            )
            provisional = CodexWorker(environment.checkout)
            config = configuration(environment, provisional.binary)
            client = CodexWorker(environment.checkout, config)
            self.clients[run.id] = client
            await client.start()
            assert client.process
            self.execution.save_local(
                run.id,
                {
                    "root": str(environment.root),
                    "checkout": str(environment.checkout),
                    "runtime": str(environment.runtime),
                    "common_git": str(environment.common_git),
                    "python_runtime": str(environment.python_runtime),
                    "pid": client.process.pid,
                    "process_stamp": await asyncio.to_thread(process_stamp, client.process.pid),
                },
            )
            client.on_commands = lambda commands: self.execution.save_local(
                run.id, {**self.execution.local(run.id), "commands": commands}
            )
            await preflight(
                client,
                environment,
                self.workspace.directory,
                [Path(p.path) for p in self.workspace.projects()],
            )
            if getattr(client, "supports_activity", False):
                activity = ActivityRecorder(self.workspace, run.project_id, run.id)
                client.on_activity = activity.emit
                activity.emit(
                    ActivityUpdate(key="started", kind="status", text="Worker environment ready.")
                )
            setup = await run_checks(client, run.setup_commands, run.setup_timeout_seconds)
            self.execution.record_setup(run.project_id, run.id, setup)
            if any(check.exit_code for check in setup):
                raise ApplicationError(
                    "runtime_setup_failed",
                    "Runtime setup failed before model launch. Inspect output and update settings.",
                )
            if setup and not await asyncio.to_thread(
                gitops.unchanged, environment, starting_commit
            ):
                raise ApplicationError(
                    "runtime_changed_source",
                    "Setup changed source files. Fix setup commands; inspect preserved changes.",
                )
            self.execution.started(run.project_id, run.id)
            bridge = WorkerBridge(self.execution, run, client)
            client.on_tool = bridge.call
            client.on_usage = lambda usage: self.execution.usage(run.project_id, run.id, usage)
            sections = self.execution.assignment(run.project_id, run.id)
            brief = {
                "task": sections["title"],
                "task_type": sections.get("task_type", "feature"),
                "base_commit": run.base_commit,
                "agreement_revision": run.agreement_revision,
                "decision_sequence": run.decision_sequence,
                "completion": run.completion,
                "target_branch": run.target_branch,
                "configured_tools": toolchain(environment.runtime)["tools"],
                **brief_context(sections, discussion=run.purpose == "discussion"),
                "instructions": (
                    "Read complete description/feedback pages when listed as truncated. "
                    "Read input, previous_reply, correction and validation when present. "
                    "Read stages and earlier_answers before work; attempt_history for orientation. "
                    "Use update_stages at broad phase transitions and explain what changed. "
                    "Phases describe the process, not file edits or implementation checklists. "
                    "Keep human approval/integration outside agent stages; Flowfield tracks them. "
                    "Copy existing stage ids/outcomes verbatim; put progress evidence in reason, "
                    "not outcome. Ask about scope changes. Completed plans never grant "
                    "completion, approval or integration. If no stages exist, define a few broad "
                    "phases within the agreement before work (e.g. Explore/Implement/Verify or "
                    "Investigate/Synthesize for findings); no mandatory template. "
                    "Input contains the exact question and saved human answer; prior unfinished "
                    "code is already in this checkout. Preserve the original agreed outcome. "
                    "Use ordinary answers within that scope; if input materially changes it, "
                    "ask a focused clarification instead of silently changing the assignment. "
                    "Correction inputs "
                    "retain both source and target ancestry; resolve conflict markers within scope "
                    "without changing Git metadata. Ask if the required fix changes intent. "
                    "Read nonempty project/milestone constraints, decisions, handoff, questions, "
                    "prerequisite results and predecessor "
                    "context before implementing. read_context provides complete "
                    "sections in pages; search_context searches only this frozen assignment. "
                    "New discoveries are observations, not authority to change intent. "
                    "Configured tools supplement the private Python runtime and system "
                    "utilities. Discover commands on PATH; install project dependencies "
                    "in "
                    "this checkout/runtime when needed. Keep manifests, lockfiles and setup "
                    "instructions reproducible so a clean validation/inspection copy works. "
                    "A setup task must deliver reusable configuration, not merely a temporary "
                    "installation. Missing machine-wide tools/access need a focused question. "
                    "Repository guidance begins at the worktree root; "
                    "do not search parent directories. "
                    "Use run_command to inspect AGENTS.md and "
                    "source files. Python is on PATH in your private runtime. Do not "
                    "commit; the service will capture every tracked/nonignored file "
                    "after execution stops, validate it, and deliver only after human approval. "
                    "Complete the whole agreed outcome. Features deliver usable behavior; "
                    "bugs need regression evidence; maintenance preserves stated constraints; "
                    "investigations report findings, evidence, uncertainty and recommendations. "
                    "The type does not override the actual agreement. Explicitly report partial "
                    "progress and remaining required work if unfinished; do not call a small "
                    "increment complete for a broader task. Code needs human approval; complete "
                    "report-only findings finish when delivered with an unchanged repository tree."
                ),
            }
            if run.purpose == "discussion":
                brief["instructions"] = (
                    "You are a service-managed worker answering a task conversation message. "
                    "Read repository instructions at the checkout root and keep the worker role. "
                    "Do not adopt coordinator responsibilities or invoke the Flowfield CLI. "
                    "This is a read-only discussion, not "
                    "authorization to change code, plans, approval or task completion. Read the "
                    "full current question in feedback. Use the brief's context policy to retrieve "
                    "only the evidence needed to answer it. Start with selected_result for a "
                    "result question or previous_reply for a follow-up. Read full descriptions, "
                    "answers/decisions and repository files when the answer depends on them. "
                    "Do not perform an exhaustive review for a simple clarification. Lead with "
                    "the answer, support it concisely and state uncertainty. Do not modify files "
                    "or run setup/install/test commands. If asked for changes, explain that the "
                    "human can use Request changes. Report uncertainty and questions in your "
                    "reply. Use submit_result with summary as your answer, checks describing only "
                    "what you actually inspected, and outcome complete for this reply only."
                )
            else:
                brief["instructions"] += (
                    " Feedback is bound to the preceding result. Explicit human reports of trying "
                    "that version are human-reported evidence, not tests you "
                    "executed. Use relevant "
                    "human confirmation to resolve manual-test limitations; do not simply repeat "
                    "an unavailable interactive test. Reassess the whole agreed outcome and retain "
                    "other unfinished requirements. Human test evidence is never code approval."
                )
            try:
                outcome = await asyncio.wait_for(
                    client.run(
                        run.model,
                        run.effort,
                        json.dumps(brief, ensure_ascii=False),
                        [
                            tool
                            for tool in worker_tools()
                            if run.purpose == "work"
                            or tool["name"] not in ("update_stages", "ask_question")
                        ],
                    ),
                    900,
                )
            except TimeoutError as error:
                raise ApplicationError(
                    "worker_timeout",
                    (
                        "Worker reached the 15-minute local execution limit. Inspect "
                        "preserved work and explicitly retry."
                    ),
                ) from error
            confirmed = await client.stop()
            if not confirmed:
                terminal, problem = (
                    "uncertain",
                    (
                        "Could not confirm all tracked commands stopped. Keep this slot "
                        "reserved until reconciled."
                    ),
                )
            elif bridge.question_id:
                input_checkpoint, ignored = await asyncio.to_thread(
                    environment.snapshot, starting_commit
                )
                terminal, problem = (
                    "waiting_for_input",
                    "Work preserved; waiting for an answer and an eligible continuation."
                    + (
                        " Ignored runtime files remain in the previous workspace."
                        if ignored
                        else ""
                    ),
                )
            elif outcome.get("status") == "completed" and bridge.result:
                result = bridge.result
                commit, ignored = await asyncio.to_thread(environment.snapshot, starting_commit)
                assert commit
                if run.purpose == "discussion" and await asyncio.to_thread(
                    git, environment.checkout, "rev-parse", commit + "^{tree}"
                ) != await asyncio.to_thread(
                    git, environment.checkout, "rev-parse", starting_commit + "^{tree}"
                ):
                    raise ApplicationError(
                        "discussion_changed_code",
                        "This reply changed files. Changes are preserved but cannot be delivered.",
                    )
                # Generated-file evidence belongs to execution diagnostics, not user limitations.
                self.execution.excluded_files(run.project_id, run.id, ignored)
                terminal = "in_review"
            elif self.execution.get(run.project_id, run.id).status == "stopping":
                terminal, problem = (
                    "stopped",
                    "Worker and tracked commands stopped; work preserved.",
                )
            else:
                problem = (
                    "Worker ended without a submitted result. Inspect preserved work "
                    "and explicitly retry."
                )
        except Exception as error:
            problem = (
                error.message
                if isinstance(error, ApplicationError)
                else (
                    f"Worker preparation/execution failed ({type(error).__name__}). "
                    "Work is preserved."
                )
            )
            if client and not await client.stop():
                terminal, problem = (
                    "uncertain",
                    problem + " Tracked process cleanup could not be confirmed.",
                )
        finally:
            if client:
                await client.close()
            if activity:
                captured = commit or input_checkpoint
                if environment and captured:
                    from flowfield.adapters.activity_diff import captured_changes

                    try:
                        summary = await asyncio.to_thread(
                            captured_changes, environment.checkout, starting_commit, captured
                        )
                    except Exception:
                        summary = "File summary unavailable; captured evidence is preserved."
                    activity.emit(ActivityUpdate(key="captured-files", kind="tool", text=summary))
                with contextlib.suppress(Exception):
                    await activity.close()
            self.clients.pop(run.id, None)
            with contextlib.suppress(ApplicationError):
                self.execution.finish(
                    run.project_id,
                    run.id,
                    terminal,
                    result=result,
                    commit=commit,
                    problem=problem,
                    input_checkpoint=input_checkpoint,
                )

    async def stop(self, project_id: str, run_id: str, request: RunAction) -> Run:
        run = self.execution.stop_requested(project_id, run_id, request)
        if run.status == "stopped":
            return run
        client = self.clients.get(run_id)
        if client:
            confirmed = await client.stop()
            task = self.jobs.get(run_id)
            if task:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 20)
            current = self.execution.get(project_id, run_id)
            if current.status == "stopping":
                return self.execution.finish(
                    project_id,
                    run_id,
                    "stopped" if confirmed else "uncertain",
                    problem="Stop requested; work preserved.",
                )
            return current
        if run_id in self.jobs:
            return run  # Preparation sees the stop before starting a model turn.
        metadata = self.execution.local(run_id)
        pid = metadata.get("pid")
        if pid:
            stamp = await asyncio.to_thread(process_stamp, pid)
            if stamp and stamp != metadata.get("process_stamp"):
                return self.execution.finish(
                    project_id,
                    run_id,
                    "uncertain",
                    problem=(
                        "Process identity changed. Refusing to signal a possibly unrelated"
                        " process; inspect this attempt before recovery."
                    ),
                )
            if stamp:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGTERM)
                await asyncio.sleep(0.5)
                if await asyncio.to_thread(process_stamp, pid):
                    return self.execution.finish(
                        project_id,
                        run_id,
                        "uncertain",
                        problem=(
                            "Owned process still exists after Stop. Work preserved; manual "
                            "inspection required."
                        ),
                    )
        if metadata.get("commands"):
            return self.execution.finish(
                project_id,
                run_id,
                "uncertain",
                problem="The service lost contact with running tools. Harness absence "
                "does not prove those tools stopped. Work and capacity remain reserved; "
                "inspect the local processes before recovery.",
            )
        return self.execution.finish(
            project_id,
            run_id,
            "stopped",
            problem=(
                "Owned harness is absent or stopped. Previous work preserved; retry is explicit."
            ),
        )

    def environment(self, run_id: str) -> LocalEnvironment | None:
        data = self.execution.local(run_id)
        if not data:
            return None
        return LocalEnvironment(
            *[
                Path(data[key])
                for key in ("root", "checkout", "runtime", "common_git", "python_runtime")
            ]
        )

    def location(self, project_id: str, run_id: str) -> RunLocation:
        run = self.execution.get(project_id, run_id)
        environment = self.environment(run_id)
        if not environment:
            root = self.workspace.directory / "environments" / run.id
            checkout = root / "worktree"
            if root.exists():
                return RunLocation(workspace=str(checkout if checkout.exists() else root))
        return (
            environment.location(run.base_commit, run.result_commit)
            if environment
            else RunLocation()
        )

    async def check_integration(self, project_id: str, run_id: str) -> Run:
        run = self.execution.get(project_id, run_id)
        if run.status != "accepted":
            raise ApplicationError(
                "not_accepted", "Accept the result before checking availability.", 409
            )
        await asyncio.to_thread(self.integrations.refresh_availability, project_id)
        return self.execution.get(project_id, run_id)

    async def close(self) -> None:
        self.closing = True
        await self.setup_validation.close()
        if self.loop_task:
            self.loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.loop_task
        for run in self.execution.active():
            if run.id in self.jobs or run.id in self.clients:
                with contextlib.suppress(Exception):
                    await self.stop(
                        run.project_id, run.id, RunAction(expected_revision=run.revision)
                    )
        if self.jobs:
            await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        if self.delivery_jobs:
            await asyncio.gather(*list(self.delivery_jobs.values()), return_exceptions=True)
        if self.lock:
            self.lock.close()
            self.lock = None
