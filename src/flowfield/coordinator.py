"""Explicit coordinator turns; independent of worker scheduling and capacity."""

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from flowfield.adapters.agent_mcp import serve_scope
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_tools import coordinator_scope
from flowfield.coordinator_models import CoordinatorSend, CoordinatorTurn
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.run_activity import ActivityRecorder

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

GUIDANCE = """You are this project's Flowfield coordinator. Help the human shape intent,
prepare tasks and resolve saved input. This is a fresh native session with a bounded recent
conversation handoff, not a replay of an earlier session. Read get_project and get_board
first; canonical Flowfield state takes precedence over old messages. Follow full-text and
pagination links before editing, and read current revisions before every consequential write.
Use only the named scoped MCP connection for Flowfield operations. Do not use ambient
Flowfield connections, CLI or database files. Project identity is already bound to these tools.
Read repository files as needed; do not edit code, run setup, install tools, launch workers or
change Git state. Native filesystem access is read-only and escalation is unavailable.
Capture agreed work in existing tasks when possible. Brainstorming is not authorization.
Task descriptions explain the desired outcome and completion conditions. Milestones group
tasks; only tasks have dependencies. Read current decisions and prepare assignments with
create_task/edit_task when intent is clear. Prepared is not started. Prioritizing into Up next
can make work eligible for an enabled queue: do this only when the human authorized scheduling.
Use apply_answer to reconcile already saved coordinator-owned answers; managed worker answers
are delivered by the service. Never invent a human answer or approve code. Direct the human
to the task conversation for answers and exact result approval. Stop ends this turn, not workers.
After an interrupted turn inspect canonical state before repeating any operation: completed
writes remain committed. Explain useful results and concrete next actions concisely. Link tasks
with Markdown using /projects/{project_id}/tasks/{task_key}. Never claim unsupported actions.
"""


class Coordinator:
    def __init__(self, supervisor: "Supervisor"):
        self.supervisor = supervisor
        self.store = CoordinatorStore(supervisor.workspace)
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.finishing: set[str] = set()
        self.closing = False

    def send(self, project: str, conversation: str, request: CoordinatorSend) -> CoordinatorTurn:
        if self.closing or self.supervisor.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        turn, created = self.store.reserve(
            project, conversation, request, available=len(self.jobs) < 16
        )
        if created:
            job = asyncio.create_task(self._run(turn))
            self.jobs[turn.id] = job
            job.add_done_callback(lambda _: self.jobs.pop(turn.id, None))
            job.add_done_callback(lambda _: self.finishing.discard(turn.id))
        return turn

    def _prompt(self, turn: CoordinatorTurn, server: str) -> str:
        page = self.store.page(turn.project_id, turn.conversation_id, before=turn.number)
        history: list[dict[str, str]] = []
        remaining = 48000
        for previous in reversed(page.items):
            entry = {
                "human": previous.text,
                "status": previous.status,
                "coordinator": "\n\n".join(
                    e.text for e in previous.activity.items if e.kind == "agent"
                ),
            }
            size = len(json.dumps(entry))
            if size > remaining or len(history) >= 8:
                break
            history.insert(0, entry)
            remaining -= size
        return json.dumps(
            {
                "instructions": f"Use the {server} MCP connection.\n" + GUIDANCE,
                "project_id": turn.project_id,
                "flowfield_connection": server,
                "history_is_partial": bool(page.next_before or len(history) < len(page.items)),
                "recent_conversation": history,
                "human_message": turn.text,
            },
            ensure_ascii=False,
        )

    async def _run(self, turn: CoordinatorTurn) -> None:
        workspace = self.supervisor.workspace
        client: CodexAgent | None = None
        temporary: tempfile.TemporaryDirectory[str] | None = None
        recorder = ActivityRecorder(workspace, turn.project_id, turn.id, store=self.store)
        status: Literal["completed", "failed", "stopped"] = "failed"
        notice = ""
        try:
            grant = await coordinator_scope(workspace, turn.project_id)
            async with serve_scope(grant) as server:
                try:
                    project = workspace.project(turn.project_id)
                    temporary = tempfile.TemporaryDirectory(prefix="flowfield-coordinator-")
                    environment = LocalHost(os.environ).launch_environment(
                        Path(project.path), Path(temporary.name)
                    )
                    client = CodexAgent(workspace.directory, Path(project.path), environment)
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        current.native_started = True
                        self.store._save(db, current)
                    await client.start([server])
                    applied = await client.configure(turn.settings.choice, read_only=True)
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        current.status = "running"
                        current.applied = turn.settings.model_copy(update={"choice": applied})
                        self.store._save(db, current)
                    client.on_activity = recorder.emit
                    assert client.session.session_id
                    async with self.supervisor.permissions.turn(
                        turn.project_id,
                        "coordinator",
                        session_id=client.session.session_id,
                        turn_id=turn.id,
                        conversation_id=turn.conversation_id,
                    ):
                        # Read-only coordination cannot authorize native escalation.
                        outcome = await client.prompt(self._prompt(turn, server.name), None)
                    status = "completed" if outcome.get("status") == "completed" else "failed"
                    if status == "failed":
                        notice = (
                            "The agent ended before completing its reply. "
                            "Send a new message to continue."
                        )
                finally:
                    # Fence new task mutations before stopping native work. The server drains
                    # already-running canonical operations; cancellation never rolls them back.
                    grant.revoke()
                    self.finishing.add(turn.id)
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
                        current.status = "stopping"
                        self.store._save(db, current)
                    if client is not None:
                        await client.close()
        except asyncio.CancelledError:
            status = "stopped"
            notice = "Stopped. Saved task changes remain; workers continue independently."
        except ApplicationError as error:
            notice = str(error)
        except Exception:
            notice = (
                "The coordinator disconnected or could not complete this turn. "
                "Check the host harness and try again."
            )
        finally:
            self.finishing.add(turn.id)
            # Startup can fail before entering the scoped endpoint. Fence Stop here too,
            # so a late request cannot cancel the recorder while it commits final output.
            with workspace.connection(write=True, project_id=turn.project_id) as db:
                current = self.store._get(db, turn.project_id, turn.id)
                current.status = "stopping"
                self.store._save(db, current)
            if temporary is not None:
                temporary.cleanup()
            await recorder.close()
            with workspace.connection(write=True, project_id=turn.project_id) as db:
                current = self.store._get(db, turn.project_id, turn.id)
                if client is not None and not client.cleanup_confirmed:
                    current.status = "uncertain"
                    current.notice = (
                        "Native cleanup could not be confirmed. Stop any remaining "
                        "coordinator process on the host, then confirm it stopped."
                    )
                else:
                    current.status = status
                    current.notice = notice
                self.store._save(db, current)

    async def stop(self, project: str, identity: str) -> CoordinatorTurn:
        with self.supervisor.workspace.connection(write=True, project_id=project) as db:
            current = self.store._get(db, project, identity)
            job = self.jobs.get(identity)
            if job and current.status in ("starting", "running"):
                current.status = "stopping"
                self.store._save(db, current)
                # Let a just-created task enter its finally block before cancellation.
                awaitable = job
            else:
                return current
        await asyncio.sleep(0)
        if not awaitable.done() and identity not in self.finishing:
            awaitable.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(awaitable)
        return self.store.get(project, identity)

    async def close(self) -> None:
        self.closing = True
        for identity in list(self.jobs):
            with self.supervisor.workspace.connection() as db:
                row = db.execute(
                    "SELECT project_id FROM coordinator_turns WHERE id=?", (identity,)
                ).fetchone()
            if row:
                await self.stop(row[0], identity)
        if self.jobs:
            await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
