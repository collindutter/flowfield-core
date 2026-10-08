"""Attempt-owned browser capture and publication, independent of the agent harness."""

import asyncio
from pathlib import Path
from uuid import uuid4

from flowfield.adapters.browser_session import BrowserInfo, BrowserSessionError, BrowserSessions
from flowfield.adapters.local_execution import LocalAttempt
from flowfield.artifacts import Artifact, Artifacts
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import Run


class AttemptMedia:
    def __init__(
        self,
        execution: Execution,
        run: Run,
        environment: LocalAttempt,
        browsers: BrowserSessions,
    ):
        self.execution, self.run = execution, run
        self.environment, self.browsers = environment, browsers
        self.pending: list[Path] = []

    def _notify(self) -> None:
        if self.execution.workspace.on_change:
            self.execution.workspace.on_change(self.run.project_id)

    def _receipt(self, confirmed: bool) -> None:
        self.execution.save_local(
            self.run.id,
            {
                **self.execution.local(self.run.id),
                "browser_launch_started": True,
                "browser_cleanup_confirmed": confirmed,
            },
        )

    async def publish(self, path: str, title: str, description: str = "") -> Artifact:
        source = Path(path)
        if not source.is_absolute():
            source = self.environment.checkout / source
        return await asyncio.to_thread(
            Artifacts(self.execution.workspace).publish,
            self.run.project_id,
            self.run.id,
            source,
            roots=(self.environment.checkout, self.environment.runtime),
            title=title,
            description=description,
        )

    async def open(self, url: str, record_video: bool) -> BrowserInfo:
        # Persist before launching: a service crash cannot imply browser cleanup.
        self._receipt(False)
        try:
            return await self.browsers.open(
                self.run.id, self.environment.runtime, url, record_video
            )
        except BrowserSessionError as error:
            raise ApplicationError("browser_unavailable", str(error), 409) from error
        finally:
            self._notify()

    async def screenshot(self, title: str, description: str = "") -> Artifact:
        path = self.environment.runtime / "output" / f"screenshot-{uuid4().hex}.png"
        try:
            await self.browsers.screenshot(self.run.id, path)
        except BrowserSessionError as error:
            raise ApplicationError("browser_unavailable", str(error), 409) from error
        return await self.publish(str(path), title, description)

    async def close(self) -> list[Artifact]:
        try:
            self.pending.extend(await self.browsers.close(self.run.id))
            if self.execution.local(self.run.id).get("browser_launch_started"):
                self._receipt(True)
            published = []
            while self.pending:
                artifact = await self.publish(
                    str(self.pending[0]), "Browser recording", "Recorded worker browser session."
                )
                published.append(artifact)
                self.pending.pop(0)
            return published
        finally:
            self._notify()
