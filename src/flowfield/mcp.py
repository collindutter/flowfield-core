"""MCP tools over the service's application operations; no harness-specific state."""

import json
from collections.abc import Callable
from typing import Any, Literal

from anyio import to_thread
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ValidationError

from flowfield.activity import ActivityCreate, DecisionWithdraw, EntryKind
from flowfield.application import (
    MilestoneCreate,
    MilestoneEdit,
    MilestoneIdentifier,
    ProjectEdit,
    ProjectPrefix,
    ProjectSetup,
    TaskCreate,
    TaskEdit,
    TaskIdentifier,
    TaskPriority,
    TaskProgress,
    TaskPublish,
    TaskReconcile,
    Workspace,
)
from flowfield.conversation_api import add_conversation_tools
from flowfield.errors import ApplicationError
from flowfield.guidance import Guidance, GuidanceChange, template
from flowfield.inspection import Inspections
from flowfield.inspection_models import Inspection, InspectionConfig, InspectionPrepare
from flowfield.project_config import Identifier
from flowfield.questions import (
    AnswerRetract,
    QuestionAnswer,
    QuestionApply,
    QuestionCreate,
    QuestionFollowUp,
    Questions,
    QuestionWithdraw,
)
from flowfield.reads import ContextReads, receipt
from flowfield.search import Entity, History, Search
from flowfield.supervisor import Supervisor


def create_mcp(
    workspace: Callable[[], Workspace],
    supervisor: Callable[[], Supervisor] | None = None,
    *,
    origin: str | None = None,
) -> FastMCP:
    mcp = FastMCP(
        "Flowfield",
        instructions=template("mcp-instructions.md").strip(),
        host="127.0.0.1",
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    add_conversation_tools(mcp, workspace)

    async def invoke(action: Callable[[], dict[str, Any]]) -> CallToolResult:
        failed = False
        try:
            payload = await to_thread.run_sync(action)
        except ApplicationError as error:
            payload = {"error": {"code": error.code, "message": error.message}}
            failed = True
        except ValidationError as error:
            payload = {"error": {"code": "invalid_request", "message": str(error)[:2000]}}
            failed = True
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
            isError=failed,
        )

    async def mutate(action: Callable[[], dict[str, Any]]) -> CallToolResult:
        return await invoke(lambda: receipt(action()))

    def reads() -> ContextReads:
        if origin is not None:
            return ContextReads(workspace(), origin)
        request = mcp.get_context().request_context.request
        request_origin = (
            f"{request.url.scheme}://{request.url.netloc}" if request is not None else ""
        )
        return ContextReads(workspace(), request_origin)

    def attributed[T: BaseModel](request: T) -> T:
        if "author" not in request.model_fields_set:
            return request.model_copy(update={"author": "agent"})
        return request

    def inspection_view(value: Inspection | None, offset: int = 0) -> dict[str, Any]:
        if value is None:
            return {"inspection": None}
        if offset < 0 or offset > len(value.command):
            raise ApplicationError("invalid_offset", "Choose an offset within the command text.")
        result = value.model_dump(exclude={"setup_commands", "run_command"})
        result["command"] = value.command[offset : offset + 8000]
        result["command_next_offset"] = (
            offset + 8000 if len(value.command) > offset + 8000 else None
        )
        return result

    @mcp.tool(annotations=read)
    async def get_inspection_settings(project_id: Identifier) -> CallToolResult:
        """Read the project run command. Setup comes from integration settings."""
        return await invoke(lambda: Inspections(workspace()).settings(project_id).model_dump())

    @mcp.tool(annotations=write)
    async def configure_inspection(
        project_id: Identifier, settings: InspectionConfig
    ) -> CallToolResult:
        """Save the agreed local run command. Does not execute it or invalidate code approval."""
        return await invoke(
            lambda: Inspections(workspace()).configure(project_id, settings).model_dump()
        )

    @mcp.tool(annotations=read)
    async def get_inspection(
        project_id: Identifier,
        inspection_id: Identifier | None = None,
        result_id: Identifier | None = None,
        command_offset: int = 0,
    ) -> CallToolResult:
        """Read a retained copy by ID, or the latest copy for a result.
        Omitting both IDs only reads a legacy destination snapshot, if one exists.
        Does not prepare files or run code. Read all command pages before using the command.
        """
        return await invoke(
            lambda: inspection_view(
                Inspections(workspace()).get(project_id, inspection_id)
                if inspection_id
                else Inspections(workspace()).latest(project_id, result_id),
                command_offset,
            )
        )

    @mcp.tool(annotations=write)
    async def prepare_inspection(
        project_id: Identifier, request: InspectionPrepare
    ) -> CallToolResult:
        """Try the selected result candidate; result_id is required.
        expected_revision binds that result.
        Prepares an independent checkout and prints commands; never runs setup/application code.
        Reuses the saved copy without overwriting edits; new_copy explicitly preserves it and
        creates another. Read remaining command pages before use. No approval or branch update.
        """
        return await invoke(
            lambda: inspection_view(Inspections(workspace()).prepare(project_id, request))
        )

    @mcp.tool(annotations=read)
    async def list_projects(after: str | None = None, limit: int = 20) -> CallToolResult:
        """Discover project IDs and paths in bounded pages."""
        return await invoke(lambda: reads().projects(after=after, limit=limit))

    @mcp.tool(annotations=read)
    async def get_project(project_id: Identifier) -> CallToolResult:
        """Read a project's description and revision."""
        return await invoke(lambda: reads().project(project_id))

    @mcp.tool(annotations=write)
    async def edit_project(project_id: Identifier, changes: ProjectEdit) -> CallToolResult:
        """Update supplied project fields with a revision check; omitted fields stay unchanged."""
        return await mutate(
            lambda: workspace().edit_project(project_id, attributed(changes)).model_dump()
        )

    @mcp.tool(annotations=write)
    async def initialize_project(
        project_id: Identifier,
        path: str,
        name: str | None = None,
        task_prefix: ProjectPrefix | None = None,
    ) -> CallToolResult:
        """Adopt an existing absolute directory; preserve files, Git state and portable config."""
        return await mutate(
            lambda: (
                workspace()
                .setup_project(
                    ProjectSetup(
                        id=project_id, path=path, name=name, task_prefix=task_prefix, author="agent"
                    )
                )
                .model_dump()
            )
        )

    @mcp.tool(annotations=read)
    async def get_project_guidance(project_id: Identifier, preview: bool = True) -> CallToolResult:
        """Preview packaged guidance, installed ownership, instruction files and Git baseline.
        Use preview=false for compact status without repeating instruction text.
        Read the full preview before deliberate installation/update.
        Read-only; does not prove a fresh conversation loaded the guide or MCP tools.
        """

        def read_guidance() -> dict[str, Any]:
            value = Guidance(workspace()).get(project_id).model_dump()
            if not preview:
                value["preview_chars"] = {key: len(value.pop(key)) for key in ("section", "skill")}
                value["preview_omitted"] = True
            return value

        return await invoke(read_guidance)

    @mcp.tool(annotations=write)
    async def update_project_guidance(
        project_id: Identifier, change: GuidanceChange
    ) -> CallToolResult:
        """Deliberately install/update or remove unchanged Flowfield-owned project guidance.
        Requires the preview revision. Preserve local edits and unrelated text. Only use
        when the human requests project-guidance adoption/removal; never edit global rules.
        No Git commit, queue enablement or session reload is performed.
        """
        return await mutate(lambda: Guidance(workspace()).change(project_id, change).model_dump())

    @mcp.tool(annotations=read)
    async def get_board(project_id: Identifier) -> CallToolResult:
        """Return-to-work briefing: bounded counts, attention, changes and suggested next action."""
        return await invoke(lambda: reads().overview(project_id))

    @mcp.tool(annotations=read)
    async def search_context(
        project_id: Identifier,
        query: str,
        entity: Entity | None = None,
        kind: str | None = None,
        task_id: str | None = None,
        history: History = "current",
        since: str | None = None,
        until: str | None = None,
        before: int | None = None,
        limit: int = 20,
    ) -> CallToolResult:
        """Search words in project evidence with bounded snippets and exact source references.
        Defaults to current sources; history/all includes old task/question revisions and
        superseded decisions/results, clearly labeled. kind filters decision/note/handoff/
        event or task type. Dates use YYYY-MM-DD. Follow sources before treating a hit as intent.
        """
        return await invoke(
            lambda: Search(workspace(), reads().browser_origin).page(
                project_id,
                query,
                entity=entity,
                kind=kind,
                task_id=task_id,
                history=history,
                since=since,
                until=until,
                before=before,
                limit=limit,
            )
        )

    @mcp.tool(annotations=read)
    async def list_milestones(
        project_id: Identifier, after: str | None = None, limit: int = 20
    ) -> CallToolResult:
        """Read milestone groupings without a dependency or work-status lifecycle."""
        return await invoke(lambda: reads().milestones(project_id, after=after, limit=limit))

    @mcp.tool(annotations=read)
    async def get_milestone(
        project_id: Identifier, milestone_id: MilestoneIdentifier
    ) -> CallToolResult:
        """Read a milestone by project-local key (M-1) or internal ID."""
        return await invoke(lambda: reads().milestone(project_id, milestone_id))

    @mcp.tool(annotations=write)
    async def create_milestone(
        project_id: Identifier, milestone: MilestoneCreate
    ) -> CallToolResult:
        """Create an optional grouping; omit id to generate one."""
        return await mutate(
            lambda: workspace().create_milestone(project_id, attributed(milestone)).model_dump()
        )

    @mcp.tool(annotations=write)
    async def edit_milestone(
        project_id: Identifier, milestone_id: MilestoneIdentifier, changes: MilestoneEdit
    ) -> CallToolResult:
        """Edit supplied milestone fields with a revision check."""
        return await mutate(
            lambda: (
                workspace()
                .edit_milestone(project_id, milestone_id, attributed(changes))
                .model_dump()
            )
        )

    @mcp.tool(annotations=read)
    async def list_tasks(
        project_id: Identifier,
        include_archived: bool = False,
        status: str | None = None,
        task_type: str | None = None,
        milestone_id: str | None = None,
        readiness: str | None = None,
        query: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> CallToolResult:
        """Page summaries by task number; filter status/type/milestone/readiness or key/title.

        Empty milestone_id selects ungrouped work. Archive excluded by default.
        No descriptions/history.
        Position expresses upcoming priority; list order stays stable while work moves.
        """
        return await invoke(
            lambda: reads().tasks(
                project_id,
                include_archived=include_archived,
                status=status,
                task_type=task_type,
                milestone_id=milestone_id,
                readiness=readiness,
                query=query,
                after=after,
                limit=limit,
            )
        )

    @mcp.tool(annotations=read)
    async def get_task(project_id: Identifier, task_id: TaskIdentifier) -> CallToolResult:
        """Read current agreement, readiness, latest authored update and five refs per relationship.

        No revisions. Relationship counts and truncated_fields identify omitted context.
        Read complete prerequisites before replacing a dependency set.
        """
        return await invoke(lambda: reads().task(project_id, task_id))

    @mcp.tool(annotations=read)
    async def list_task_relationships(
        project_id: Identifier,
        task_id: TaskIdentifier,
        relation: Literal["prerequisites", "blocked_by", "dependents"] = "prerequisites",
        after: int | None = None,
        limit: int = 20,
    ) -> CallToolResult:
        """Page related task summaries by task number; use counts on get_task to see omissions."""
        return await invoke(
            lambda: reads().relationships(
                project_id, task_id, relation=relation, after=after, limit=limit
            )
        )

    @mcp.tool(annotations=read)
    async def list_task_revisions(
        project_id: Identifier, task_id: TaskIdentifier, before: int | None = None, limit: int = 20
    ) -> CallToolResult:
        """Page revision metadata newest first. Use get_text with revision for saved fields."""
        return await invoke(
            lambda: reads().revisions(project_id, task_id, before=before, limit=limit)
        )

    @mcp.tool(annotations=read)
    async def get_text(
        project_id: Identifier,
        resource: Literal["task", "milestone", "project", "activity", "question", "result"],
        identity: str | None = None,
        field: str = "body",
        revision: int | None = None,
        offset: int = 0,
        limit: int = 4000,
    ) -> CallToolResult:
        """Deliberately read full text in bounded chunks; next_offset continues.

        Task: body/change_note/reconciliation_reason/dependencies.
        Result: summary/checks/limitations/feedback/problem/correction.
        Project: description/path. Milestone and activity: body. Dependencies are a JSON array.
        Pass returned revision on subsequent task/project/milestone chunks. Activity is immutable.
        """
        return await invoke(
            lambda: reads().text(project_id, resource, identity, field, revision, offset, limit)
        )

    @mcp.tool(annotations=read)
    async def get_activity(project_id: Identifier, entry_id: Identifier) -> CallToolResult:
        """Read one entry with replacement links and a bounded body; get_text retrieves the rest."""
        return await invoke(lambda: reads().activity_entry(project_id, entry_id))

    @mcp.tool(annotations=write)
    async def create_task(project_id: Identifier, task: TaskCreate) -> CallToolResult:
        """Capture agreed work, default feature in Backlog; first search for an existing task.

        For actionable intent include preparation with completion and expected_decision_sequence
        zero for a new task. Capture and preparation commit atomically or neither is saved. Check
        code destination/check settings first; if genuinely missing, save intent without
        preparation and explain the setup blocker. Never label code as report to bypass setup.
        Does not enable the queue. Only choose Up next with human execution authorization.
        """
        return await mutate(
            lambda: workspace().create_task(project_id, attributed(task)).model_dump()
        )

    @mcp.tool(annotations=write)
    async def edit_task(
        project_id: Identifier, task_id: TaskIdentifier, changes: TaskEdit
    ) -> CallToolResult:
        """Refine the existing task; read complete description/dependencies before replacement.

        Include preparation for actionable upcoming work, using completion and the decision
        sequence from get_task, to save and prepare atomically. Failure leaves prior intent
        unchanged. Preserves priority/queue; active work requires reconciliation instead.
        Reconcile existing stages to the new agreement with update_task_stages while idle;
        scheduling waits for that explicit plan update.
        milestone_id=null clears grouping; archived=false restores.
        """
        return await mutate(
            lambda: workspace().edit_task(project_id, task_id, attributed(changes)).model_dump()
        )

    @mcp.tool(annotations=write)
    async def prioritize_task(
        project_id: Identifier, task_id: TaskIdentifier, priority: TaskPriority
    ) -> CallToolResult:
        """Prioritize within/between Backlog and Up next; before_id inserts, omitted appends.

        Moving eligible work into an enabled Up next queue authorizes execution; it does not
        enable a paused queue or override prerequisites. Respect human priorities.
        """
        return await mutate(
            lambda: (
                workspace().prioritize_task(project_id, task_id, attributed(priority)).model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def publish_task(
        project_id: Identifier, task_id: TaskIdentifier, publication: TaskPublish
    ) -> CallToolResult:
        """Prepare an existing task's assignment after reading its full intent and task evidence.
        Prefer create_task/edit_task with preparation when also authoring intent. This legacy
        operation name means preparation, not scheduling: it neither moves the task nor enables
        the queue. Use revision and decision_sequence from get_task. Resolve consequential
        gaps first; unfinished prerequisites may remain. Never grants code approval.
        """
        return await mutate(
            lambda: (
                workspace().publish_task(project_id, task_id, attributed(publication)).model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def record_progress(
        project_id: Identifier, task_id: TaskIdentifier, progress: TaskProgress
    ) -> CallToolResult:
        """Record actual coordinated progress, never intent to start or a worker launch.

        Keep progress current while working. In review means a result awaits review;
        Manual Done requires completion='report' and explicit report acceptance.
        Code and managed results require review_result and service delivery.
        Reopen/defer only after reconciling the work.
        """
        return await mutate(
            lambda: (
                workspace().record_progress(project_id, task_id, attributed(progress)).model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def reconcile_task(
        project_id: Identifier, task_id: TaskIdentifier, reconciliation: TaskReconcile
    ) -> CallToolResult:
        """After reviewing affected work, retain its column and record why it remains valid.

        Requires satisfied prerequisites. Re-completing a prerequisite alone does not
        validate dependent results. Read the task, review the result, then explain it.
        """
        return await mutate(
            lambda: (
                workspace()
                .reconcile_task(project_id, task_id, attributed(reconciliation))
                .model_dump()
            )
        )

    @mcp.tool(annotations=read)
    async def list_activity(
        project_id: Identifier,
        task_id: TaskIdentifier | None = None,
        kind: EntryKind | None = None,
        current_only: bool = False,
        before: int | None = None,
        limit: int = 20,
    ) -> CallToolResult:
        """Read task activity or retained project history; next_cursor pages older entries.

        current_only returns only decisions that have not been superseded.
        Omitted task_id means project scope, never all tasks.
        """
        return await invoke(
            lambda: reads().activity(
                project_id,
                task_id=task_id,
                kind=kind,
                current_only=current_only,
                before=before,
                limit=limit,
            )
        )

    @mcp.tool(annotations=write)
    async def add_activity(project_id: Identifier, entry: ActivityCreate) -> CallToolResult:
        """Append a Markdown note, decision or handoff to a task.

        Notes preserve findings/results, never launch or steer workers. A decision does not
        update requirements automatically. supersedes replaces a current same-scope decision.
        An optional stable id makes identical retries safe; entries cannot be edited/deleted.
        Handoffs require expected_task_revision and explicit supersedes (selected handoff ID,
        or null initially). Use short paragraphs for outcome, evidence/limits and next step;
        include work location.
        """
        return await mutate(
            lambda: workspace().add_activity(project_id, attributed(entry)).model_dump()
        )

    @mcp.tool(annotations=read)
    async def list_questions(
        project_id: Identifier,
        status: str = "active",
        task_id: TaskIdentifier | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> CallToolResult:
        """Read bounded Needs you summaries; active includes open and answered, not applied."""
        return await invoke(
            lambda: reads().questions(
                project_id, status=status, task_id=task_id, after=after, limit=limit
            )
        )

    @mcp.tool(annotations=read)
    async def get_question(
        project_id: Identifier, question_id: Identifier, revision: int | None = None
    ) -> CallToolResult:
        """Read one question, current by default. Prior revisions retain earlier answers.

        Long fields are excerpts; get_text with resource=question retrieves complete text.
        """
        return await invoke(lambda: reads().question(project_id, question_id, revision))

    @mcp.tool(annotations=write)
    async def ask_question(project_id: Identifier, question: QuestionCreate) -> CallToolResult:
        """Ask a human question with context/recommendation. Set task_id for one task, or omit
        it for project input with optional affected_task_ids. blocking_scope requires named
        targets and gates only those tasks until application; omit for advisory input. No launch.
        """
        return await mutate(
            lambda: Questions(workspace()).ask(project_id, attributed(question)).model_dump()
        )

    @mcp.tool(annotations=write)
    async def answer_question(
        project_id: Identifier, question_id: Identifier, response: QuestionAnswer
    ) -> CallToolResult:
        """Send the user's answer. Managed task input automatically continues eligible work;
        queue pause/ownership/scope still gate it. Project/coordinator input needs application.
        A pending answer may be edited; once assigned, use correct_answer for new input.
        """
        return await mutate(
            lambda: (
                Questions(workspace())
                .answer(project_id, question_id, attributed(response))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def correct_answer(
        project_id: Identifier, question_id: Identifier, response: QuestionAnswer
    ) -> CallToolResult:
        """Record a correction to consumed input without rewriting its evidence.

        Creates an answered task question owned by the coordinator for reconciliation with
        current work. Does not inject text into a running worker or silently revise its scope.
        """
        return await mutate(
            lambda: (
                Questions(workspace())
                .correct(project_id, question_id, attributed(response))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def apply_answer(
        project_id: Identifier, question_id: Identifier, application: QuestionApply
    ) -> CallToolResult:
        """Atomically update optional task body, record the decision, and resolve this question.

        Only coordinator-owned input; managed task answers are service-delivered. Require a
        saved answer and fresh question/task revisions. Project questions instead need
        expected_project_revision and task_updates with every affected task/revision, even when
        unchanged; description optionally replaces project intent. Effects are atomic and the
        resolution is saved with the project question. If task body is omitted, explain
        why the existing Description already satisfies the answer in decision. Never truncate it.
        Other questions/dependencies still block. No worker is launched or notified.
        """
        return await mutate(
            lambda: (
                Questions(workspace())
                .apply(project_id, question_id, attributed(application))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def follow_up_question(
        project_id: Identifier, question_id: Identifier, follow_up: QuestionFollowUp
    ) -> CallToolResult:
        """Ask a focused clarification of a saved answer, keeping prior answers on this request."""
        return await mutate(
            lambda: (
                Questions(workspace())
                .follow_up(project_id, question_id, attributed(follow_up))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def withdraw_question(
        project_id: Identifier, question_id: Identifier, withdrawal: QuestionWithdraw
    ) -> CallToolResult:
        """Withdraw obsolete input with a reason, without pretending its answer was applied."""
        return await mutate(
            lambda: (
                Questions(workspace())
                .withdraw(project_id, question_id, attributed(withdrawal))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def retract_answer(
        project_id: Identifier, question_id: Identifier, retraction: AnswerRetract
    ) -> CallToolResult:
        """Retract an unapplied answer and reopen the same question.

        Preserve prior responses and blocking scope.
        """
        return await mutate(
            lambda: (
                Questions(workspace())
                .retract_answer(project_id, question_id, attributed(retraction))
                .model_dump()
            )
        )

    @mcp.tool(annotations=write)
    async def withdraw_decision(
        project_id: Identifier, decision_id: Identifier, withdrawal: DecisionWithdraw
    ) -> CallToolResult:
        """Withdraw a current decision with a reason; preserve its history.

        Does not undo code or requirements.
        """
        return await mutate(
            lambda: (
                workspace()
                .withdraw_decision(project_id, decision_id, attributed(withdrawal))
                .model_dump()
            )
        )

    if supervisor is not None:
        from flowfield.execution_mcp import add_execution_tools

        add_execution_tools(mcp, supervisor, reads)
    return mcp
