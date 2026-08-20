#!/usr/bin/env python3
"""Restore every client to its pre-measurement configuration.

Safe to run at any time, including when nothing is configured and after a
hard kill that skipped the addon's shutdown hook. Restoration of VS Code
settings is byte-exact from the backup taken at setup, so it does not depend
on this tool's own serializer having been faithful.

    python scripts/proxy_stop.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import harness_meter_config as config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=None)
    args = parser.parse_args()

    result = config.revert(root=args.root)
    if result["reverted"]:
        print("\nRestart VS Code for the restored settings to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
