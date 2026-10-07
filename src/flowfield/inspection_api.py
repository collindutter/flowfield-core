"""Explicit inspection preparation and read-only saved copies."""

from collections.abc import Callable

from fastapi import APIRouter

from flowfield.application import Workspace
from flowfield.inspection import Inspections
from flowfield.inspection_models import (
    Inspection,
    InspectionConfig,
    InspectionPrepare,
    InspectionSettings,
)


def inspection_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}")

    @router.get("/inspection/settings")
    def settings(project_id: str) -> InspectionSettings:
        return Inspections(workspace()).settings(project_id)

    @router.put("/inspection/settings")
    def configure(project_id: str, request: InspectionConfig) -> InspectionSettings:
        return Inspections(workspace()).configure(project_id, request)

    @router.get("/inspection")
    def latest(project_id: str, result_id: str) -> Inspection | None:
        return Inspections(workspace()).latest(project_id, result_id)

    @router.get("/inspections/{identity}")
    def detail(project_id: str, identity: str) -> Inspection:
        return Inspections(workspace()).get(project_id, identity)

    @router.post("/inspection")
    def prepare(project_id: str, request: InspectionPrepare) -> Inspection:
        return Inspections(workspace()).prepare(project_id, request)

    return router
