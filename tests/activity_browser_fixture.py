"""Seed/append deterministic active output for browser tests; never launch a harness."""

import json
import sys
from pathlib import Path
from uuid import uuid4

from project_fixtures import task_request

from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.conversation import Conversation
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, Usage, WorkerResult
from flowfield.reply_models import Reply, ReplyBinding
from flowfield.run_activity import ActivityUpdate, RunActivity

ws = Workspace(Path(sys.argv[1]))
execution = Execution(ws)
if sys.argv[2] == "create":
    path = Path(sys.argv[1]) / "stream-project"
    path.mkdir()
    ws.setup_project(ProjectSetup(path=str(path), task_prefix="STR"))
    task = ws.create_task(
        "stream-project",
        task_request(
            id="stream",
            title="Observe live output",
            body="Verify the public activity feed.",
            status="up_next",
        ),
    )
    ws.publish_task(
        "stream-project",
        task.id,
        TaskPublish(
            completion="report",
            expected_revision=task.revision,
        ),
    )
    execution.configure(
        "stream-project", SettingsEdit(expected_revision=1, model="offline-fixture", effort="none")
    )
    execution.queue("stream-project", QueueEdit(expected_revision=2, enabled=True))
    run = execution.claim("stream-project", "a" * 40, {})
    execution.started("stream-project", run.id)
    execution.queue("stream-project", QueueEdit(expected_revision=3, enabled=False))
else:
    run = execution.page("stream-project").items[0]
if sys.argv[2] == "long":
    # Enough independent entries to exercise scrolling after per-entry abbreviation.
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [
            ActivityUpdate(
                key=f"long-{index}",
                kind="output",
                text=f"Saved entry {index}\n" + "Output line\n" * 100,
            )
            for index in range(20)
        ],
    )
if sys.argv[2] == "compact":
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [
            ActivityUpdate(
                key="script",
                kind="command",
                text="python - <<'PY'\n" + "print('retained source')\n" * 100 + "PY\nExit code: 17",
            ),
            ActivityUpdate(
                key="code",
                kind="agent",
                text="Explanation\n```python\nprint('code block source')\n```\nA useful limitation",
            ),
        ],
    )
    execution.usage("stream-project", run.id, Usage(total_tokens=240, cached_input_tokens=100))
elif sys.argv[2] == "stop-race":
    execution.usage("stream-project", run.id, Usage(total_tokens=400, cached_input_tokens=200))
elif sys.argv[2] == "discussion":
    gate = Conversation(ws).eligibility("stream-project", "stream")
    # A retained legacy discussion, seeded as historical state rather than new input.
    reply = Reply(
        binding=ReplyBinding.model_validate(
            gate.model_dump(include=set(ReplyBinding.model_fields))
        ),
        body="Why this approach?",
        action="message",
        project_id="stream-project",
        task_id="stream",
        created_at=run.created_at,
        status="assigned",
    )
    run = run.model_copy(
        update={
            "id": uuid4().hex,
            "purpose": "discussion",
            "reply_id": reply.id,
            "status": "preparing",
            "revision": 1,
            "result": None,
        }
    )
    reply.run_id = run.id
    with ws.connection(write=True) as db:
        db.execute(
            "INSERT INTO task_replies VALUES (?,?,?,?,?)",
            (reply.project_id, reply.task_id, reply.id, reply.created_at, reply.model_dump_json()),
        )
        db.execute(
            "INSERT INTO runs(id,project_id,task_id,status,data,assignment) VALUES (?,?,?,?,?,?)",
            (run.id, run.project_id, run.task_id, run.status, run.model_dump_json(), "{}"),
        )
    execution.started("stream-project", run.id)
    settings = execution.settings("stream-project")
    execution.queue("stream-project", QueueEdit(expected_revision=settings.revision, enabled=False))
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [ActivityUpdate(key="read", kind="tool", text="Reading selected evidence")],
    )
    execution.usage("stream-project", run.id, Usage(total_tokens=750, complete=True))
    execution.finish(
        "stream-project",
        run.id,
        "in_review",
        commit=run.base_commit,
        result=WorkerResult(
            summary="A focused answer to your question.",
            checks="Read selected report",
            limitations="No runtime test was needed.",
        ),
    )
RunActivity(ws).write(
    "stream-project",
    run.id,
    [
        ActivityUpdate(
            key=sys.argv[2],
            kind="output",
            text=sys.argv[2]
            + " observed output"
            + ("\nOutput line" * 100 if sys.argv[2] == "long" else ""),
        )
    ],
)
print(json.dumps({"run_id": run.id}))
