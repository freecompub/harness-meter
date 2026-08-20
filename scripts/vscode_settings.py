#!/usr/bin/env python3
"""Generate the VS Code proxy settings for harness-meter.

VS Code is configured by adding two keys to its User settings.json. The
automatic setup (`mitmdump -s token_meter.py` or `scripts/proxy_start.py`)
patches your real settings.json in place. This command instead renders just the
fragment, for the cases that path does not cover: a fresh install with no
settings.json yet, a manual merge, or simply inspecting what will be set.

    python scripts/vscode_settings.py                    # print the JSON fragment
    python scripts/vscode_settings.py --port 9001        # non-default port
    python scripts/vscode_settings.py --output frag.json # write a standalone file

The JSON goes to stdout (so it can be redirected cleanly); the candidate
settings.json locations are printed to stderr as guidance.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from harness_meter import config


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.DEFAULT_PORTS["copilot_vscode"],
        help="proxy port (default: 8081)",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="write the fragment to this file instead of stdout",
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite --output if it exists"
    )
    args = parser.parse_args()

    text = config.vscode_settings_json(args.port)

    if args.output is not None:
        if args.output.exists() and not args.force:
            print(
                f"vscode-settings: {args.output} exists; pass --force to overwrite.",
                file=sys.stderr,
            )
            return 1
        args.output.write_text(text, encoding="utf-8")
        print(f"vscode-settings: wrote {args.output}", file=sys.stderr)
        return 0

    sys.stdout.write(text)
    print(
        "\n# Merge these keys into your VS Code User settings.json. Candidates:",
        file=sys.stderr,
    )
    for flavor, path in config._vscode_roots():
        marker = "exists" if path.exists() else "not present"
        print(f"#   {flavor}: {path}  ({marker})", file=sys.stderr)
    print("# Then restart VS Code fully for the proxy to take effect.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
