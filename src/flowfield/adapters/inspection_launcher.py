"""Write a saved terminal launcher; preparation never executes project commands."""

import shlex
from pathlib import Path

from flowfield.adapters.local_environment import LocalEnvironment
from flowfield.adapters.local_execution import LocalAttempt


def write_launcher(
    environment: LocalEnvironment | LocalAttempt, setup: list[str], run: str
) -> Path:
    launcher = environment.root / "try-result.sh"
    variables = environment.shell_environment()
    if isinstance(environment, LocalAttempt):
        # The terminal supplies its own host environment. Save only per-copy paths;
        # never serialize inherited credentials, PATH or native harness settings.
        variables = {
            key: variables[key]
            for key in (
                "PWD",
                "TMPDIR",
                "TMP",
                "TEMP",
                "FLOWFIELD_RUN_ID",
                "FLOWFIELD_WORKSPACE",
                "FLOWFIELD_RUNTIME_DIR",
            )
        }
    exports = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in variables.items())
    # Outside the worktree: preview edits and generated launcher files cannot enter delivery.
    # Exclusive creation prevents accidental replacement of an existing copy's instructions.
    with launcher.open("x") as stream:
        stream.write(
            "#!/bin/sh\n"
            "# Saved Flowfield inspection. Run explicitly; stop with Ctrl+C.\n"
            "set -e\n"
            f"cd {shlex.quote(str(environment.checkout))}\n"
            + (
                "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR "
                "GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE\n"
                if isinstance(environment, LocalAttempt)
                else "unset PYTHONHOME PYTHONPATH\n"
            )
            + exports
            + "\n".join([*setup, run]).rstrip()
            + "\n"
        )
    launcher.chmod(0o600)
    return launcher
