"""Local application data paths."""

import os
from pathlib import Path


def data_path() -> Path:
    if configured := os.environ.get("FLOWFIELD_DATA_DIR"):
        return Path(configured).expanduser().resolve()
    return Path.home() / ".flowfield"
