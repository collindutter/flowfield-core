"""Durable conversation evidence; native sessions are not application identities."""

from typing import Literal

from pydantic import Field

from flowfield.agent_models import AgentRecord, EffectiveAgent
from flowfield.run_activity import RunActivityPage


class CoordinatorConversation(AgentRecord):
    id: str
    number: int
    created_at: str


class CoordinatorHistory(AgentRecord):
    items: list[CoordinatorConversation]
    next_before: int | None = None


class CoordinatorSend(AgentRecord):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,100}$")
    text: str = Field(min_length=1, max_length=16000)


class CoordinatorTurn(AgentRecord):
    id: str
    number: int = 0
    project_id: str
    conversation_id: str
    text: str
    created_at: str
    status: Literal[
        "starting",
        "running",
        "stopping",
        "completed",
        "stopped",
        "failed",
        "interrupted",
        "uncertain",
    ] = "starting"
    settings: EffectiveAgent
    applied: EffectiveAgent | None = None
    activity: RunActivityPage = Field(default_factory=RunActivityPage)
    notice: str = ""
    native_started: bool = False


class CoordinatorPage(AgentRecord):
    conversation: CoordinatorConversation
    items: list[CoordinatorTurn]
    next_before: int | None = None
    active: CoordinatorTurn | None = None
