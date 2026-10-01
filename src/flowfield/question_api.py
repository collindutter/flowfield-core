"""HTTP transport for task questions; application rules live in Questions."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from flowfield.application import Workspace
from flowfield.questions import (
    AnswerRetract,
    Question,
    QuestionAnswer,
    QuestionApply,
    QuestionCreate,
    QuestionFollowUp,
    Questions,
    QuestionWithdraw,
)


def question_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/questions")

    @router.get("")
    def questions(
        project_id: str,
        status: str = "active",
        task_id: str | None = None,
        after: int | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return Questions(workspace()).list(
            project_id, status=status, task_id=task_id, after=after, limit=limit
        )

    @router.post("", status_code=201)
    def ask(project_id: str, request: QuestionCreate) -> Question:
        return Questions(workspace()).ask(project_id, request)

    @router.get("/{identity}")
    def question(project_id: str, identity: str, revision: int | None = None) -> Question:
        return Questions(workspace()).get(project_id, identity, revision)

    @router.get("/{identity}/history")
    def history(
        project_id: str, identity: str, before: int | None = None, limit: int = 10
    ) -> dict[str, Any]:
        return Questions(workspace()).history(project_id, identity, before=before, limit=limit)

    @router.post("/{identity}/answer")
    def answer(project_id: str, identity: str, request: QuestionAnswer) -> Question:
        return Questions(workspace()).answer(project_id, identity, request)

    @router.post("/{identity}/apply")
    def apply(project_id: str, identity: str, request: QuestionApply) -> Question:
        return Questions(workspace()).apply(project_id, identity, request)

    @router.post("/{identity}/correction")
    def correct(project_id: str, identity: str, request: QuestionAnswer) -> Question:
        return Questions(workspace()).correct(project_id, identity, request)

    @router.post("/{identity}/follow-up")
    def follow_up(project_id: str, identity: str, request: QuestionFollowUp) -> Question:
        return Questions(workspace()).follow_up(project_id, identity, request)

    @router.post("/{identity}/retract-answer")
    def retract_answer(project_id: str, identity: str, request: AnswerRetract) -> Question:
        return Questions(workspace()).retract_answer(project_id, identity, request)

    @router.post("/{identity}/withdraw")
    def withdraw(project_id: str, identity: str, request: QuestionWithdraw) -> Question:
        return Questions(workspace()).withdraw(project_id, identity, request)

    return router
