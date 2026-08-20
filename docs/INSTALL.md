# Installation

harness-meter is a single mitmproxy addon plus two helper scripts. There is
nothing to compile and no service to run — installation is a Python package
install and a one-time certificate step.

## Requirements

| Requirement | Version | Notes |
| --- | --- | --- |
| Python | 3.12+ | Uses `zip(strict=...)` and PEP 604 unions; older versions will not run |
| mitmproxy | 10+ | Pulled in automatically as a dependency |
| pytest | 8+ | Only for running the test suite (`[dev]` extra) |

Windows, macOS and Linux run the **same code path**. There are no shell
scripts to keep in sync across platforms — the proxy configuration is written
in Python precisely so a fix lands once for all three operating systems.

## Quick install (recommended)

Clone the repository and run the installer. It checks your Python version,
creates a local `.venv` at the repo root, installs the package into it, and
reports whether the mitmproxy CA is present:

```bash
git clone https://github.com/freecompub/harness-meter.git
cd harness-meter
python3 scripts/install.py --test
```

| Flag | Effect |
| --- | --- |
| _(none)_ | Create `.venv`, install with the `[dev]` extra |
| `--test` | Also run the test suite after installing |
| `--no-dev` | Runtime dependencies only, no pytest |
| `--recreate` | Delete and rebuild the venv from scratch |
| `--venv PATH` | Put the venv somewhere other than `.venv` |

The installer deliberately works inside a virtualenv, so a system Python marked
**externally managed** (PEP 668 — the `error: externally-managed-environment`
you get from a Homebrew Python) is never touched. Run it with any Python 3.12+;
the venv inherits that interpreter.

Activate the environment afterward:

```bash
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\Activate.ps1       # Windows (PowerShell)
```

## Manual install

If you prefer to set it up yourself, the installer does nothing you cannot do by
hand. Always use a virtualenv so mitmproxy's dependency tree does not land in
your system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate         # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"           # or `pip install -e .` for runtime only
```

## Verify the checkout

```bash
pytest
```

All tests should pass without mitmproxy running and without any client
installed — the parsing core is deliberately importable without the proxy so
it can be unit-tested in isolation. A green suite confirms the two corrections
that silently corrupt a comparison (collapsing cumulative SSE frames, and
reconciling the OpenAI/Anthropic `usage` schemas) behave as specified.

## Generate the mitmproxy CA

The Node-based clients (Copilot CLI, Claude Code) and VS Code all need to trust
mitmproxy's certificate authority. harness-meter never installs a CA into the
**system** trust store — it points each client at the CA file directly
(`NODE_EXTRA_CA_CERTS` for the Node clients, `http.proxyStrictSSL: false` for
VS Code). This leaves no durable residue if teardown ever fails.

The CA is created the first time mitmproxy **starts its proxy** — that is, your
first measurement run generates it. `scripts/install.py` reports whether the CA
already exists but does not create one, because generating it means starting the
proxy.

The file lands at `~/.mitmproxy/mitmproxy-ca-cert.pem` on every platform. The
automatic setup and the helper scripts both read it from there; if it is
missing they print a note telling you to run a measurement once and retry.

## Project layout

The importable package lives under `src/` — a `src` layout, so the editable
install exposes exactly the package and nothing else (no accidental imports of
the repo root or the test directory):

```
src/harness_meter/
  parsing.py    # pure token-usage core — no mitmproxy dependency, unit-tested
  config.py     # cross-platform client configuration (proxy setup/teardown)
  analyze.py    # aggregation into a comparable report; main() entry point
token_meter.py  # the mitmproxy addon, loaded by path: mitmdump -s token_meter.py
analyze.py      # thin wrapper for `python analyze.py`
scripts/        # install.py, uninstall.py, proxy_start.py, proxy_stop.py,
                # vscode_settings.py
```

The two root files are operational entry points run from a checkout, not part of
the installed package. `harness-meter-analyze` (a console script) and
`python -m harness_meter.analyze` are equivalent to `python analyze.py`.

## Uninstall

To remove the local environment, run the uninstaller. It **restores any
still-proxied clients first** — so it never leaves VS Code or a CLI pointed at a
dead port — then deletes the venv and the tool's scratch state
(`.harness-meter/`):

```bash
python3 scripts/uninstall.py            # revert clients, remove .venv + state
python3 scripts/uninstall.py --purge    # also delete measurements/ and results.csv
python3 scripts/uninstall.py --yes      # skip the confirmation prompt
```

Your measurement output (`measurements/`, `results.csv`) is treated as data and
kept unless you pass `--purge`. Reverting the client configuration needs only
the standard library, so the uninstaller works even after the venv is gone.

## Next steps

- [CONFIGURATION.md](CONFIGURATION.md) — what each client needs, every
  environment variable, and the state file
- [USAGE.md](USAGE.md) — an end-to-end measurement run, start to finish
