"""Prepare independent test directories before adopting them through production operations."""

from pathlib import Path

from flowfield.application import Project, ProjectSetup, Workspace


def existing_directory(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def adopt(workspace: Workspace, request: ProjectSetup) -> Project:
    existing_directory(request.path)
    return workspace.setup_project(request)
