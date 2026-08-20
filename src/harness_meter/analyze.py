#!/usr/bin/env python3
"""Aggregate harness-meter measurements into a comparable report.

Reads the JSONL files produced by the mitmproxy addon, joins them against an
outcome file, and reports the only figure that means anything: cost per
*successful* task, as a distribution rather than a point estimate.

    python analyze.py --dir measurements --results results.csv

results.csv (you write this by hand or from CI):

    run,task,client,success
    r01,T04,claude_code,1
    r01,T04,copilot_cli,1
    r01,T04,copilot_vscode,0
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import statistics
from collections import defaultdict
from typing import Any

TOKEN_KEYS = ("input", "output", "cache_write", "cache_read")


def load_records(directory: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def load_outcomes(path: pathlib.Path | None) -> dict[tuple[str, str, str], bool]:
    if path is None or not path.exists():
        return {}
    outcomes: dict[tuple[str, str, str], bool] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["run"], row["task"], row["client"])
            outcomes[key] = str(row["success"]).strip() in ("1", "true", "True", "yes")
    return outcomes


def fold_sessions(
    records: list[dict[str, Any]], kind: str
) -> dict[tuple[str, str, str], dict[str, float]]:
    """One session = one client, one task, one run.

    Within a session, distinct HTTP requests are summed: each is a separate
    billing event. (Summing SSE frames *inside* a request would be wrong, but
    the addon already collapses those.)
    """
    sessions: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for record in records:
        if record.get("kind") != kind:
            continue
        if record.get("status") != 200:
            continue
        key = (record["run"], record["task"], record["client"])
        bucket = sessions[key]
        tokens = record.get("tokens") or {}
        for token_key in TOKEN_KEYS:
            bucket[token_key] += tokens.get(token_key, 0)
        bucket["billable_input"] += record.get("billable_input", 0)
        bucket["prompt_bytes"] += record.get("prompt_bytes", 0)
        bucket["turns"] += 1
        bucket["system_bytes"] = max(
            bucket["system_bytes"], record.get("system_bytes", 0)
        )
    return sessions


def iqr(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    quartiles = statistics.quantiles(values, n=4, method="inclusive")
    return quartiles[2] - quartiles[0]


def summarize(
    sessions: dict[tuple[str, str, str], dict[str, float]],
    outcomes: dict[tuple[str, str, str], bool],
    require_success: bool,
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, float]]] = defaultdict(list)
    attempts: dict[tuple[str, str], int] = defaultdict(int)

    for key, bucket in sessions.items():
        run, task, client = key
        attempts[(task, client)] += 1
        if require_success and outcomes and not outcomes.get(key, False):
            continue
        grouped[(task, client)].append(bucket)

    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for group_key, buckets in grouped.items():
        billable = [b["billable_input"] for b in buckets]
        output = [b["output"] for b in buckets]
        total = [b["billable_input"] + b["output"] for b in buckets]
        summary[group_key] = {
            "n": len(buckets),
            "attempts": attempts[group_key],
            "success_rate": len(buckets) / attempts[group_key],
            "median_total": statistics.median(total),
            "iqr_total": iqr(total),
            "median_billable_input": statistics.median(billable),
            "median_output": statistics.median(output),
            "median_turns": statistics.median([b["turns"] for b in buckets]),
            "system_bytes": max(b["system_bytes"] for b in buckets),
        }
    return summary


def render(summary: dict[tuple[str, str], dict[str, Any]]) -> str:
    if not summary:
        return "No sessions matched. Check --dir, --results, and the kind filter.\n"

    header = (
        f"{'task':<8}{'client':<18}{'n':>4}{'succ':>7}"
        f"{'median':>12}{'IQR':>10}{'turns':>7}{'sys_B':>9}"
    )
    lines = [header, "-" * len(header)]
    for (task, client), stats in sorted(summary.items()):
        lines.append(
            f"{task:<8}{client:<18}{stats['n']:>4}"
            f"{stats['success_rate']:>6.0%} "
            f"{stats['median_total']:>11,.0f}"
            f"{stats['iqr_total']:>10,.0f}"
            f"{stats['median_turns']:>7.0f}"
            f"{stats['system_bytes']:>9,.0f}"
        )

    lines.append("")
    lines.append(
        "median/IQR are total billable tokens (billable_input + output) "
        "per successful session."
    )
    lines.append(
        "An IQR near or above the median means the sample is too small to "
        "rank harnesses on this task."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="measurements", type=pathlib.Path)
    parser.add_argument("--results", default="results.csv", type=pathlib.Path)
    parser.add_argument(
        "--kind",
        default="agentic",
        choices=["agentic", "inline"],
        help="inline completions are reported separately, never merged",
    )
    parser.add_argument(
        "--include-failures",
        action="store_true",
        help="include sessions that did not pass the task's gate",
    )
    args = parser.parse_args()

    records = load_records(args.dir)
    outcomes = load_outcomes(args.results)
    if not outcomes and not args.include_failures:
        print("! no results.csv found - reporting all sessions as if successful\n")

    sessions = fold_sessions(records, args.kind)
    summary = summarize(sessions, outcomes, require_success=not args.include_failures)
    print(render(summary))


if __name__ == "__main__":
    main()
