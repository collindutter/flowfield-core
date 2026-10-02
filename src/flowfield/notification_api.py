"""Service lifetime and HTTP delivery for persistent notifications."""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from flowfield.application import Workspace
from flowfield.notifications import (
    Notification,
    NotificationPage,
    Notifications,
    NotificationSettings,
    OperationNotice,
)
from flowfield.updates import Updates, UpdateStatus


class DismissNotices(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=200)
    through: int | None = Field(default=None, ge=0)


class BrowserClaim(BaseModel):
    deliver: bool = True


class UpdateCheck(BaseModel):
    reason: Literal["startup", "manual"] = "manual"


class UpdateSettings(BaseModel):
    automatic: bool


class NotificationService:
    def __init__(self, workspace: Workspace):
        self.notifications = Notifications(workspace)
        self.updates = Updates(workspace)
        self.stopped = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.updates.reconcile()
        self.notifications.sync_attention()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                await asyncio.to_thread(self.notifications.sync_attention)
                self.updates.trigger("hourly")
            except Exception:
                logging.getLogger(__name__).exception("Notification refresh failed; retrying")
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=3)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self.stopped.set()
        if self.task:
            # Join the database-owning thread before Supervisor releases workspace ownership.
            cancelled = False
            while not self.task.done():
                try:
                    await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    cancelled = True
            self.task.result()
            await self.updates.close()
            if cancelled:
                raise asyncio.CancelledError
            return
        await self.updates.close()


def notification_router(service: Callable[[], NotificationService]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/notifications")
    def list_notifications() -> NotificationPage:
        return service().notifications.page()

    @router.post("/notifications/operations")
    def operation(value: OperationNotice) -> NotificationPage:
        return service().notifications.operation(value)

    @router.post("/notifications/dismiss")
    def dismiss(value: DismissNotices) -> NotificationPage:
        return service().notifications.dismiss(ids=value.ids, through=value.through)

    @router.get("/notifications/settings")
    def settings() -> NotificationSettings:
        return service().notifications.settings()

    @router.put("/notifications/settings")
    def configure(value: NotificationSettings) -> NotificationSettings:
        service().notifications.sync_attention()
        return service().notifications.configure(value)

    @router.post("/notifications/browser/claim")
    def claim(value: BrowserClaim) -> list[Notification]:
        return service().notifications.claim_browser(deliver=value.deliver)

    @router.get("/updates")
    def updates() -> UpdateStatus:
        return service().updates.status()

    @router.post("/updates/check")
    async def check(value: UpdateCheck) -> UpdateStatus:
        return service().updates.trigger(value.reason)

    @router.put("/updates/settings")
    def update_settings(value: UpdateSettings) -> UpdateStatus:
        return service().updates.configure(value.automatic)

    return router
