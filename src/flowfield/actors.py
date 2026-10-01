"""Display attribution, never an authentication or permission boundary."""

from typing import Literal

from flowfield.execution_models import Record

ActorRole = Literal["human", "coordinator", "worker", "flowfield", "other"]
ACTOR_ROLES: dict[str, ActorRole] = {
    "human": "human",
    "coordinator": "coordinator",
    "agent": "coordinator",
    "worker": "worker",
    "flowfield": "flowfield",
    "service": "flowfield",
}
ACTOR_LABELS = {
    "human": "You",
    "coordinator": "Coordinator",
    "worker": "Worker",
    "flowfield": "Flowfield",
}


class Actor(Record):
    role: ActorRole
    label: str
    identity: str


def actor(identity: str) -> Actor:
    # agent is the legacy coordinator-interface default. Managed attempts use worker
    # or worker:<attempt>; keep the original identity rather than rewriting evidence.
    role = ACTOR_ROLES.get(identity, "worker" if identity.startswith("worker:") else "other")
    return Actor(role=role, label=ACTOR_LABELS.get(role, identity), identity=identity)
