"""Codex bridge cleanup capability; no native policy or process discovery here.

The receipt covers native turns and tracked terminals in a dedicated app-server.
It does not claim arbitrary daemon, external MCP-service or host containment.
"""

from typing import Any, Literal

from acp.client import ClientSideConnection
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowfield.adapters.acp_session import ShutdownTimeouts
from flowfield.errors import ApplicationError

# The bridge bounds quiescence at 10 seconds. Leave 5 seconds for transport.
CODEX_SHUTDOWN_TIMEOUTS = ShutdownTimeouts(native_cleanup=15)

CAPABILITY = {
    "version": 1,
    "method": "_flowfield/quiesce",
    "scope": "native-turns-and-terminals",
}


class CleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    method: Literal["_flowfield/quiesce"]
    scope: Literal["native-turns-and-terminals"]
    sessionId: str
    status: Literal["confirmed", "uncertain"]
    reason: str | None
    checkedThreads: int = Field(ge=0, le=64)
    stoppedTerminals: int = Field(ge=0, le=256)


def require_cleanup(capabilities: dict[str, Any]) -> None:
    """Call before a managed turn; stock/incompatible bridges fail explicitly."""
    metadata = capabilities.get("_meta")
    value = metadata.get("flowfield.cleanup") if isinstance(metadata, dict) else None
    if value != CAPABILITY or type(value.get("version")) is not int:
        raise ApplicationError(
            "cleanup_unavailable",
            "This Codex bridge does not support verified managed cleanup. "
            "Use the compatible Flowfield bridge build before starting managed work.",
            409,
        )


async def quiesce(
    connection: ClientSideConnection, session_id: str, capabilities: dict[str, Any]
) -> bool:
    require_cleanup(capabilities)
    response = await connection.ext_method("flowfield/quiesce", {"sessionId": session_id})
    if not isinstance(response, dict) or type(response.get("version")) is not int:
        return False
    try:
        receipt = CleanupReceipt.model_validate(response)
    except ValidationError:
        return False
    return (
        receipt.sessionId == session_id
        and receipt.status == "confirmed"
        and receipt.reason is None
        and receipt.checkedThreads > 0
    )
