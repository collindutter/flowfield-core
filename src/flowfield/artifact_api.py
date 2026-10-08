"""Project-scoped artifact reads; only attempt services may publish output."""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse

from flowfield.application import Workspace
from flowfield.artifacts import INLINE_MIMES, Artifact, Artifacts


def artifact_router(workspace: Callable[[], Workspace]) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}")

    @router.get("/runs/{run_id}/artifacts")
    def run_artifacts(project_id: str, run_id: str) -> list[Artifact]:
        return Artifacts(workspace()).list(project_id, run_id=run_id)

    @router.get("/tasks/{task_id}/artifacts")
    def task_artifacts(project_id: str, task_id: str) -> list[Artifact]:
        return Artifacts(workspace()).list(project_id, task_id=task_id)

    @router.get("/artifacts/{artifact_id}")
    def media(project_id: str, artifact_id: str, download: bool = False) -> FileResponse:
        artifacts = Artifacts(workspace())
        artifact = artifacts.get(project_id, artifact_id)
        inline = not download and artifact.mime in INLINE_MIMES
        return FileResponse(
            artifacts.stored_path(artifact),
            media_type=artifact.mime if inline else "application/octet-stream",
            filename=artifact.name,
            content_disposition_type="inline" if inline else "attachment",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    return router
