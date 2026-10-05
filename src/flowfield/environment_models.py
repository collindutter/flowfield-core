"""Historical restricted-runtime settings. Retained for reading saved records only."""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

RESERVED_VARIABLES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "BASH_ENV",
        "ENV",
        "ZDOTDIR",
        "SHELL",
        "CDPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PIP_CONFIG_FILE",
        "PIP_CACHE_DIR",
        "UV_CACHE_DIR",
        "UV_PYTHON_DOWNLOADS",
        "UV_PROJECT_ENVIRONMENT",
        "NPM_CONFIG_CACHE",
        "NPM_CONFIG_PREFIX",
        "NPM_CONFIG_USERCONFIG",
        "NPM_CONFIG_GLOBALCONFIG",
    }
)
RESERVED_PREFIXES = ("GIT_", "DYLD_", "LD_", "CODEX_")
RESERVED_SUFFIXES = ("PROXY",)
VARIABLE_HELP = (
    "Non-secret single-line variables; $RUNTIME and $CHECKOUT expand per private copy. "
    "Case-insensitive reserved names: "
    + ", ".join(sorted(RESERVED_VARIABLES))
    + ". Reserved prefixes: "
    + ", ".join(RESERVED_PREFIXES)
    + "; reserved suffixes: "
    + ", ".join(RESERVED_SUFFIXES)
    + "."
)


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: dict[str, str] = Field(
        default_factory=dict,
        max_length=40,
        description="Executable name to absolute installed path. No login-shell PATH is inherited. "
        "Declare commands the project needs, including helpers such as rg; declaring a tool "
        "does not install it. Validate setup to test access through the worker boundary.",
    )
    read_paths: list[str] = Field(
        default_factory=list,
        max_length=40,
        description="Absolute read-only support paths needed by declared tools. Do not grant "
        "the whole home, project checkout or service state.",
    )
    variables: dict[str, str] = Field(
        default_factory=dict, max_length=40, description=VARIABLE_HELP
    )

    @model_validator(mode="after")
    def valid(self) -> "EnvironmentConfig":
        for name, path in self.tools.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,79}", name):
                raise ValueError("Tool names must be simple executable names.")
            if not Path(path).is_absolute() or len(path) > 2000:
                raise ValueError("Tool paths must be absolute local paths.")
        for path in self.read_paths:
            if not Path(path).is_absolute() or len(path) > 2000:
                raise ValueError("Read paths must be absolute local paths.")
        for name, value in self.variables.items():
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)
                or name.upper() in RESERVED_VARIABLES
                or name.upper().endswith(RESERVED_SUFFIXES)
                or name.upper().startswith(RESERVED_PREFIXES)
            ):
                raise ValueError(f"{name} is reserved by the managed environment.")
            if len(value) > 2000 or "\0" in value or "\n" in value:
                raise ValueError("Environment values must be bounded single-line strings.")
        return self
