"""run.json: per-run facts that must stay out of the reproducible outputs."""
from __future__ import annotations

import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import __version__
from .jsonio import read_json, write_json

RUN_FILE = "run.json"
DURATION_DECIMALS = 3


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def record_step(
    out_dir: Path,
    step: str,
    started: float,
    extra: Optional[dict] = None,
    fresh: bool = False,
) -> dict:
    """Add one step's timing to run.json. `fresh` starts a new run record."""
    path = out_dir / RUN_FILE
    previous = {} if fresh or not path.exists() else read_json(path)
    finished = time.time()
    timing = {
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "seconds": round(finished - started, DURATION_DECIMALS),
    }
    updated = {
        **previous,
        "arche_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        **(extra or {}),
        "steps": {**previous.get("steps", {}), step: timing},
    }
    write_json(path, updated)
    return updated
