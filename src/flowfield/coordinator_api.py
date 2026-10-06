"""Browser chat operations. Reading a project never starts a model turn."""

from collections.abc import Callable

from fastapi import APIRouter, Query

from flowfield.agent_models import AgentCommand, AgentSettingsEdit, AgentSettingsView
from flowfield.agent_settings import AgentSettings
from flowfield.coordinator_models import (
    CoordinatorConversation,
    CoordinatorPage,
    CoordinatorSend,
    CoordinatorTurn,
)
from flowfield.supervisor import Supervisor


def coordinator_router(supervisor: Callable[[], Supervisor]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/coordinator")

    @router.get("")
    def history(
        project_id: str,
        before: int | None = Query(None, ge=1),
        after: int | None = Query(
            None,
            ge=1,
            description="Refresh this turn and newer turns, up to 20 in ascending order.",
        ),
    ) -> CoordinatorPage:
        return supervisor().coordinator.store.page(project_id, before=before, after=after)

    @router.post("", status_code=201)
    def new(project_id: str) -> CoordinatorConversation:
        return supervisor().coordinator.store.new(project_id)

    @router.post("/commands/discover")
    async def commands(project_id: str, refresh: bool = False) -> list[AgentCommand]:
        return await supervisor().coordinator.commands(project_id, refresh=refresh)

    @router.post("/messages", status_code=202)
    async def message(project_id: str, request: CoordinatorSend) -> CoordinatorTurn:
        service = supervisor()
        conversation = service.coordinator.store.new(project_id)
        return service.coordinator.send(project_id, conversation.id, request)

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

    @router.post("/turns/{turn_id}/reset-session", status_code=204)
    def reset_session(project_id: str, turn_id: str) -> None:
        supervisor().coordinator.store.reset_session(project_id, turn_id)

    @router.get("/{conversation_id}/settings")
    def settings(project_id: str, conversation_id: str) -> AgentSettingsView:
        supervisor().coordinator.store.page(project_id, conversation_id)
        return AgentSettings(supervisor().workspace).get(project_id, "coordinator")

    @router.put("/{conversation_id}/settings")
    async def edit(
        project_id: str, conversation_id: str, request: AgentSettingsEdit
    ) -> AgentSettingsView:
        service = supervisor()
        service.coordinator.store.page(project_id, conversation_id)
        if request.selection:
            await service.validate_agent_choice(request.selection)
        return AgentSettings(service.workspace).edit(project_id, "coordinator", request)

    return router
