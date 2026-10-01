"""Stable task result and exact-version review over HTTP."""

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Query

from flowfield.execution_history import (
    ExecutionHistory,
    ExecutionItem,
    ExecutionPage,
    TaskHistoryPage,
)
from flowfield.execution_models import RunAction
from flowfield.result_models import ResultPage, ResultReview, ResultVersion
from flowfield.supervisor import Supervisor


def result_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}")

    @router.get("/tasks/{task_id}/history")
    def history(
        project_id: str,
        task_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=30, ge=1, le=50),
        mode: Literal["all", "notes", "decisions", "attempts"] = "all",
        include_superseded: bool = False,
    ) -> TaskHistoryPage:
        return ExecutionHistory(supervisor().workspace).timeline(
            project_id, task_id, offset, limit, mode, include_superseded
        )

    @router.get("/tasks/{task_id}/executions")
    def executions(
        project_id: str,
        task_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=50),
        run_id: str | None = None,
    ) -> ExecutionPage:
        return ExecutionHistory(supervisor().workspace).page(
            project_id, task_id, offset, limit, run_id
        )

    @router.get("/tasks/{task_id}/executions/{identity}")
    def execution(project_id: str, task_id: str, identity: str) -> ExecutionItem:
        return ExecutionHistory(supervisor().workspace).get(project_id, task_id, identity)

    @router.get("/tasks/{task_id}/results")
    def listing(
        project_id: str,
        task_id: str,
        before: int | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> ResultPage:
        return supervisor().results.page(project_id, task_id, before, limit)

    @router.get("/results/{identity}")
    def detail(project_id: str, identity: str) -> ResultVersion:
        return supervisor().results.get(project_id, identity)

    @router.post("/results/{identity}/review")
    def review(project_id: str, identity: str, request: ResultReview) -> ResultVersion:
        return supervisor().results.review(project_id, identity, request)

    @router.post("/results/{identity}/prepare")
    def prepare(project_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.reprepare(project_id, identity, request)

    @router.post("/results/{identity}/cancel")
    def cancel(project_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.cancel(project_id, identity, request)

    @router.post("/results/{identity}/retry-delivery")
    def retry_delivery(project_id: str, identity: str, request: RunAction) -> ResultVersion:
        return supervisor().results.retry_delivery(project_id, identity, request)

    @router.post("/results/{identity}/correct")
    async def correct(project_id: str, identity: str, request: RunAction) -> ResultVersion:
        return await asyncio.to_thread(supervisor().results.correct, project_id, identity, request)

    @router.post("/results/{identity}/revalidate")
    async def revalidate(project_id: str, identity: str, request: RunAction) -> ResultVersion:
        return await asyncio.to_thread(
            supervisor().results.revalidate, project_id, identity, request
        )

    return router
