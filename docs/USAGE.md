# Usage

An end-to-end measurement run, from a clean checkout to a comparable number.
This guide assumes you have already followed [INSTALL.md](INSTALL.md) and that
the mitmproxy CA exists at `~/.mitmproxy/mitmproxy-ca-cert.pem`.

For the experimental method behind the numbers — how to state a hypothesis,
design tasks, and avoid the traps that make a comparison meaningless — see
[PROTOCOL.md](PROTOCOL.md) and [TASKS.md](TASKS.md). This page is the mechanics.

## The shape of a run

```
1. Start the proxy (auto-configures the three clients)
2. Source the CLI snippets; restart VS Code
3. Drive each harness through the same task
4. Stop the proxy (auto-restores the clients)
5. Record outcomes, then analyze
```

## 1. Start the proxy

A single mitmdump process listens on all three ports. Label the run and task
through the environment so the output is attributable:

```bash
MEASURE_RUN=r01 MEASURE_TASK=T04 mitmdump -s token_meter.py \
  --mode regular@8081 \
  --mode regular@8082 \
  --mode regular@8083 \
  --set stream_large_bodies=10m
```

On startup the addon configures all three clients automatically (unless
`MEASURE_AUTOCONFIG=0`). It prints the port map and a note per client. Output is
appended to `./measurements/r01.jsonl`.

`stream_large_bodies=10m` keeps mitmproxy from buffering very large request
bodies into memory — agentic contexts can be large.

## 2. Point each client at its port

**Copilot CLI and Claude Code** — source the generated snippet in the shell
where you will launch each one:

```bash
source .harness-meter/env/copilot_cli.sh      # or .ps1 on Windows
source .harness-meter/env/claude_code.sh
```

**VS Code** — already configured in `settings.json`, but it must be **fully
restarted** (quit and reopen, not a window reload) to re-read the proxy.

See [CONFIGURATION.md](CONFIGURATION.md) for exactly what each snippet exports
and why VS Code is handled differently.

## 3. Drive each harness through the task

Run the identical task in each harness. The measurement is only comparable if
the input is identical, so:

- Issue the **same prompt, verbatim**, in each harness. No follow-ups, no
  hints. (The [task template](TASKS.md) explains why three flat lines.)
- Start a **fresh session** per harness — a new process, not a new chat in an
  existing window.
- Randomize harness order across runs so provider-side load does not correlate
  with harness identity.

Every request the harness makes is recorded as one JSONL row: the client
(inferred from the port), whether it was `agentic` or `inline`, normalized
token counts, `billable_input`, `prompt_bytes`, `system_bytes`, latency, and
the declared model. No prompt content is stored unless `MEASURE_KEEP_BODIES=1`.

## 4. Stop the proxy

Stop mitmdump with **Ctrl-C**. The `done()` hook restores every client to its
pre-measurement state: VS Code `settings.json` is copied back byte-for-byte
from the backup, and the env snippets are deleted.

Then **restart VS Code again** so it drops the proxy setting.

> Environment variables you sourced persist in the shells you opened. Close
> those shells, or unset `HTTP_PROXY` / `HTTPS_PROXY` / `NODE_EXTRA_CA_CERTS`.

If mitmdump was killed with SIGKILL (or the machine lost power), the shutdown
hook never ran and the clients are still proxied. Repair it:

```bash
python scripts/proxy_stop.py
```

It restores from the journalled state in `.harness-meter/state.json`, so it
works even though the process that made the changes is gone.

## 5. Record outcomes and analyze

A token count means nothing without knowing whether the session succeeded. Cost
per attempt would reward a harness that gives up early, so the reported figure
is cost per **successful** session, and you supply the outcomes.

Write `results.csv` by hand (or emit it from CI):

```csv
run,task,client,success
r01,T04,claude_code,1
r01,T04,copilot_cli,1
r01,T04,copilot_vscode,0
```

A session counts as successful only if the target test passes, the full suite
stays green, and no test file was modified — see [PROTOCOL.md](PROTOCOL.md).

Then aggregate:

```bash
python analyze.py --dir measurements --results results.csv
```

Output:

```
task    client               n   succ      median       IQR  turns    sys_B
---------------------------------------------------------------------------
T04     claude_code          4   80%      97,141    22,944      7   11,000
T04     copilot_cli          4   80%     100,628    20,702      8    7,400
T04     copilot_vscode       5  100%     145,721    67,863      8   15,200
```

The reported `median`/`IQR` are **total billable tokens**
(`billable_input + output`) per successful session. When the IQR approaches or
exceeds the median — as on the third row above — the sample is too small to
rank anything, which is the normal state of affairs at n=5.

### analyze.py options

| Flag | Default | Effect |
| --- | --- | --- |
| `--dir` | `measurements` | Directory of `*.jsonl` measurement files |
| `--results` | `results.csv` | Outcome file; if absent, all sessions count as successful (with a warning) |
| `--kind` | `agentic` | Report `agentic` or `inline` traffic — never merged |
| `--include-failures` | off | Include sessions that did not pass the task's gate |

Inline completions (VS Code keystroke-triggered) are isolated from agentic
traffic and reported separately with `--kind inline`. Merging them would charge
VS Code for a workload the CLI harnesses never run.

## Comparing two configurations of one harness

The measurement does not care whether the two sides are different harnesses or
one harness in two configurations. Hold the client fixed, toggle a single
feature — a skill, an MCP server, a plugin, an instruction file — and the same
pipeline reports the marginal token cost of that feature.

The one rule: change **exactly one thing** between the two arms, and label them
so `analyze.py` keeps them apart. Since it groups by `(task, client)`, encode
the configuration in the task id.

Baseline — feature off:

```bash
MEASURE_RUN=r01 MEASURE_TASK=T04-mcp-off mitmdump -s token_meter.py \
  --mode regular@8081 --mode regular@8082 --mode regular@8083 \
  --set stream_large_bodies=10m
# drive Claude Code through T04, then Ctrl-C
```

Then enable the MCP server (or skill, or plugin) and repeat, changing only the
label:

```bash
MEASURE_RUN=r01 MEASURE_TASK=T04-mcp-on mitmdump -s token_meter.py \
  --mode regular@8081 --mode regular@8082 --mode regular@8083 \
  --set stream_large_bodies=10m
# drive the same T04, same prompt, then Ctrl-C
```

Record outcomes for both arms in `results.csv`, then aggregate:

```bash
python analyze.py --dir measurements --results results.csv
```

Output shape (**synthetic figures, for illustration**):

```
task           client               n   succ      median       IQR  turns    sys_B
----------------------------------------------------------------------------------
T04-mcp-off    claude_code          5  100%      96,320    18,110      7    9,800
T04-mcp-on     claude_code          5  100%     121,540    21,300      8   14,600
```

Read it as: the MCP server added ~25k billable tokens per successful session on
this task, and ~4.8k of that is fixed `system_bytes` — the tool manifest paid on
every request, before any task work. If success rate had dropped in the `on`
arm, the extra cost would be buying nothing, which is exactly what the success
gate is there to surface.

The same shape compares a `CLAUDE.md` present vs absent, one MCP server vs two,
or a lean tool set vs a broad one. Keep the change to one variable per pair, or
the difference is no longer attributable.

## Before you trust a result

Four traps turn a number into a *wrong* number. They are covered in the README
under "Before you trust a result" and enforced by the protocol:

- Restart VS Code after setup **and** after teardown.
- Confirm your Copilot license accepts a proxied, self-signed connection.
- Neutralize instruction files (`CLAUDE.md`,
  `.github/copilot-instructions.md`) — identical across harnesses, or absent
  from all.
- "Same model" is declarative on the Copilot side; watch `system_bytes`, which
  often explains most of the gap.

## Privacy

By default only sizes are persisted, never prompt content. `MEASURE_KEEP_BODIES=1`
enables full request capture and should be treated as debug-only: it writes
source code in cleartext to `measurements/`. That directory is gitignored —
keep it that way.
