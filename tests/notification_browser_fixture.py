"""Seed a deterministic release observation; no package registry or model calls."""

import sys
from pathlib import Path

from flowfield import __version__
from flowfield.application import Workspace, now
from flowfield.notifications import save_state, state
from flowfield.updates import Updates

workspace = Workspace(Path(sys.argv[1]))
with workspace.connection(write=True, notify=False) as db:
    saved = state(db, "updates")
    checked = now()
    saved.update(latest_version=sys.argv[2], last_success=checked, last_attempt=checked, error=None)
    save_state(db, "updates", saved)
Updates(workspace, installed=__version__).reconcile()
