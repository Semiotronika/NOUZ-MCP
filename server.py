#!/usr/bin/env python3
"""Compatibility wrapper for running NOUZ from the repository root.

The implementation lives in ``nouz_mcp.server``. This file keeps
``python server.py`` working without duplicating the server source.
"""

from nouz_mcp.server import main


if __name__ == "__main__":
    main()
