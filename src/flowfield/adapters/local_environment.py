"""Legacy native-worker runtime. New ACP preparation lives in local_execution."""

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from flowfield.adapters.git_workspace import GitWorkspace
from flowfield.adapters.git_workspace import baseline as baseline
from flowfield.adapters.git_workspace import contains as contains
from flowfield.adapters.git_workspace import git as git
from flowfield.adapters.toolchain import prepare_tools, toolchain, validate_tools
from flowfield.environment_models import EnvironmentConfig
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunLocation


@dataclass(frozen=True)
class LocalEnvironment:
    root: Path
    checkout: Path
    runtime: Path
    common_git: Path
    python_runtime: Path

    @classmethod
    def prepare(
        cls,
        directory: Path,
        repository: Path,
        run_id: str,
        commit: str,
        config: EnvironmentConfig | None = None,
        protected: list[Path] | None = None,
    ) -> "LocalEnvironment":
        config = config or EnvironmentConfig()
        validate_tools(config, [directory, repository, *(protected or [])])
        root = directory.resolve() / "environments" / run_id
        workspace = GitWorkspace.prepare(root, repository, commit)
        checkout, common = workspace.checkout, workspace.common_git
        runtime = root / "runtime"
        runtime.mkdir()
        for name in ("home", "tmp"):
            (runtime / name).mkdir()
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(runtime / "python")],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ApplicationError(
                "runtime_setup_failed",
                "Could not prepare the attempt's Python runtime. Worktree retained for inspection.",
            ) from error
        prepare_tools(runtime, config)
        return cls(root, checkout, runtime, common, Path(sys.base_prefix).resolve())

    def shell_environment(self) -> dict[str, str]:
        native_git = shutil.which("git") or "/usr/bin/git"
        if sys.platform == "darwin":
            native_git = subprocess.check_output(
                ["/usr/bin/xcrun", "--find", "git"], text=True
            ).strip()
        variables = {
            name: value.replace("$RUNTIME", str(self.runtime)).replace(
                "$CHECKOUT", str(self.checkout)
            )
            for name, value in toolchain(self.runtime)["variables"].items()
        }
        return {
            **variables,
            "PATH": os.pathsep.join(
                [
                    str(self.runtime / "python" / "bin"),
                    str(self.runtime / "bin"),
                    str(Path(native_git).parent),
                    "/usr/bin",
                    "/bin",
                ]
            ),
            "HOME": str(self.runtime / "home"),
            "TMPDIR": str(self.runtime / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "VIRTUAL_ENV": str(self.runtime / "python"),
            "UV_PROJECT_ENVIRONMENT": str(self.runtime / "python"),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_CACHE_DIR": str(self.runtime / "pip-cache"),
            "UV_CACHE_DIR": str(self.runtime / "uv-cache"),
            "UV_PYTHON_DOWNLOADS": "never",
            "npm_config_cache": str(self.runtime / "npm-cache"),
            "npm_config_prefix": str(self.runtime / "npm-prefix"),
            "npm_config_userconfig": str(self.runtime / "npm-user.config"),
            "npm_config_globalconfig": str(self.runtime / "npm-global.config"),
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def snapshot(self, parent: str) -> tuple[str, list[str]]:
        return GitWorkspace(self.root, self.checkout, self.common_git).snapshot(parent)

    def location(self, base: str, result: str | None) -> RunLocation:
        prefix = f"git -C {shlex.quote(str(self.checkout))}"
        return RunLocation(
            workspace=str(self.checkout),
            diff_command=f"{prefix} diff --no-ext-diff --no-textconv {base} {result}"
            if result
            else f"{prefix} status --short",
            try_command=f"cd {shlex.quote(str(self.checkout))}\n"
            f'export PATH={shlex.quote(str(self.runtime / "python" / "bin"))}:"$PATH"\n'
            "# Run the project's checks listed in the worker result above.",
        )
