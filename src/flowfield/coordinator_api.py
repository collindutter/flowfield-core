"""Browser chat operations. Reading a project never starts a model turn."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.agent_models import AgentSettingsEdit, AgentSettingsView
from flowfield.agent_settings import AgentSettings
from flowfield.coordinator_models import (
    CoordinatorConversation,
    CoordinatorHistory,
    CoordinatorPage,
    CoordinatorSend,
    CoordinatorTurn,
)
from flowfield.supervisor import Supervisor


def coordinator_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/coordinator")

    @router.get("")
    def history(project_id: str, before: int | None = Query(None, ge=1)) -> CoordinatorHistory:
        return supervisor().coordinator.store.history(project_id, before)

    @router.post("", status_code=201)
    def new(project_id: str) -> CoordinatorConversation:
        return supervisor().coordinator.store.new(project_id)

    @router.get("/{conversation_id}")
    def page(
        project_id: str, conversation_id: str, before: int | None = Query(None, ge=1)
    ) -> CoordinatorPage:
        return supervisor().coordinator.store.page(project_id, conversation_id, before)

    @router.post("/{conversation_id}/messages", status_code=202)
    async def send(
        project_id: str, conversation_id: str, request: CoordinatorSend
    ) -> CoordinatorTurn:
        return supervisor().coordinator.send(project_id, conversation_id, request)

    @router.post("/turns/{turn_id}/stop")
    async def stop(project_id: str, turn_id: str) -> CoordinatorTurn:
        return await supervisor().coordinator.stop(project_id, turn_id)

    @router.post("/turns/{turn_id}/confirm-stopped")
    def recover(project_id: str, turn_id: str) -> CoordinatorTurn:
        return supervisor().coordinator.store.confirm_stopped(project_id, turn_id)

    @router.get("/{conversation_id}/settings")
    def settings(project_id: str, conversation_id: str) -> AgentSettingsView:
        supervisor().coordinator.store.page(project_id, conversation_id)
        return AgentSettings(supervisor().workspace).get(project_id, "coordinator", conversation_id)

    @router.put("/{conversation_id}/settings")
    async def edit(
        project_id: str, conversation_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        service = supervisor()
        service.coordinator.store.page(project_id, conversation_id)
        if request.selection:
            await service.validate_agent_choice(request.selection)
        return AgentSettings(service.workspace).edit(
            project_id, "coordinator", request, conversation_id
        )

    return router
