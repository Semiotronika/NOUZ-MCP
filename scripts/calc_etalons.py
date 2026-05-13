#!/usr/bin/env python3
"""Compatibility wrapper for the packaged nouz-calc-etalons command."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nouz_mcp.calc_etalons import main


if __name__ == "__main__":
    raise SystemExit(main())
