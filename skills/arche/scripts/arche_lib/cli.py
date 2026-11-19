"""Shared entry-point wrapper: one JSON summary line out, exit codes for errors."""
from __future__ import annotations

import json
import sys
from typing import Callable, List

from .errors import ArcheError


def run(main: Callable[[List[str]], dict]) -> None:
    """Call `main(argv)`, print its summary as one JSON line, map errors to exit codes."""
    try:
        summary = main(sys.argv[1:])
    except ArcheError as exc:
        print(f"arche: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
