"""Coalesced live invalidations; SQLite remains the durable source of truth."""

import asyncio
import json
from collections.abc import AsyncIterator


class Changes:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.subscribers: set[asyncio.Queue[set[str] | None]] = set()

    def publish(self, project_id: str | None = None) -> None:
        # Application writes also run in worker threads. Only touch queues on the loop.
        self.loop.call_soon_threadsafe(self._notify, project_id)

    def _notify(self, project_id: str | None) -> None:
        for queue in self.subscribers:
            pending = set() if queue.empty() else queue.get_nowait()
            # A catalog/reconnect event subsumes all pending project changes.
            if pending is None or project_id is None:
                queue.put_nowait(None)
            else:
                queue.put_nowait(pending | {project_id})

    async def events(self) -> AsyncIterator[dict[str, str | int]]:
        queue: asyncio.Queue[set[str] | None] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(None)  # Every connection resyncs, including after a restart.
        try:
            while True:
                projects = await queue.get()
                yield {
                    "event": "change",
                    "data": json.dumps(
                        {"projects": sorted(projects) if projects is not None else None}
                    ),
                    "retry": 1000,
                }
        finally:
            self.subscribers.remove(queue)
