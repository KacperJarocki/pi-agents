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

# Flag/value pairs stay on one line (tests/test_port_sweep_script_sources.py).
# fmt: off
DEFAULT_RESEARCH_ARGS = [
    "--preset", "balanced35",
    "--gap", "5m",
    "--randomize",
    "--shuffle-phases",
    "--detach",
]
# fmt: on


if __name__ == "__main__":
    runner = Path(__file__).resolve().parent / "scripts" / "research-traffic-runner.py"
    args = sys.argv[1:] or DEFAULT_RESEARCH_ARGS
    sys.argv = [str(runner), *args]
    runpy.run_path(str(runner), run_name="__main__")
