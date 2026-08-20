#!/usr/bin/env python3
"""Check the environment and set up a local virtualenv for harness-meter.

One code path for Windows, macOS and Linux — the same reason the proxy
configuration is Python rather than a pair of shell scripts. Bootstraps a
`.venv` at the repository root and installs the package into it, so a
system Python marked "externally managed" (PEP 668, common with Homebrew)
is never touched.

    python3 scripts/install.py            # create .venv, install with [dev]
    python3 scripts/install.py --test     # ... then run the test suite
    python3 scripts/install.py --no-dev   # runtime deps only, no pytest
    python3 scripts/install.py --recreate # rebuild the venv from scratch

Run it with any Python 3.12+; the virtualenv inherits that interpreter.
"""

from __future__ import annotations

import argparse
import pathlib
import platform
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 12)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _fail(message: str) -> int:
    print(f"install: {message}", file=sys.stderr)
    return 1


def check_python() -> bool:
    """The venv inherits the interpreter running this script."""
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        have = platform.python_version()
        _fail(
            f"Python {need}+ is required; this interpreter is {have}.\n"
            f"  Re-run with a newer one, e.g. `python3.12 scripts/install.py`."
        )
        return False
    print(f"install: Python {platform.python_version()} — ok")
    return True


def venv_python(venv_dir: pathlib.Path) -> pathlib.Path:
    """Path to the venv's interpreter, per platform."""
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def create_venv(venv_dir: pathlib.Path, recreate: bool) -> pathlib.Path:
    if venv_dir.exists() and recreate:
        print(f"install: removing existing {venv_dir}")
        shutil.rmtree(venv_dir)

    python = venv_python(venv_dir)
    if python.exists():
        print(f"install: reusing venv at {venv_dir}")
        return python

    print(f"install: creating venv at {venv_dir}")
    # Use the running interpreter so the venv inherits its version.
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    return python


def pip_install(python: pathlib.Path, dev: bool) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True
    )
    target = ".[dev]" if dev else "."
    print(f"install: pip install -e {target!r}")
    subprocess.run(
        [str(python), "-m", "pip", "install", "-e", target],
        check=True,
        cwd=REPO_ROOT,
    )


def run_tests(python: pathlib.Path) -> int:
    print("install: running the test suite")
    result = subprocess.run([str(python), "-m", "pytest", "-q"], cwd=REPO_ROOT)
    return result.returncode


def report_ca() -> None:
    """The mitmproxy CA is generated on the first proxy start, not by install."""
    ca = pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if ca.exists():
        print(f"install: mitmproxy CA present at {ca}")
    else:
        print(
            "install: mitmproxy CA not found. It is created the first time the "
            "proxy starts —\n"
            "  your first measurement run generates it, then setup can trust it."
        )


def activation_hint(venv_dir: pathlib.Path) -> str:
    rel = (
        venv_dir.relative_to(REPO_ROOT)
        if venv_dir.is_relative_to(REPO_ROOT)
        else venv_dir
    )
    if platform.system() == "Windows":
        return f"{rel}\\Scripts\\Activate.ps1   (PowerShell)"
    return f"source {rel}/bin/activate"


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
        "--no-dev", action="store_true", help="skip the [dev] extra (no pytest)"
    )
    parser.add_argument(
        "--test", action="store_true", help="run the test suite after installing"
    )
    parser.add_argument(
        "--recreate", action="store_true", help="delete and rebuild the venv"
    )
    args = parser.parse_args()

    if not check_python():
        return 1

    try:
        python = create_venv(args.venv, args.recreate)
        pip_install(python, dev=not args.no_dev)
    except subprocess.CalledProcessError as exc:
        return _fail(f"a step failed (exit {exc.returncode}): {exc.cmd}")

    report_ca()

    if args.test:
        if args.no_dev:
            print("install: --test needs the [dev] extra; ignoring --no-dev for pytest")
            try:
                subprocess.run(
                    [str(python), "-m", "pip", "install", "-e", ".[dev]"],
                    check=True,
                    cwd=REPO_ROOT,
                )
            except subprocess.CalledProcessError as exc:
                return _fail(f"could not install test deps (exit {exc.returncode})")
        code = run_tests(python)
        if code != 0:
            return _fail("test suite failed")

    print(
        "\ninstall: done. Activate the environment with:\n"
        f"  {activation_hint(args.venv)}\n"
        "Then follow docs/USAGE.md for a measurement run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
