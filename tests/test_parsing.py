"""Tests for the usage parsers.

These guard the two mistakes that silently corrupt a comparison: summing
cumulative SSE frames, and treating the OpenAI and Anthropic definitions of
"prompt tokens" as if they meant the same thing.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import token_meter as tm  # noqa: E402


ANTHROPIC_STREAM = """\
event: message_start
data: {"type":"message_start","message":{"usage":{"input_tokens":1200,\
"cache_creation_input_tokens":400,"cache_read_input_tokens":8000,\
"output_tokens":1}}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"text":"hello"}}

event: message_delta
data: {"type":"message_delta","usage":{"output_tokens":350}}

data: [DONE]
"""

OPENAI_STREAM = """\
data: {"choices":[{"delta":{"content":"hi"}}]}

data: {"choices":[],"usage":{"prompt_tokens":9200,"completion_tokens":350,\
"total_tokens":9550,"prompt_tokens_details":{"cached_tokens":8000}}}

data: [DONE]
"""


def test_anthropic_stream_keeps_cache_split():
    usage = tm.parse_stream(ANTHROPIC_STREAM)
    assert usage["input"] == 1200
    assert usage["cache_write"] == 400
    assert usage["cache_read"] == 8000
    assert usage["output"] == 350


def test_cumulative_frames_are_not_summed():
    """message_start reports output_tokens=1, message_delta reports 350.

    Summing gives 351. The correct answer is 350.
    """
    assert tm.parse_stream(ANTHROPIC_STREAM)["output"] == 350


def test_openai_prompt_tokens_are_cache_exclusive_after_parse():
    """OpenAI folds cached tokens into prompt_tokens; Anthropic does not.

    Both streams above describe the same workload. Parsed, they must agree.
    """
    usage = tm.parse_stream(OPENAI_STREAM)
    assert usage["input"] == 1200
    assert usage["cache_read"] == 8000
    assert usage["output"] == 350


def test_both_schemas_yield_identical_billable_input():
    anthropic = tm.parse_stream(ANTHROPIC_STREAM)
    openai = tm.parse_stream(OPENAI_STREAM)
    # Anthropic writes 400 tokens to cache here, OpenAI reports no write.
    assert tm.billable_input(anthropic) == 1200 + 400 * 1.25 + 8000 * 0.10
    assert tm.billable_input(openai) == 1200 + 8000 * 0.10


def test_inline_traffic_is_classified_apart():
    assert tm.kind_of("api.githubcopilot.com", "/chat/completions") == "agentic"
    assert tm.kind_of("api.anthropic.com", "/v1/messages") == "agentic"
    assert (
        tm.kind_of("proxy.individual.githubcopilot.com", "/v1/engines/x/completions")
        == "inline"
    )


def test_prompt_bytes_ignores_sampling_parameters():
    """Only context counts. Sampling knobs must not inflate the byte metric."""
    body = {
        "model": "a-very-long-model-identifier",
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "abcde"}],
    }
    assert tm.prompt_bytes(body) == len("user") + len("abcde")


def test_malformed_frames_are_skipped_not_fatal():
    stream = 'data: {"broken\n\ndata: {"usage":{"output_tokens":7}}\n'
    assert tm.parse_stream(stream)["output"] == 7


def test_system_bytes_found_in_either_position():
    anthropic_style = {"system": "abc", "messages": []}
    openai_style = {"messages": [{"role": "system", "content": "abcd"}]}
    assert tm._system_bytes(anthropic_style) == 3
    assert tm._system_bytes(openai_style) == 4
