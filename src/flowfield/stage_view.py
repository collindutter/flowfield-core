"""Differences between immutable stage revisions, without inventing execution state."""

from flowfield.stage_models import StagePlan


def stage_changes(current: StagePlan, previous: StagePlan | None) -> tuple[str, list[str]]:
    if previous is None:
        return "Stages defined", []
    before = {s.id: s for s in previous.stages}
    after = {s.id: s for s in current.stages}
    changes: list[str] = []
    structural = False
    for stage in current.stages:
        prior = before.get(stage.id)
        if prior is None:
            changes.append(f"Added {stage.title}.")
            structural = True
            continue
        if prior.title != stage.title:
            changes.append(f"Renamed {prior.title} to {stage.title}.")
            structural = True
        if prior.outcome != stage.outcome:
            changes.append(f"Revised the outcome of {stage.title}.")
            structural = True
        if prior.status != stage.status:
            changes.append(
                f"{stage.title}: {prior.status.capitalize()} → {stage.status.capitalize()}."
            )
    for stage in previous.stages:
        if stage.id not in after:
            changes.append(f"Removed {stage.title}.")
            structural = True
    if [s.id for s in previous.stages if s.id in after] != [
        s.id for s in current.stages if s.id in before
    ]:
        changes.append("Reordered stages.")
        structural = True
    if previous.agreement_revision != current.agreement_revision:
        changes.append("Reconciled stages with the revised task agreement.")
        structural = True
    if not changes:
        return "Stage update", ["Stages unchanged."]
    return "Stages revised" if structural else "Stage changed", changes
