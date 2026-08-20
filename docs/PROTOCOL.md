# Experimental protocol

## Hypothesis

State it falsifiably before collecting anything. A usable form:

> On tasks requiring cross-file navigation, harness A consumes a median of at
> least 30% fewer total billable tokens than harness B at equal or higher
> success rate.

"Which tool is more efficient" is not a hypothesis and cannot be refuted.

## Design

Within-subject: every harness runs every task. This removes task difficulty as
a between-group confound, at the cost of requiring a full reset between runs.

- **Repetitions**: n ≥ 5 per (task, harness). Agentic loops are high-variance;
  a single observation is uninterpretable.
- **Order**: randomize harness order within each run. Fixed order lets drift in
  provider-side load correlate with harness identity.
- **Blinding**: not achievable. Record it as a known limitation rather than
  claiming otherwise.

## Preconditions

Verify once, before the first run:

1. Copilot license accepts a proxied connection with the mitmproxy CA.
2. The same model is selectable and *actually selected* in all three clients.
3. Instruction files are identical across harnesses, or absent from all.
4. Ports 8081–8083 are free.
5. The task's test suite is green on a clean checkout.

## Per-run procedure

```bash
git reset --hard <baseline-sha>
git clean -fd
git revert --no-commit <fix-sha>   # keep the test, drop the fix
git checkout <fix-sha> -- <test-path>
```

Then, for each harness in randomized order:

1. Start a fresh session. Not a new chat in an existing window — a new process.
2. Issue the prompt verbatim. No follow-ups, no clarification, no nudging.
3. Stop when the harness declares completion or hits its own limit.
4. Run the full suite. Record the outcome.
5. Reset the working tree before the next harness.

## Success criterion

A session counts as successful only if:

- the target test passes, **and**
- the full suite remains green, **and**
- no test file was modified.

The second and third conditions matter more than they look. Without them, a
harness that satisfies the target test by weakening an assertion elsewhere
scores a win.

Record outcomes in `results.csv`:

```csv
run,task,client,success
r01,T04,claude_code,1
r01,T04,copilot_cli,0
```

## Reporting

Report medians with IQR, per task, before aggregating across tasks. Report
success rate alongside cost — they are not separable, and a cheap harness that
fails half the time is not cheaper.

Report `system_bytes` per harness. If it explains most of the observed gap,
the finding is about scaffolding size, not about harness intelligence, and
should be stated that way.

## Known limitations

Write these down; they are not weaknesses of the method but boundaries of the
claim.

- Model identity on the Copilot side is declarative, not verified.
- Provider-side caching state is not controllable and varies between runs.
- No blinding is possible.
- Results are specific to the codebase under test. Context-acquisition cost
  depends heavily on repository structure and does not transfer.
