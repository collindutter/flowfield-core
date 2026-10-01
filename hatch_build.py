"""Reject distributable artifacts missing their prebuilt admin UI."""

import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name == "wheel" and version == "editable":
            return
        web = Path(self.root) / "src/flowfield/_web"
        index = web / "index.html"
        error = "Admin UI is missing or incomplete. Run `pnpm --dir web build` before packaging."
        if not index.is_file():
            raise RuntimeError(error)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.read_text())
        if not any(asset.endswith(".js") for asset in assets) or not any(
            asset.endswith(".css") for asset in assets
        ):
            raise RuntimeError(error)
        if any(not (web / asset.lstrip("/")).is_file() for asset in assets):
            raise RuntimeError(error)
