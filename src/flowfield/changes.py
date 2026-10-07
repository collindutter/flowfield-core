"""Coalesced live invalidations; SQLite remains the durable source of truth."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class Pending:
    projects: set[str] | None = field(default_factory=set)
    activity: set[tuple[str, str]] = field(default_factory=set)


class Changes:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.subscribers: set[asyncio.Queue[Pending]] = set()

    def publish(self, project_id: str | None = None) -> None:
        # Application writes also run in worker threads. Only touch queues on the loop.
        self.loop.call_soon_threadsafe(self._notify, project_id)

    def publish_activity(self, project_id: str, attempt_id: str) -> None:
        self.loop.call_soon_threadsafe(self._notify, project_id, attempt_id)

    def _notify(self, project_id: str | None, attempt_id: str | None = None) -> None:
        for queue in self.subscribers:
            pending = Pending() if queue.empty() else queue.get_nowait()
            if attempt_id is not None and project_id is not None:
                pending.activity.add((project_id, attempt_id))
                if len(pending.activity) > 100:
                    pending.projects = None
                    pending.activity.clear()
            elif project_id is None:
                pending.projects = None
            elif pending.projects is not None:
                pending.projects.add(project_id)
            queue.put_nowait(pending)

    async def events(self) -> AsyncIterator[dict[str, str | int]]:
        queue: asyncio.Queue[Pending] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(
            Pending(projects=None)
        )  # Every connection resyncs, including after a restart.
        try:
            while True:
                pending = await queue.get()
                if pending.projects is None or pending.projects:
                    yield {
                        "event": "change",
                        "data": json.dumps(
                            {
                                "projects": sorted(pending.projects)
                                if pending.projects is not None
                                else None
                            }
                        ),
                        "retry": 1000,
                    }
                if pending.activity:
                    yield {
                        "event": "activity",
                        "data": json.dumps({"activity": sorted(pending.activity)}),
                    }
        finally:
            self.subscribers.remove(queue)
