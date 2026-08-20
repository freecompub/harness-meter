# Configuration reference

harness-meter attributes traffic by **listen port**, not by destination host —
VS Code and Copilot CLI both talk to `githubcopilot.com`, so host-based
classification cannot separate them. Configuration therefore comes down to one
rule: **point each client at its own port.**

| Port | Client | Configured through |
| --- | --- | --- |
| 8081 | Copilot in VS Code (`copilot_vscode`) | `settings.json` (`http.proxy`, `http.proxyStrictSSL`) |
| 8082 | Copilot CLI (`copilot_cli`) | environment snippet in `.harness-meter/env/` |
| 8083 | Claude Code (`claude_code`) | environment snippet in `.harness-meter/env/` |

The defaults live in `src/harness_meter/config.py` (`DEFAULT_PORTS`) and in
`token_meter.py` (`PORT_MAP`). Override them with `--ports` when running the
helper scripts (see below); the addon itself always uses 8081–8083.

## Automatic vs. manual configuration

There are two ways to apply this configuration. They do the same thing.

**Automatic (default).** When `mitmdump -s token_meter.py` starts, the addon's
`running()` hook configures all three clients; when it stops — including on
Ctrl-C — the `done()` hook restores them. You do not run anything by hand
except sourcing the two CLI snippets (a child process cannot mutate its
parent's shell). Set `MEASURE_AUTOCONFIG=0` to turn this off entirely.

**Manual.** `scripts/proxy_start.py` and `scripts/proxy_stop.py` apply and
revert the exact same configuration without launching the proxy. Use them for
the two cases automation does not cover: configuring **before** you start
mitmdump, and repairing state **after** a hard kill (SIGKILL, power loss) where
the shutdown hook never ran.

```bash
python scripts/proxy_start.py                 # configure the three clients
python scripts/proxy_start.py --ports 9001,9002,9003   # non-default ports
python scripts/proxy_stop.py                  # restore everything
```

`proxy_start.py` refuses to run if a state file already exists — overwriting it
would replace the pristine `settings.json` backup with an already-proxied one,
making the original unrecoverable. Run `proxy_stop.py` first, or pass `--force`
if you understand the consequence. Both scripts accept `--root PATH` to place
the state directory somewhere other than the current directory.

## Per-client detail

### Copilot in VS Code — port 8081

Configured by editing the VS Code user `settings.json` in place. harness-meter
owns exactly two keys and leaves everything else untouched:

```json
{
  "http.proxy": "http://127.0.0.1:8081",
  "http.proxyStrictSSL": false
}
```

If you would rather add these keys yourself — a fresh VS Code with no
`settings.json` yet, or a manual merge — generate the fragment instead of
patching in place:

```bash
python scripts/vscode_settings.py                    # print the JSON to stdout
python scripts/vscode_settings.py --port 9001        # non-default port
python scripts/vscode_settings.py --output frag.json # write a standalone file
```

The JSON goes to stdout (so `> frag.json` stays clean); the candidate
`settings.json` locations are printed to stderr as guidance. This only renders
the keys — it does not touch your editor — so restart VS Code after you merge
them yourself.

Before editing, a **byte-exact** backup is copied to `.harness-meter/`.
Restoration copies that backup back verbatim — it never re-serializes your
settings — so comments and trailing commas survive intact. Real
`settings.json` files are JSONC (comments, trailing commas), which plain
`json.loads` rejects; harness-meter strips them with a character-by-character
scanner that preserves `//` and `/*` inside string literals, so a URL in one of
your settings is never corrupted.

All three VS Code flavors are configured if present — **Code**, **Code -
Insiders**, and **VSCodium** — because running the stable build is not evidence
the others are absent, and configuring the wrong one silently produces zero VS
Code traffic. The `settings.json` locations searched:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\<flavor>\User\settings.json` |
| macOS | `~/Library/Application Support/<flavor>/User/settings.json` |
| Linux | `$XDG_CONFIG_HOME/<flavor>/User/settings.json` (falls back to `~/.config`) |

> **VS Code must be fully restarted** after configuration and again after
> teardown. Proxy settings are read at process start; a window reload leaves it
> talking to a port that is no longer listening, which looks like a broken
> editor rather than a config change.

VS Code gets **no** environment snippet on purpose. Exporting a proxy in the
shell that launches VS Code would route the editor's extension traffic through
the same port and pollute the agentic totals.

### Copilot CLI — port 8082 · Claude Code — port 8083

Both are Node clients configured through the environment, because a proxy set
in a config file is easy to forget to unset. harness-meter writes one snippet
per client to `.harness-meter/env/`; you `source` the relevant one in the shell
where you will launch that client:

```bash
source .harness-meter/env/copilot_cli.sh      # bash / zsh
source .harness-meter/env/claude_code.sh
```

```powershell
. .\.harness-meter\env\copilot_cli.ps1        # PowerShell (dot-source)
. .\.harness-meter\env\claude_code.ps1
```

Each snippet exports:

| Variable | Value | Why |
| --- | --- | --- |
| `HTTP_PROXY` / `HTTPS_PROXY` (and lowercase) | `http://127.0.0.1:<port>` | Route the client through mitmproxy |
| `NO_PROXY` | `localhost,127.0.0.1,::1` | Keep loopback traffic direct |
| `NODE_EXTRA_CA_CERTS` | `~/.mitmproxy/mitmproxy-ca-cert.pem` | Trust the mitmproxy CA without touching the system store |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | `1` (Claude Code only) | Removes telemetry and update pings from the measured stream |

One file per client keeps the port binding explicit, so a mix-up is visible
rather than silent. The snippets are deleted on teardown, but **variables
already exported into an open shell persist** — close those shells or unset the
variables by hand after a run.

## Measurement environment variables

Set these when launching `mitmdump -s token_meter.py` to label and route the
output:

| Variable | Default | Effect |
| --- | --- | --- |
| `MEASURE_RUN` | current timestamp (`%Y%m%d-%H%M%S`) | Run id; names the output file `<run>.jsonl` |
| `MEASURE_TASK` | `unspecified` | Task id recorded on every row |
| `MEASURE_DIR` | `./measurements` | Directory for the JSONL output |
| `MEASURE_KEEP_BODIES` | unset | `1` persists full request bodies — **debug only**, writes source code in cleartext |
| `MEASURE_AUTOCONFIG` | `1` | `0` disables automatic client configuration on startup/shutdown |

## The state file

Every mutation is journalled to `.harness-meter/state.json` at configuration
time: which `settings.json` files were edited, where their backups live, the
ports in use, and the env snippets written. This is what makes teardown
survivable after a crash — `proxy_stop.py` reads the journal and finishes the
job even when the addon's shutdown hook never ran.

The entire `.harness-meter/` directory is scratch state and is gitignored.
Do not commit it.

## What is deliberately left untouched

- **The system proxy** (WinINET registry, `networksetup`, `gsettings`). A
  machine-wide proxy would capture unrelated traffic and destroy the port-based
  attribution the whole measurement depends on.
- **The system certificate store.** `NODE_EXTRA_CA_CERTS` and
  `proxyStrictSSL: false` make a system CA install unnecessary — and a CA
  install is the one change that leaves durable residue if teardown fails.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "CA not found" note at startup | mitmproxy has never run | Run `mitmdump --version` once, then retry |
| Zero VS Code traffic recorded | Wrong flavor configured, or VS Code not restarted | Restart VS Code fully; confirm the configured flavor matches the one you use |
| `proxy_start.py` exits with "state file already present" | A previous session did not tear down | Run `proxy_stop.py`, or `--force` to overwrite (discards the pristine backup) |
| Copilot rejects the proxied connection | Business/Enterprise plans may block self-signed CAs | Test with a bare `mitmdump` first; see the note in the README |
| Proxy still active after Ctrl-C failed | SIGKILL skipped the `done()` hook | Run `python scripts/proxy_stop.py` — it restores from the journalled state |
