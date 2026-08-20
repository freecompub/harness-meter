# Task design

## The template: revert-and-reimplement

Take a bug-fix commit already merged into the repository under test. Revert the
source change but **keep the test**. The test goes red. Ask each harness to
make it green.

Why this template rather than writing tasks by hand:

- Ground truth is the original diff, written before the experiment existed, so
  there is no author bias toward one harness's style.
- The judge is a test, so success is binary and requires no human rating.
- Difficulty is realistic by construction — it is a bug that actually happened.

## Selection criteria

A commit is a good candidate when:

- **Symptom and cause live in different files.** If a single `grep` on the
  error message lands on the fix, all harnesses converge and the task
  discriminates nothing. Cross-file navigation is the variable of interest.
- **The diff touches 2–4 files, roughly 20–60 lines.** Smaller is noise;
  larger introduces too many partial-credit outcomes.
- **No network, database, or clock dependency.** Environmental variance would
  contaminate the token measurement.
- **An existing unit test fails fast and legibly.** Slow suites inflate wall
  time without adding signal.

Reject any commit whose message or test name gives away the fix location.

## The prompt

Identical across all three harnesses, verbatim:

```
The test `<exact_test_name>` is failing.
Fix the source code so it passes.
Do not modify any test file.
```

Three lines. No file paths, no hints, no "start by looking at". Every piece of
guidance you add shortens the retrieval phase — which is precisely the phase
that separates the harnesses.

## Varying difficulty

Build 6–8 tasks and vary navigation depth deliberately, so the corpus can show
*where* harnesses diverge rather than just *whether* they do:

| Depth | Shape | Expected discrimination |
| --- | --- | --- |
| 1 | Cause in the file named by the stack trace | Low — baseline |
| 2 | Cause one import away | Moderate |
| 3 | Cause in a shared validator or constant | High |
| 3+ | Cause in configuration or a code-generation step | Highest, but flakiest |

Record the depth for each task. If a harness wins only at depth 1, that is a
much narrower claim than "it is more efficient".

## Freezing the corpus

Pin the corpus before the first measurement run and do not edit it afterward.
Adding or dropping tasks once results are visible converts the experiment into
a search for a favorable subset.

Store it as a machine-readable manifest:

```yaml
- id: T04
  fix_sha: a1b2c3d
  baseline_sha: 9f8e7d6
  test: tests/validation/test_glucose_bounds.py::test_upper_bound_rejected
  depth: 3
  files_in_reference_diff: 3
```
