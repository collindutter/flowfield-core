"""Public output, replay, bounded storage and shared state; no model calls."""

import asyncio
import base64
import sqlite3

import pytest
from test_execution import BASE, fixture
from test_input_continuation import question

from flowfield.adapters.codex_activity import CodexActivity
from flowfield.application import Workspace
from flowfield.attention import attention_notices, attention_page
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution_models import QueueEdit
from flowfield.questions import QuestionCreate, Questions
from flowfield.run_activity import ActivityRecorder, ActivityUpdate, RunActivity


def test_bounded_snapshot_replays_after_restart_and_rejects_late_output(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})
    store = RunActivity(execution.workspace)
    invalidations = []
    execution.workspace.on_change = invalidations.append
    store.write("harbor", run.id, [ActivityUpdate(key="agent", kind="agent", text="Hello")])
    store.write(
        "harbor", run.id, [ActivityUpdate(key="agent", kind="agent", text=" world", append=True)]
    )
    assert not invalidations
    read = store.read("harbor", run.id)
    assert read.items[0].text == "Hello world" and read.active and read.supported
    assert not store.read("harbor", run.id, read.revision).changed
    restored = RunActivity(Workspace(execution.workspace.directory)).read("harbor", run.id)
    assert restored == read
    store.write(
        "harbor",
        run.id,
        [ActivityUpdate(key=str(i), kind="output", text="x" * 9000) for i in range(120)],
    )
    read = store.read("harbor", run.id)
    assert read.omitted and all(e.omitted for e in read.items)
    assert len(read.items) <= 100 and sum(len(e.text) for e in read.items) <= 60000
    execution.finish("harbor", run.id, "uncertain")
    store.write("harbor", run.id, [ActivityUpdate(key="late", kind="agent", text="Ignore")])
    assert store.read("harbor", run.id).revision == read.revision
    assert not store.read("harbor", run.id).active
    with pytest.raises(ApplicationError):
        store.read("another-project", run.id)


def test_adapter_filters_private_events_and_scopes_output():
    output = []
    adapter = CodexActivity(output.append)
    scope = {"threadId": "thread", "turnId": "turn", "itemId": "wire-id"}
    adapter.event(
        "item/reasoning/textDelta", {**scope, "delta": "private"}, "thread", "turn", set()
    )
    adapter.event(
        "item/agentMessage/delta",
        {**scope, "turnId": "other", "delta": "wrong"},
        "thread",
        "turn",
        set(),
    )
    assert output == []
    adapter.event("item/agentMessage/delta", {**scope, "delta": "Public"}, "thread", "turn", set())
    adapter.event(
        "item/completed",
        {**scope, "item": {"id": "wire-id", "type": "agentMessage", "text": "Public message"}},
        "thread",
        "turn",
        set(),
    )
    assert output[0].key == output[1].key != "wire-id" and not output[1].append
    event = {
        "processId": "owned",
        "stream": "stdout",
        "deltaBase64": base64.b64encode(b"checked\n").decode(),
        "capReached": True,
    }
    adapter.event("command/exec/outputDelta", event, None, None, set())
    assert len(output) == 2
    adapter.event("command/exec/outputDelta", event, None, None, {"owned"})
    assert output[-1].text == "checked\n" and output[-1].omitted
    for chunk in (b"\xe2\x82", b"\xac"):
        adapter.event(
            "command/exec/outputDelta",
            {**event, "stream": "stderr", "deltaBase64": base64.b64encode(chunk).decode()},
            None,
            None,
            {"owned"},
        )
    assert output[-1].text == "€" and not output[-1].append


def test_buffer_drains_and_marks_overflow_without_board_invalidations(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {})

    async def exercise():
        recorder = ActivityRecorder(execution.workspace, "harbor", run.id)
        for i in range(250):
            recorder.emit(ActivityUpdate(key=str(i), kind="output", text="\x1b[31moutput\x1b[0m"))
        assert len(recorder.pending) == 100
        await recorder.flush()
        write = recorder.store.write

        def unavailable(*args):
            raise sqlite3.OperationalError("fixture storage interruption")

        recorder.store.write = unavailable
        recorder.emit(ActivityUpdate(key="lost", kind="output", text="not durable"))
        await recorder.flush()
        assert recorder.lost
        recorder.store.write = write
        await recorder.close()

    asyncio.run(exercise())
    page = RunActivity(execution.workspace).read("harbor", run.id)
    assert page.omitted or any(e.omitted for e in page.items)
    assert all("\x1b" not in e.text for e in page.items)


def test_shared_state_matches_question_attention_and_static_pause(tmp_path):
    execution = fixture(tmp_path)
    run, item = question(execution)
    execution.finish("harbor", run.id, "waiting_for_input", input_checkpoint=BASE)
    board = BrowserReads(execution.workspace).board("harbor")
    state = next(t.state for t in board.tasks if t.id == "task-0")
    attention = attention_page(execution.workspace, "harbor", "action")
    assert attention.items[0].state == state
    assert state.tone == "attention" and item.id in state.href
    notices = attention_notices(execution.workspace)
    assert notices[0].href == state.href
    second = Questions(execution.workspace).ask(
        "harbor",
        QuestionCreate(
            task_id="task-0",
            question="Another choice?",
            context="A separate consequence",
            recommendation="Keep it small",
        ),
    )
    attention = attention_page(execution.workspace, "harbor", "action")
    assert all(item.id in item.state.href for item in attention.items if item.kind == "question")
    assert any(item.id == second.id for item in attention.items)
    execution.queue(
        "harbor", QueueEdit(expected_revision=execution.settings("harbor").revision, enabled=False)
    )
    # Another queued task is static, never an animated executor.
    assert all(
        t.state.tone != "active" for t in BrowserReads(execution.workspace).board("harbor").tasks
    )
