# harness-meter

Measure and compare token consumption across AI coding agent harnesses —
GitHub Copilot in VS Code, Copilot CLI, and Claude Code — on the same model
and the same task.

The point is not to count tokens. Every one of these tools already reports its
own numbers. The point is that **those numbers are not comparable**: they are
produced by different instrumentation, use different definitions of "prompt
tokens", and mix task work with background traffic. harness-meter puts a single
parser in front of all three so the comparison has one unit.

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

Requires Python 3.12+ and mitmproxy 10+.

## Run

```bash
MEASURE_RUN=r01 MEASURE_TASK=T04 mitmdump -s token_meter.py \
  --mode regular@8081 \
  --mode regular@8082 \
  --mode regular@8083 \
  --set stream_large_bodies=10m
```

| Port | Client | Configuration |
| --- | --- | --- |
| 8081 | VS Code | `"http.proxy": "http://127.0.0.1:8081"`, `"http.proxyStrictSSL": false`, then restart VS Code fully |
| 8082 | Copilot CLI | `HTTPS_PROXY=http://127.0.0.1:8082` |
| 8083 | Claude Code | `HTTPS_PROXY=http://127.0.0.1:8083` |

Node-based clients also need the CA, generated on first launch:

```bash
export NODE_EXTRA_CA_CERTS=$HOME/.mitmproxy/mitmproxy-ca-cert.pem
```

## Analyze

Record outcomes in `results.csv`, then:

```bash
python analyze.py --dir measurements --results results.csv
```

```
task    client               n   succ      median       IQR  turns    sys_B
---------------------------------------------------------------------------
T04     claude_code          4   80%      97,141    22,944      7   11,000
T04     copilot_cli          4   80%     100,628    20,702      8    7,400
T04     copilot_vscode       5  100%     145,721    67,863      8   15,200
```

The reported figure is total billable tokens **per successful session**. Cost
per attempt rewards a harness that gives up early, so failures are excluded by
default (`--include-failures` to override).

## Before you trust a result

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

## License

MIT
