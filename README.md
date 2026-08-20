# harness-meter

[![CI](https://github.com/freecompub/harness-meter/actions/workflows/ci.yml/badge.svg)](https://github.com/freecompub/harness-meter/actions/workflows/ci.yml)

Measure and compare token consumption across AI coding agent harnesses —
GitHub Copilot in VS Code, Copilot CLI, and Claude Code — on the same model
and the same task.

The point is not to count tokens. Every one of these tools already reports its
own numbers. The point is that **those numbers are not comparable**: they are
produced by different instrumentation, use different definitions of "prompt
tokens", and mix task work with background traffic. harness-meter puts a single
parser in front of all three so the comparison has one unit.

## Documentation

| Guide | What it covers |
| --- | --- |
| [docs/INSTALL.md](docs/INSTALL.md) | Requirements, package install, generating the mitmproxy CA |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Ports, every environment variable, per-client setup, the state file, troubleshooting |
| [docs/USAGE.md](docs/USAGE.md) | A measurement run end to end: start, drive, stop, analyze |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | The experimental method — hypothesis, design, success criteria |
| [docs/TASKS.md](docs/TASKS.md) | How to build a discriminating task corpus |

The rest of this README is a quickstart; the guides above are the reference.

## What it measures

A single mitmproxy process listens on three ports, one per client. Traffic is
attributed by listen port, not by destination host — VS Code and Copilot CLI
both talk to `githubcopilot.com`, so host-based classification cannot separate
them.

For every request it records:

| Field | Why it exists |
| --- | --- |
| `tokens.{input,output,cache_write,cache_read}` | Provider counters, normalized to one vocabulary |
| `billable_input` | Cache-weighted scalar (write ×1.25, read ×0.10) |
| `prompt_bytes` | Tokenizer-independent context size |
| `system_bytes` | Size of the harness's own scaffolding |
| `kind` | `agentic` vs `inline` — never merged |
| `n_turns`, `latency_ms` | Loop depth and wall cost |

## Three corrections it applies

These are the differences between a number and a *comparable* number.

**Cumulative SSE frames are collapsed, not summed.** Streaming responses
republish running totals. Adding them up inflates the count. The addon takes
the maximum per key within a request, and sums only across distinct requests.

**The two `usage` schemas are reconciled.** OpenAI includes cached tokens
inside `prompt_tokens`; Anthropic excludes them from `input_tokens`. Compared
raw, the same workload looks larger on one side. The parser subtracts cached
tokens so both describe the same quantity. `tests/test_parsing.py` asserts this
against a matched pair of fixtures.

**Inline completions are isolated.** VS Code emits keystroke-triggered
completions unrelated to the task. Copilot CLI and Claude Code do not. Merging
them charges VS Code for a workload the others never ran.

## Install

```bash
pip install -e ".[dev]"
pytest
```

Requires Python 3.12+ and mitmproxy 10+. Windows, macOS and Linux are
supported by the same code path; there are no shell scripts to keep in sync.

## Run

Client configuration is applied automatically when mitmdump starts and
reverted when it stops:

```bash
MEASURE_RUN=r01 MEASURE_TASK=T04 mitmdump -s token_meter.py \
  --mode regular@8081 \
  --mode regular@8082 \
  --mode regular@8083 \
  --set stream_large_bodies=10m
```

| Port | Client | How it is configured |
| --- | --- | --- |
| 8081 | VS Code | `settings.json` patched in place (backed up first) |
| 8082 | Copilot CLI | env snippet in `.harness-meter/env/` |
| 8083 | Claude Code | env snippet in `.harness-meter/env/` |

A child process cannot mutate its parent's environment, so the two CLI clients
get snippet files to source in their own shell:

```bash
source .harness-meter/env/copilot_cli.sh     # or . .\copilot_cli.ps1 on Windows
source .harness-meter/env/claude_code.sh
```

VS Code needs no snippet — exporting a proxy in its shell too would route
extension traffic through the same port and pollute the agentic totals. It does
need a **full restart**; a window reload does not re-read proxy settings.

### Manual control

Automatic setup covers the normal path. The scripts exist for the two cases it
does not: configuring before launching mitmdump, and repairing state after a
hard kill.

```bash
python scripts/proxy_start.py      # configure
python scripts/proxy_stop.py       # restore
```

`MEASURE_AUTOCONFIG=0` disables the automatic hooks entirely.

### What gets touched, and what does not

Only the three target clients are configured. Deliberately untouched:

- **The system proxy** (WinINET registry, `networksetup`, `gsettings`). It would
  capture unrelated traffic and destroy the port-based attribution the whole
  measurement depends on.
- **The system certificate store.** `NODE_EXTRA_CA_CERTS` covers the Node
  clients and `proxyStrictSSL: false` covers VS Code, so no CA is installed and
  none can be left behind.

Teardown restores `settings.json` from a byte-exact copy taken at setup, not by
re-serializing — comments and formatting survive intact. Every change is
journalled to `.harness-meter/state.json`, so `proxy_stop.py` can finish the job
after a SIGKILL, when the shutdown hook never ran.

## Analyze

Record outcomes in `results.csv`, then:

```bash
python analyze.py --dir measurements --results results.csv
```

Output shape (**synthetic figures — this project ships no measurements**):

```
task    client               n   succ      median       IQR  turns    sys_B
---------------------------------------------------------------------------
T04     claude_code          4   80%      97,141    22,944      7   11,000
T04     copilot_cli          4   80%     100,628    20,702      8    7,400
T04     copilot_vscode       5  100%     145,721    67,863      8   15,200
```

Note the IQR on the third row: roughly half its median. On that sample the
three harnesses cannot be ranked, and reporting the medians as a result would
be wrong. This is the normal state of affairs at n=5 — plan for more.

The reported figure is total billable tokens **per successful session**. Cost
per attempt rewards a harness that gives up early, so failures are excluded by
default (`--include-failures` to override).

## Before you trust a result

**Restart VS Code after setup, and again after teardown.** Proxy settings are
read at process start. A window reload leaves it talking to a port that is no
longer listening, which looks like a broken editor rather than a config issue.

**Check your Copilot license first.** Business and Enterprise plans may reject
proxied connections with self-signed certificates. Test with a bare mitmproxy
before building a protocol on top of this.

**Neutralize the instruction files.** `CLAUDE.md` and
`.github/copilot-instructions.md` are not read by the same tools. If one exists
and the other does not, you are measuring instruction files, not harnesses.
Either make them identical or remove both.

**"Same model" is declarative on the Copilot side.** The identifier you select
is a routing alias, not a verifiable snapshot, and GitHub injects its own system
prompt regardless. `system_bytes` exists to make that visible — it is often the
variable that explains most of the gap.

**One task proves nothing.** Inter-task variance exceeds inter-tool variance.
Build 6–8 tasks on the same template and compare per-task distributions before
aggregating. If the IQR approaches the median, the sample is too small to rank
anything.

See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the full experimental procedure
and [`docs/TASKS.md`](docs/TASKS.md) for the task design template.

## Privacy

No prompt content is persisted by default — only sizes. `MEASURE_KEEP_BODIES=1`
enables full request capture, and should be treated as a debug-only mode: it
writes source code in cleartext to `measurements/`.

## Status

Early. The measurement pipeline is tested end to end, but **no real comparison
has been published with it yet** — every number in this README is synthetic,
generated to validate the aggregation code. Treat the tool as instrumentation,
not as a source of findings.

## License

MIT
