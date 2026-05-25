#!/usr/bin/env python3
"""Shortcut for running the port-sweep research protocol.

Default usage:

    python3 research.py

This intentionally runs only the port-sweep phases. Keep benign IoT traffic
running separately in the background when measuring false positives.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    runner = Path(__file__).resolve().parent / "scripts" / "research-traffic-runner.py"
    sys.argv = [str(runner), *sys.argv[1:]]
    runpy.run_path(str(runner), run_name="__main__")
