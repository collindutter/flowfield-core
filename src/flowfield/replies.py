"""Atomic task input and service-owned discussion attempts, reusing the worker boundary."""

import json
import sqlite3
from uuid import uuid4

from flowfield.application import Workspace, now
from flowfield.conversation import Conversation
from flowfield.environment_models import EnvironmentConfig
from flowfield.errors import ApplicationError
from flowfield.execution_models import Run, WorkerSettings
from flowfield.questions import QuestionAnswer, Questions
from flowfield.reply_models import Reply, ReplyBinding, ReplyCreate
from flowfield.result_models import ResultReview
from flowfield.results import Results
from flowfield.worker_context import enrich


class Replies:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def submit(self, project_id: str, task_id: str, request: ReplyCreate) -> Reply:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            task_id = self.workspace._task(db, project_id, task_id).id
            old = db.execute("SELECT data FROM task_replies WHERE id=?", (request.id,)).fetchone()
            if old:
                reply = Reply.model_validate_json(old[0])
                if (
                    reply.project_id != project_id
                    or reply.task_id != task_id
                    or reply.model_dump(include=set(ReplyCreate.model_fields))
                    != request.model_dump()
                ):
                    raise ApplicationError(
                        "reply_conflict", "This message ID is already used.", 409
                    )
                return reply
            gate = Conversation(self.workspace)._eligibility(db, project_id, task_id)
            if not gate.enabled:
                raise ApplicationError(
                    "input_closed", "Work is processing. Your draft is kept; wait until idle.", 409
                )
            binding = ReplyBinding.model_validate(
                gate.model_dump(include=set(ReplyBinding.model_fields))
            )
            if request.binding != binding:
                raise ApplicationError(
                    "reply_stale",
                    "The task, question or result changed. Review it before "
                    "sending your preserved draft.",
                    409,
                )
            if not request.body.strip():
                raise ApplicationError("empty_reply", "Write a reply before sending.")
            reply = Reply(
                **request.model_dump(), project_id=project_id, task_id=task_id, created_at=now()
            )
            if request.action == "answer":
                if not binding.question_id or not binding.question_revision:
                    raise ApplicationError(
                        "question_required", "There is no current question to answer.", 409
                    )
                Questions(self.workspace)._answer(
                    db,
                    project_id,
                    binding.question_id,
                    QuestionAnswer(
                        expected_revision=binding.question_revision,
                        answer=request.body,
                        author=request.author,
                    ),
                )
                reply.status = "recorded"
            elif binding.question_id:
                raise ApplicationError(
                    "answer_required",
                    "Answer the current question before starting other work.",
                    409,
                )
            elif request.action == "observation":
                if not binding.result_id or not binding.result_revision:
                    raise ApplicationError(
                        "result_required", "Select a result before recording testing.", 409
                    )
                # Exact binding was checked in the same transaction. Recording evidence
                # neither schedules a model nor changes the result, task or approval.
                reply.status = "recorded"
            elif request.action == "changes":
                if not binding.result_id or not binding.result_revision:
                    raise ApplicationError(
                        "result_required",
                        "Select the current result before requesting changes.",
                        409,
                    )
                results = Results(self.workspace)
                version = results._get(db, project_id, binding.result_id)
                results._review(
                    db,
                    project_id,
                    version.id,
                    ResultReview(
                        expected_revision=binding.result_revision,
                        candidate_commit=version.candidate_commit or version.source_commit,
                        action="request_changes",
                        note=request.body,
                        author=request.author,
                    ),
                )
                reply.status = "recorded"
            else:
                task = self.workspace._task(db, project_id, task_id)
                if not task.publication or task.publication_status != "published":
                    raise ApplicationError(
                        "setup_required",
                        "Prepare this task with your coordinating conversation "
                        "before asking a worker.",
                        409,
                    )
                settings = Results(self.workspace).execution._settings(db, project_id)
                if not settings.model or not settings.effort:
                    raise ApplicationError(
                        "model_required",
                        "Choose the project worker model before sending a message.",
                        409,
                    )
            db.execute(
                "INSERT INTO task_replies VALUES (?,?,?,?,?)",
                (project_id, task_id, reply.id, reply.created_at, reply.model_dump_json()),
            )
            return reply

    def claim(
        self, db: sqlite3.Connection, project_id: str, settings: WorkerSettings, baseline: str
    ) -> Run | None:
        rows = db.execute(
            "SELECT data FROM task_replies WHERE project_id=? "
            "AND json_extract(data,'$.status')='pending' ORDER BY created_at",
            (project_id,),
        )
        for row in rows:
            reply = Reply.model_validate_json(row[0])
            task = self.workspace._task(db, project_id, reply.task_id)
            if (
                task.archived
                or task.reconciliation_reason
                or task.publication_status != "published"
            ):
                continue
            if db.execute(
                "SELECT 1 FROM runs WHERE project_id=? AND task_id=? AND status "
                "IN ('preparing','running','stopping','uncertain')",
                (project_id, task.id),
            ).fetchone():
                continue
            if task.agreement_revision != reply.binding.agreement_revision:
                continue  # Never silently retarget input. The UI explains the stale binding.
            selected = (
                Results(self.workspace)._get(db, project_id, reply.binding.result_id)
                if reply.binding.result_id
                else None
            )
            latest = db.execute(
                "SELECT id FROM result_versions WHERE project_id=? AND task_id=? "
                "ORDER BY version DESC LIMIT 1",
                (project_id, task.id),
            ).fetchone()
            if (
                latest[0] if latest else None
            ) != reply.binding.result_id or task.blocking_questions:
                continue
            if selected and (
                selected.revision != reply.binding.result_revision
                or selected.status in ("preparing", "delivering")
            ):
                continue
            if not settings.model or not settings.effort:
                return None
            assert task.publication
            source = selected.candidate_commit or selected.source_commit if selected else baseline
            run = Run(
                id=uuid4().hex,
                project_id=project_id,
                task_id=task.id,
                task_key=task.key,
                environment_id=uuid4().hex,
                model=settings.model,
                effort=settings.effort,
                purpose="discussion",
                reply_id=reply.id,
                agreement_revision=task.agreement_revision,
                decision_sequence=task.decision_sequence,
                base_commit=source,
                completion=task.publication.completion,
                target_branch=task.publication.target_branch,
                created_at=now(),
            )
            config = db.execute(
                "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
            ).fetchone()
            config_data = json.loads(config[0]) if config else {}
            run.environment = EnvironmentConfig.model_validate(config_data.get("environment", {}))
            run.setup_commands = []  # Read-only conversations need no project installation.
            run.setup_timeout_seconds = config_data.get("setup_timeout_seconds", 120)
            # Discussions get current intent and exact selected evidence, not authority to edit it.
            sections = {
                "title": task.key + ": " + task.title,
                "task_type": task.task_type,
                "description": task.body,
                "feedback": reply.body,
                "prerequisites": "[]",
                "project": self.workspace._project(db, project_id).description,
                "milestone": self.workspace._milestone(db, project_id, task.milestone_id).body
                if task.milestone_id
                else "",
                "decisions": json.dumps(
                    [
                        dict(r)
                        for r in db.execute(
                            "SELECT id,body FROM activity a WHERE project_id=? AND kind='decision' "
                            "AND (task_id=? OR task_id IS NULL) AND NOT EXISTS "
                            "(SELECT 1 FROM activity b WHERE b.supersedes=a.id OR "
                            "b.withdraws=a.id)",
                            (project_id, task.id),
                        )
                    ]
                ),
                "selected_result": selected.model_dump_json() if selected else "",
                "reply": reply.model_dump_json(),
                "validation": json.dumps(config_data),
            }
            previous = db.execute(
                "SELECT assignment FROM work_runs WHERE project_id=? AND task_id=? "
                "AND json_extract(data,'$.agreement_revision')=? ORDER BY number DESC LIMIT 1",
                (project_id, task.id, task.agreement_revision),
            ).fetchone()
            if previous:
                prior = json.loads(previous[0])
                for key in ("input", "earlier_answers", "exchange_history", "prerequisites"):
                    if key in prior:
                        sections[key] = prior[key]
            enrich(db, run, sections)
            db.execute(
                "INSERT INTO runs(id,project_id,task_id,status,data,assignment) "
                "VALUES (?,?,?,?,?,?)",
                (
                    run.id,
                    project_id,
                    task.id,
                    run.status,
                    run.model_dump_json(),
                    json.dumps(sections),
                ),
            )
            reply.run_id, reply.status = run.id, "assigned"
            db.execute(
                "UPDATE task_replies SET data=? WHERE id=?", (reply.model_dump_json(), reply.id)
            )
            return run
        return None

    def cancel(self, project_id: str, task_id: str, identity: str) -> Reply:
        with self.workspace.connection(write=True, project_id=project_id) as db:
            task_id = self.workspace._task(db, project_id, task_id).id
            row = db.execute(
                "SELECT data FROM task_replies WHERE project_id=? AND task_id=? AND id=?",
                (project_id, task_id, identity),
            ).fetchone()
            if not row:
                raise ApplicationError("not_found", "Message not found.", 404)
            reply = Reply.model_validate_json(row[0])
            if reply.status not in ("pending", "cancelled"):
                raise ApplicationError(
                    "reply_assigned",
                    "This reply already belongs to a worker; stop that attempt instead.",
                    409,
                )
            reply.status = "cancelled"
            db.execute(
                "UPDATE task_replies SET data=? WHERE id=?", (reply.model_dump_json(), identity)
            )
            return reply
