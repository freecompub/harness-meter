#!/usr/bin/env python3
"""Configure the three clients to route through harness-meter.

Standalone counterpart to the addon's automatic setup, for operators who
prefer to configure before launching mitmdump, or who need to repair state
after a crash.

    python scripts/proxy_start.py
    python scripts/proxy_start.py --ports 9001,9002,9003
"""

from __future__ import annotations

import argparse
import pathlib

from harness_meter import config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ports",
        help="vscode,cli,claude - defaults to 8081,8082,8083",
    )
    parser.add_argument("--root", type=pathlib.Path, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="reconfigure even if a previous session left state behind",
    )
    args = parser.parse_args()

    if config.is_active(args.root) and not args.force:
        print(
            "harness-meter: state file already present.\n"
            "  A previous session did not tear down cleanly.\n"
            "  Run scripts/proxy_stop.py first, or pass --force to overwrite.\n"
            "  Overwriting discards the original backup: the settings.json\n"
            "  you restore later would be the already-proxied one."
        )
        return 1

    ports = dict(config.DEFAULT_PORTS)
    if args.ports:
        values = [int(p) for p in args.ports.split(",")]
        if len(values) != 3:
            parser.error("--ports needs exactly three comma-separated ports")
        ports = dict(zip(config.DEFAULT_PORTS.keys(), values, strict=True))

    config.apply(ports=ports, root=args.root)

    env_dir = config.state_dir(args.root) / "env"
    print("\nBefore launching each client, in its own shell:")
    print(f"  source {env_dir / 'copilot_cli.sh'}      # or .ps1 on Windows")
    print(f"  source {env_dir / 'claude_code.sh'}")
    print("VS Code is already configured, but must be restarted completely.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
