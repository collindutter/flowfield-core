"""Write a saved terminal launcher; preparation never executes project commands."""

import shlex
from pathlib import Path

from flowfield.adapters.local_environment import LocalEnvironment


def write_launcher(environment: LocalEnvironment, setup: list[str], run: str) -> Path:
    launcher = environment.root / "try-result.sh"
    exports = "".join(
        f"export {key}={shlex.quote(value)}\n"
        for key, value in environment.shell_environment().items()
    )
    # Outside the worktree: preview edits and generated launcher files cannot enter delivery.
    # Exclusive creation prevents accidental replacement of an existing copy's instructions.
    with launcher.open("x") as stream:
        stream.write(
            "#!/bin/sh\n"
            "# Saved Flowfield inspection. Run explicitly; stop with Ctrl+C.\n"
            "set -e\n"
            f"cd {shlex.quote(str(environment.checkout))}\n"
            "unset PYTHONHOME PYTHONPATH\n" + exports + "\n".join([*setup, run]).rstrip() + "\n"
        )
    launcher.chmod(0o600)
    return launcher
