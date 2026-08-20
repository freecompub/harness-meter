#!/usr/bin/env python3
"""Remove the local harness-meter environment.

Restores any still-proxied clients first — so uninstalling never leaves your
editor pointed at a dead port — then deletes the virtualenv and the tool's
scratch state. Measurement output is your data and is left in place unless you
pass --purge.

    python3 scripts/uninstall.py            # revert clients, remove .venv + state
    python3 scripts/uninstall.py --purge    # also delete measurements/ and results.csv
    python3 scripts/uninstall.py --yes      # do not prompt for confirmation

Run it with any Python; reverting the client configuration needs only the
standard library (harness_meter.config imports no third-party package), so it
works even after the venv is gone.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def revert_clients() -> None:
    """Restore VS Code / CLI configuration from the journalled state, if any."""
    state = REPO_ROOT / ".harness-meter" / "state.json"
    if not state.exists():
        print("uninstall: no active proxy state to revert.")
        return
    print("uninstall: reverting client configuration before removing the venv")
    # config imports only the stdlib, so the current interpreter can run the
    # revert with src on the path — no venv required.
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "proxy_stop.py")],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )


def collect_targets(venv_dir: pathlib.Path, purge: bool) -> list[pathlib.Path]:
    targets: list[pathlib.Path] = []
    if venv_dir.exists():
        targets.append(venv_dir)
    state_dir = REPO_ROOT / ".harness-meter"
    if state_dir.exists():
        targets.append(state_dir)
    if purge:
        for path in (REPO_ROOT / "measurements", REPO_ROOT / "results.csv"):
            if path.exists():
                targets.append(path)
    return targets


def confirm(targets: list[pathlib.Path]) -> bool:
    print("uninstall: the following will be removed:")
    for path in targets:
        print(f"  {path}")
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except EOFError:
        print("uninstall: no input available; aborting. Pass --yes to skip the prompt.")
        return False
    return answer in ("y", "yes")


def remove(path: pathlib.Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    print(f"uninstall: removed {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--venv",
        type=pathlib.Path,
        default=REPO_ROOT / ".venv",
        help="virtualenv location (default: .venv at the repo root)",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="also delete measurements/ and results.csv (your data)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not prompt for confirmation"
    )
    args = parser.parse_args()

    revert_clients()

    targets = collect_targets(args.venv, args.purge)
    if not targets:
        print("uninstall: nothing to remove.")
        return 0

    if not args.yes and not confirm(targets):
        print("uninstall: aborted.")
        return 1

    for path in targets:
        remove(path)

    print("uninstall: done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
