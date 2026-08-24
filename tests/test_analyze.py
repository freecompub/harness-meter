"""Tests for the aggregation report.

These guard the reported figures: the cache-weighted billable headline, and the
raw-token / cache columns that make a harness's cache reliance visible.
"""

from __future__ import annotations

import statistics

from harness_meter import analyze


def _record(run: str, tokens: dict[str, int], billable: float) -> dict:
    return {
        "run": run,
        "task": "T",
        "client": "claude_code",
        "kind": "agentic",
        "status": 200,
        "tokens": tokens,
        "billable_input": billable,
        "prompt_bytes": 0,
        "system_bytes": 1000,
    }


def _summ(records: list[dict]) -> dict:
    sessions = analyze.fold_sessions(records, "agentic")
    summary = analyze.summarize(sessions, outcomes={}, require_success=False)
    return summary[("T", "claude_code")]


def test_summary_reports_raw_tokens_and_cache_split():
    records = [
        _record(
            "r1",
            {"input": 4092, "output": 1043, "cache_write": 1200, "cache_read": 26112},
            6703.2,
        ),
        _record(
            "r2",
            {"input": 4500, "output": 980, "cache_write": 800, "cache_read": 24000},
            6400.0,
        ),
    ]
    stats = _summ(records)
    raw = [4092 + 1043 + 1200 + 26112, 4500 + 980 + 800 + 24000]
    assert stats["median_tokens"] == statistics.median(raw)
    assert stats["median_cache_read"] == statistics.median([26112, 24000])
    assert stats["median_cache_write"] == statistics.median([1200, 800])


def test_raw_tokens_exceed_billable_when_cache_is_reused():
    """The point of the columns: heavy cache reuse makes raw tokens dwarf the
    cheap billable figure, and that gap must be visible.
    """
    records = [
        _record(
            "r1",
            {"input": 1000, "output": 500, "cache_write": 0, "cache_read": 50000},
            6000.0,
        ),
    ]
    stats = _summ(records)
    assert stats["median_tokens"] == 51500
    assert stats["median_cache_read"] == 50000
    assert stats["median_total"] < stats["median_tokens"]


def test_only_the_requested_kind_is_counted():
    records = [
        _record(
            "r1", {"input": 100, "output": 10, "cache_write": 0, "cache_read": 0}, 100.0
        ),
        {
            **_record(
                "r2",
                {"input": 999, "output": 0, "cache_write": 0, "cache_read": 0},
                999.0,
            ),
            "kind": "inline",
        },
    ]
    stats = _summ(records)
    assert stats["n"] == 1
    assert stats["median_tokens"] == 110
