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

## Install the package

Clone the repository and install it in editable mode. The `[dev]` extra adds
pytest so you can verify the checkout:

```bash
git clone https://github.com/freecompub/harness-meter.git
cd harness-meter
pip install -e ".[dev]"
```

If you only want to run measurements and not the tests, the base install is
enough:

```bash
pip install -e .
```

A virtual environment is recommended so mitmproxy's dependency tree does not
land in your system Python:

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
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

The CA is created the first time mitmproxy starts. If you have never run
mitmproxy on this machine, generate it once:

```bash
mitmdump --version   # or run a full measurement; either creates the CA
```

The file lands at `~/.mitmproxy/mitmproxy-ca-cert.pem` on every platform. The
automatic setup and the helper scripts both read it from there; if it is
missing they print a note telling you to run mitmdump once and retry.

## Next steps

- [CONFIGURATION.md](CONFIGURATION.md) — what each client needs, every
  environment variable, and the state file
- [USAGE.md](USAGE.md) — an end-to-end measurement run, start to finish
