"""Resolve project-declared tools without inheriting a login shell or credentials."""

import json
import os
from pathlib import Path
from typing import Any, cast

from flowfield.environment_models import VARIABLE_HELP, EnvironmentConfig
from flowfield.errors import ApplicationError


def validate_tools(config: EnvironmentConfig, protected: list[Path]) -> None:
    for path in [*config.read_paths, *config.tools.values()]:
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise ApplicationError(
                "tool_unavailable", f"Required machine path is unavailable: {path}"
            )
        if resolved == Path.home() or any(
            (p.resolve().is_relative_to(resolved) or resolved.is_relative_to(p.resolve()))
            for p in protected
        ):
            raise ApplicationError(
                "unsafe_environment", f"{path} would expose protected project or service state."
            )
    for name, path in config.tools.items():
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            raise ApplicationError("tool_unavailable", f"{name} is not an executable file: {path}")


def prepare_tools(runtime: Path, config: EnvironmentConfig) -> None:
    directory = runtime / "bin"
    directory.mkdir()
    readable = {str(Path(path).resolve()) for path in config.read_paths}
    for name, installed in config.tools.items():
        target = Path(installed).resolve()
        (directory / name).symlink_to(target)
        readable.add(str(target))
    (runtime / "toolchain.json").write_text(
        json.dumps(
            {
                "read_paths": sorted(readable),
                "variables": config.variables,
                "tools": sorted(config.tools),
            }
        )
    )


def toolchain(runtime: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((runtime / "toolchain.json").read_text()))


def readable_tools(runtime: Path) -> dict[str, str]:
    return {path: "read" for path in toolchain(runtime)["read_paths"]}


def describe_environment(config: EnvironmentConfig) -> dict[str, Any]:
    """Read-only setup facts, not proof that a tool works inside a worker boundary."""
    return {
        "baseline": "Private Python/pip, Git and system /usr/bin:/bin commands; "
        "no login-shell PATH.",
        "configured_tools": [
            {
                "name": name,
                "path": path,
                "available_on_host": Path(path).is_file() and os.access(path, os.X_OK),
            }
            for name, path in sorted(config.tools.items())
        ],
        "variables": VARIABLE_HELP,
        "verification": "validate_project_setup checks worker access using the saved commands. "
        "Include each required tool in setup/checks; host availability alone is not validation.",
    }
