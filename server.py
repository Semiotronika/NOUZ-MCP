#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility wrapper for running NOUZ from the repository root.

The canonical implementation lives in nouz_mcp.server. This wrapper keeps
`python server.py` and legacy tests working without maintaining two copies of
the server.
"""

from nouz_mcp import server as _impl

globals().update({
    name: value
    for name, value in vars(_impl).items()
    if not (name.startswith("__") and name.endswith("__"))
})


def main():
    return _impl.main()


if __name__ == "__main__":
    main()
