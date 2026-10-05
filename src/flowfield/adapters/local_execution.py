"""Local machine preparation for ACP; no tool inventory, sandbox or provisioning.

The caller supplies the intended host environment explicitly (not a login shell
command or a persisted bag of credentials). Each attempt owns its checkout and
scratch directories. Native harness configuration controls access to host resources.
This foundation is not yet selected by the production scheduler.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from flowfield.adapters.git_workspace import GitWorkspace


@dataclass(frozen=True)
class LocalAttempt:
    run_id: str
    workspace: GitWorkspace
    runtime: Path
    # May include credentials. Never serialize/log the host process environment.
    _environment: Mapping[str, str] = field(repr=False)

    def launch_environment(self) -> dict[str, str]:
        """Independent copy for launch; retain the selected host's HOME/PATH/config."""
        return dict(self._environment)


class LocalHost:
    """One reusable local environment; run state is allocated by prepare().

    This is intentionally a concrete adapter, not a speculative provider interface.
    Ports, databases and global installs are not isolated by a worktree. Project setup
    must use separate resources or the caller must serialize that work.
    """

    def __init__(self, environment: Mapping[str, str]):
        self._environment = dict(environment)

    def prepare(self, directory: Path, repository: Path, run_id: str, commit: str) -> LocalAttempt:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("Run identity must be a simple name, not a path")
        workspace = GitWorkspace.prepare(
            directory.resolve() / "local-attempts" / run_id, repository, commit
        )
        runtime = workspace.root / "runtime"
        for name in ("tmp", "output"):
            (runtime / name).mkdir(parents=True)
        # Git location variables from a parent process must not redirect commands to
        # another checkout/index. Keep native Git configuration and credential helpers.
        variables = {
            key: value
            for key, value in self._environment.items()
            if key
            not in {
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_NAMESPACE",
            }
        }
        variables.update(
            PWD=str(workspace.checkout),
            TMPDIR=str(runtime / "tmp"),
            TMP=str(runtime / "tmp"),
            TEMP=str(runtime / "tmp"),
            FLOWFIELD_RUN_ID=run_id,
            FLOWFIELD_WORKSPACE=str(workspace.checkout),
            FLOWFIELD_RUNTIME_DIR=str(runtime),
        )
        return LocalAttempt(run_id, workspace, runtime, MappingProxyType(variables))
