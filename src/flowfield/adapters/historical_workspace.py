"""Historical workspace inspection only. New execution lives in local_execution."""

import shlex
from dataclasses import dataclass
from pathlib import Path

from flowfield.execution_models import RunLocation


@dataclass(frozen=True)
class HistoricalWorkspace:
    checkout: Path
    runtime: Path

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
