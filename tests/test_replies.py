"""The single composer shares exact, transactional bindings with managed workers."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_execution import BASE, fixture, result
from test_input_continuation import question
from test_results import approve, current
from test_results import fixture as result_fixture
from test_supervisor import FakeWorker

from flowfield.application import TaskCreate, TaskEdit, TaskPublish, Workspace
from flowfield.conversation import Conversation
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, RunAction, SettingsEdit, WorkerResult
from flowfield.replies import Replies
from flowfield.reply_models import ReplyBinding, ReplyCreate
from flowfield.results import Results
from flowfield.thread_view import ThreadView


def message(workspace, task="task-0", action="message", body="Why this approach?"):
    gate = Conversation(workspace).eligibility("harbor", task)
    return ReplyCreate(
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        action=action,
        body=body,
    )


def queue(execution, enabled):
    execution.queue(
        "harbor",
        QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=enabled),
    )


def test_message_receipt_pause_restart_capacity_and_cancellation(tmp_path):
    execution = fixture(tmp_path)
    replies = Replies(execution.workspace)
    request = message(execution.workspace)
    queue(execution, False)
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(lambda _: replies.submit("harbor", "task-0", request), range(2)))
    assert receipts[0] == receipts[1]
    assert execution.claim("harbor", BASE, {}) is None
    with pytest.raises(ApplicationError, match="already used"):
        replies.submit("harbor", "task-0", request.model_copy(update={"body": "Different"}))
    restarted = Execution(Workspace(execution.workspace.directory))
    gate = Conversation(restarted.workspace).eligibility("harbor", "task-0")
    assert not gate.enabled and gate.pending_reply_id == receipts[0].id
    queue(restarted, True)
    run = restarted.claim("harbor", BASE, {})
    assert run.purpose == "discussion" and run.reply_id == receipts[0].id
    assert restarted.claim("harbor", BASE, {}) is None
    with pytest.raises(ApplicationError, match="already belongs"):
        replies.cancel("harbor", "task-0", request.id)
    assert restarted.workspace.task("harbor", "task-0").status == "up_next"
    with pytest.raises(ApplicationError, match="processing"):
        replies.submit("harbor", "task-0", message(execution.workspace))
    restarted.finish("harbor", run.id, "stopped")
    stopped = restarted.get("harbor", run.id)
    restarted.retry("harbor", run.id, RunAction(expected_revision=stopped.revision))
    replies.cancel("harbor", "task-0", request.id)
    assert replies.cancel("harbor", "task-0", request.id).status == "cancelled"
    assert replies.submit("harbor", "task-0", request).status == "cancelled"


def test_stale_reply_never_retargets_or_allows_implementation_to_overtake(tmp_path):
    execution = fixture(tmp_path)
    replies = Replies(execution.workspace)
    stale = message(execution.workspace)
    reply = replies.submit("harbor", "task-0", stale)
    task = execution.workspace.task("harbor", "task-0")
    execution.workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, body="Consequentially different scope"),
    )
    assert execution.claim("harbor", BASE, {}) is None
    replies.cancel("harbor", "task-0", reply.id)
    with pytest.raises(ApplicationError, match="changed"):
        replies.submit("harbor", "task-0", stale.model_copy(update={"id": "new"}))


def test_bound_answer_is_one_atomic_receipt_and_continues_once(tmp_path):
    execution = fixture(tmp_path)
    run, question_record = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    replies = Replies(execution.workspace)
    request = message(execution.workspace, action="answer", body="Keep offline support")
    answer = replies.submit("harbor", "task-0", request)
    assert answer.status == "recorded"
    assert replies.submit("harbor", "task-0", request) == answer
    gate = Conversation(execution.workspace).eligibility("harbor", "task-0")
    assert gate.enabled and gate.reason == "answer_editable"
    revision = message(
        execution.workspace, action="answer", body="Keep offline support for all data"
    )
    replies.submit("harbor", "task-0", revision)
    late_edit = message(execution.workspace, action="answer", body="A correction after consumption")
    continuation = execution.claim("harbor", BASE, {BASE: set()})
    assert continuation.input_question_id == question_record.id
    assert (
        "Keep offline support for all data"
        in execution.assignment("harbor", continuation.id)["input"]
    )
    with pytest.raises(ApplicationError, match="processing"):
        replies.submit("harbor", "task-0", late_edit)
    assert execution.claim("harbor", BASE, {}) is None
    thread = ThreadView(execution.workspace).page_view("harbor", "task-0")
    assert len([m for m in thread.items if m.kind == "answer"]) == 2
    assert not any(m.kind == "reply" for m in thread.items)
    latest = ThreadView(execution.workspace).item_view(
        "harbor", "task-0", f"question:{question_record.id}"
    )
    assert latest.kind == "answer" and latest.body == "Keep offline support for all data"


def test_human_test_feedback_reaches_same_task_without_approving_partial_work(tmp_path):
    execution = fixture(tmp_path)
    result(execution, partial=True)
    replies = Replies(execution.workspace)
    text = "I tried the selected version: desktop keyboard play works. Finish the remaining checks."
    request = message(execution.workspace, action="changes", body=text)
    receipt = replies.submit("harbor", "task-0", request)
    assert replies.submit("harbor", "task-0", request) == receipt
    version = Results(execution.workspace).page("harbor", "task-0").items[0]
    assert version.status == "changes_requested" and version.approved_at is None
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run.purpose == "work" and run.task_id == "task-0"
    assignment = execution.assignment("harbor", run.id)
    assert assignment["feedback"] == text and "Add a check" in assignment["predecessor"]
    assert execution.workspace.task("harbor", "task-0").status != "done"


@pytest.mark.parametrize("parallel", [False, True])
def test_human_testing_records_exact_result_without_work_and_reaches_successor(tmp_path, parallel):
    service, repo, work = result_fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    if parallel:
        settings = service.execution.settings("harbor")
        service.execution.configure(
            "harbor",
            SettingsEdit(
                expected_revision=settings.revision,
                model=settings.model,
                effort=settings.effort,
                max_parallel=2,
            ),
        )
        task = service.workspace.create_task(
            "harbor", TaskCreate(title="Independent", status="up_next", body="Investigate")
        )
        service.workspace.publish_task(
            "harbor",
            task.id,
            TaskPublish(
                completion="report",
                expected_revision=task.revision,
                expected_decision_sequence=task.decision_sequence,
            ),
        )
        queue(service.execution, True)
        independent = service.execution.claim("harbor", work.base_commit, {})
        service.execution.started("harbor", independent.id)
        queue(service.execution, False)
    replies = Replies(service.workspace)
    text = "I tried Result 1: keyboard and reload work; narrow view not tested."
    request = message(service.workspace, "work", action="observation", body=text)
    before = service.workspace.task("harbor", "work")
    receipt = replies.submit("harbor", "work", request)
    assert receipt.status == "recorded" and receipt.run_id is None
    assert replies.submit("harbor", "work", request) == receipt
    assert service.execution.claim("harbor", work.base_commit, {}) is None
    assert current(service) == version and service.workspace.task("harbor", "work") == before
    restarted = Workspace(service.workspace.directory)
    entry = ThreadView(restarted).item_view("harbor", "work", "reply:" + receipt.id)
    assert entry.title == "Human testing · Result 1" and entry.result_id == version.id
    assert entry.body == text
    source = Conversation(restarted).source("harbor", "work", "reply:" + receipt.id)
    assert json.loads(source.text)["binding"]["result_id"] == version.id
    replies.submit("harbor", "work", message(service.workspace, "work", action="changes"))
    queue(service.execution, True)
    successor = service.execution.claim("harbor", work.base_commit, {})
    frozen = json.loads(service.execution.assignment("harbor", successor.id)["human_testing"])
    assert frozen["items"][0]["body"] == text
    assert frozen["items"][0]["binding"]["result_id"] == version.id
    assert "not successor code" in frozen["authority"]
    if parallel:
        assert service.execution.get("harbor", independent.id).status == "running"
        assert (
            json.loads(service.execution.assignment("harbor", independent.id)["human_testing"])
            == []
        )


def test_testing_requires_idle_exact_result_and_does_not_reopen_done(tmp_path):
    execution = fixture(tmp_path / "no-result")
    with pytest.raises(ApplicationError, match="Select a result"):
        Replies(execution.workspace).submit(
            "harbor", "task-0", message(execution.workspace, action="observation")
        )
    service, repo, work = result_fixture(tmp_path / "result")
    service.results.process("harbor")
    version = current(service)
    request = message(service.workspace, "work", action="observation")
    approve(service, version)
    service.results.process("harbor")
    replies = Replies(service.workspace)
    with pytest.raises(ApplicationError, match="changed"):
        replies.submit("harbor", "work", request)
    done = current(service)
    receipt = replies.submit(
        "harbor", "work", message(service.workspace, "work", action="observation")
    )
    assert receipt.status == "recorded"
    assert service.workspace.task("harbor", "work").status == "done"
    assert current(service) == done


def test_readonly_reply_preserves_current_candidate_approval_and_done(tmp_path):
    service, repo, work = result_fixture(tmp_path)
    service.results.process("harbor")
    version = current(service)
    replies = Replies(service.workspace)
    request = message(service.workspace, "work")
    replies.submit("harbor", "work", request)
    from flowfield.attention import attention_counts

    with service.workspace.connection() as db:
        assert attention_counts(db, "harbor").get("action", 0) == 0
    with pytest.raises(ApplicationError, match="current reply"):
        approve(service, version)
    queue(service.execution, True)
    run = service.execution.claim("harbor", work.base_commit, {})
    assert run.base_commit == version.candidate_commit
    service.execution.started("harbor", run.id)
    service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        commit=run.base_commit,
        result=WorkerResult(summary="Explanation", checks="Read source"),
    )
    assert service.results.page("harbor", "work").current_run_id == work.id
    assert current(service) == version
    with service.workspace.connection() as db:
        assert attention_counts(db, "harbor")["action"] == 1
    assert service.workspace.task("harbor", "work").status == "in_review"
    approve(service, version)
    service.results.process("harbor")
    assert service.workspace.task("harbor", "work").status == "done"
    replies.submit("harbor", "work", message(service.workspace, "work", body="Explain more"))
    done = service.workspace.task("harbor", "work")
    with pytest.raises(ApplicationError, match="Cancel the pending message"):
        service.workspace.edit_task(
            "harbor", "work", TaskEdit(expected_revision=done.revision, archived=True)
        )
    next_run = service.execution.claim("harbor", work.base_commit, {})
    from flowfield.browser import BrowserReads

    assert "active worker" in BrowserReads(service.workspace).task("harbor", "work").archive_blocker
    with pytest.raises(ApplicationError, match="active worker"):
        service.workspace.edit_task(
            "harbor", "work", TaskEdit(expected_revision=done.revision, archived=True)
        )
    assert "Explanation" in service.execution.assignment("harbor", next_run.id)["previous_reply"]
    service.execution.finish(
        "harbor",
        next_run.id,
        "in_review",
        commit=next_run.base_commit,
        result=WorkerResult(summary="More detail", checks="Read source"),
    )
    assert service.workspace.task("harbor", "work").status == "done"
    assert len(service.results.page("harbor", "work").items) == 1
    assert service.integrations._accepted("harbor", work.id).id == work.id
    with pytest.raises(ApplicationError):
        service.integrations._accepted("harbor", next_run.id)


@pytest.mark.parametrize("writes", [False, True])
def test_supervisor_owns_discussion_and_never_delivers_its_edits(tmp_path, monkeypatch, writes):
    service, repo, work = result_fixture(tmp_path)
    service.results.process("harbor")
    selected = current(service)

    class DiscussionWorker(FakeWorker):
        async def run(self, model, effort, prompt, tools):
            instructions = json.loads(prompt)["instructions"]
            assert "read-only discussion" in instructions
            assert "ask_question" not in {t["name"] for t in tools}
            if writes:
                (self.cwd / "accidental.txt").write_text("must never integrate")
            await self.on_tool(
                "submit_result",
                {"summary": "Reasoned answer", "checks": "Read source", "outcome": "complete"},
            )
            return {"status": "completed"}

    monkeypatch.setattr("flowfield.supervisor.CodexAgent", DiscussionWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda _: "fixture-process")
    Replies(service.workspace).submit("harbor", "work", message(service.workspace, "work"))
    queue(service.execution, True)
    run = service.execution.claim("harbor", work.base_commit, {})
    asyncio.run(service._execute(run, repo))
    finished = service.execution.get("harbor", run.id)
    assert finished.status == ("failed" if writes else "accepted"), finished.problem
    assert current(service) == selected
    assert service.workspace.task("harbor", "work").status == "in_review"


def test_thread_is_bounded_links_exact_revisions_and_keeps_long_sources(tmp_path):
    execution = fixture(tmp_path)
    thread = ThreadView(execution.workspace)
    for index in range(24):
        task = execution.workspace.task("harbor", "task-0")
        execution.workspace.edit_task(
            "harbor",
            task.id,
            TaskEdit(
                expected_revision=task.revision, body=f"Revision {index}\n" + "Detail " * 1200
            ),
        )
    page = thread.page_view("harbor", "task-0")
    assert len(page.items) == 20 and page.next_cursor
    newest = page.items[0]
    assert newest.truncated and len(newest.body) == 6000
    assert len(thread.item_view("harbor", "task-0", newest.id, full=True).body) > 6000
    assert thread.item_view("harbor", "task-0", "definition:1").title == "Task defined"
    older = thread.page_view("harbor", "task-0", page.next_cursor)
    assert not {m.id for m in older.items} & {m.id for m in page.items}


def test_registered_mcp_reply_and_browser_thread_share_durable_receipts(tmp_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from test_connection import running_service

    execution = fixture(tmp_path)
    request = message(execution.workspace)

    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
            httpx.AsyncClient(base_url=base, trust_env=False) as api,
        ):
            await session.initialize()
            scope = {"project_id": "harbor", "task_id": "task-0"}
            gate = await session.call_tool("get_task_input", scope)
            assert gate.structuredContent["enabled"]
            args = {**scope, "request": request.model_dump()}
            first = await session.call_tool("reply_to_task", args)
            assert not first.isError
            assert (
                await session.call_tool("reply_to_task", args)
            ).structuredContent == first.structuredContent
            thread = (await api.get("/api/projects/harbor/tasks/task-0/thread")).json()
            assert thread["items"][0]["body"] == request.body
            cancelled = await session.call_tool(
                "cancel_task_reply", {**scope, "reply_id": request.id}
            )
            assert cancelled.structuredContent["status"] == "cancelled"
            assert (await api.get("/api/projects/harbor/tasks/task-0/input-eligibility")).json()[
                "enabled"
            ]
            assert not execution.settings("harbor").enabled

    with running_service(execution.workspace.directory) as base:
        asyncio.run(exercise(base))
