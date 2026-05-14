#!/usr/bin/env python3
"""Run the local NOUZ release verification contract."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIST = Path(".build-tmp") / "release-check" / "dist"

QUICK_COMMANDS = [
    ["python", "-m", "compileall", "-q", "nouz_mcp", "pytest_smoke.py", "scripts"],
    ["python", "-m", "pytest", "-q"],
    ["python", "test_server.py"],
]

FULL_COMMANDS = [
    ["python", "-m", "build", "--outdir", str(BUILD_DIST)],
    ["python", "-m", "twine", "check", str(BUILD_DIST / "*")],
]


def command_plan(*, full: bool) -> list[list[str]]:
    return QUICK_COMMANDS + (FULL_COMMANDS if full else [])


def _actual_command(command: list[str]) -> list[str]:
    actual = [sys.executable if part == "python" else part for part in command]
    if actual[:3] == [sys.executable, "-m", "twine"] and actual[-1].endswith("*"):
        pattern = actual.pop()
        artifacts = sorted(str(path) for path in ROOT.glob(pattern))
        actual.extend(artifacts)
    return actual


def run_command(command: list[str]) -> None:
    print(f"\n$ {' '.join(command)}", flush=True)
    actual = _actual_command(command)
    subprocess.run(actual, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also build distributions and run twine check.")
    parser.add_argument("--list", action="store_true", help="Print the command plan as JSON without running it.")
    args = parser.parse_args(argv)

    commands = command_plan(full=args.full)
    if args.list:
        print(json.dumps(commands, ensure_ascii=False, indent=2))
        return 0

    for command in commands:
        run_command(command)

    print("\nRelease check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
