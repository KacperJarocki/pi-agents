#!/usr/bin/env python3
"""Shortcut for running the port-sweep research protocol.

Default usage:

    python3 research.py

With no arguments this starts the overnight balanced35 research plan in the
background with local subnet discovery enabled. Pass any CLI arguments to use
the lower-level runner directly.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


DEFAULT_RESEARCH_ARGS = [
    "--preset", "balanced35",
    "--gap", "5m",
    "--randomize",
    "--shuffle-phases",
    "--detach",
]


if __name__ == "__main__":
    runner = Path(__file__).resolve().parent / "scripts" / "research-traffic-runner.py"
    args = sys.argv[1:] or DEFAULT_RESEARCH_ARGS
    sys.argv = [str(runner), *args]
    runpy.run_path(str(runner), run_name="__main__")
