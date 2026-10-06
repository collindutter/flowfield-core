"""Prepare independent test directories before adopting them through production operations."""

from pathlib import Path

from flowfield.application import Project, ProjectSetup, Workspace


def existing_directory(path: str | Path) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def adopt(workspace: Workspace, request: ProjectSetup) -> Project:
    existing_directory(request.path)
    return workspace.setup_project(request)


def task_request(**values):
    """An independent task fixture; stage-specific journeys supply their real plan."""
    from flowfield.application import TaskCreate
    from flowfield.stage_models import Stage

    values.setdefault(
        "stages",
        [
            Stage(
                id="fixture",
                title="Fixture setup",
                outcome="Prepare the isolated fixture",
                status="completed",
            )
        ],
    )
    return TaskCreate(**values)


def fixture_stage_change(workspace, project_id, task_id):
    """Explicitly reconcile fixture stages when a test changes its task agreement."""
    from flowfield.stage_models import StageChange
    from flowfield.stages import Stages

    current = Stages(workspace).get(project_id, task_id)
    return StageChange(
        expected_revision=current.revision, stages=current.stages, reason=current.reason
    )


def reconcile_fixture_stages(workspace, project_id, task_id):
    from flowfield.stage_models import StageUpdate
    from flowfield.stages import Stages

    change = fixture_stage_change(workspace, project_id, task_id)
    task = workspace.task(project_id, task_id)
    return Stages(workspace).update(
        project_id,
        task_id,
        StageUpdate(**change.model_dump(), agreement_revision=task.agreement_revision),
    )
