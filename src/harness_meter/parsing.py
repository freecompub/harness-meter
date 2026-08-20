"""Pure token-usage parsing core.

Deliberately free of any mitmproxy dependency so it can be unit-tested and
reused without a proxy. The addon (`token_meter.py`) imports these functions;
the tests import them directly.

Two corrections live here, and they are the whole point of the tool:

- Cumulative SSE frames are collapsed with `max`, not summed — streaming
  responses republish running totals, so adding them inflates the count.
- The OpenAI and Anthropic `usage` schemas are reconciled to one vocabulary.
  OpenAI folds cached tokens into `prompt_tokens`; Anthropic excludes them from
  `input_tokens`. `extract_usage` subtracts the cached tokens so both describe
  the same quantity.
"""

from __future__ import annotations

import json
from typing import Any

# Anthropic billing multipliers. Reduce a broken-out usage (fresh / write /
# read) to a single scalar comparable across clients whose cache policies
# differ.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def kind_of(host: str, path: str) -> str:
    """Separate agentic traffic from inline-completion noise.

    Critical: VS Code emits inline completions continuously, triggered by
    keystrokes and unrelated to the task. Folding them into the agentic total
    makes the comparison wrong.
    """
    if "chat/completions" in path or "/v1/messages" in path:
        return "agentic"
    if "completions" in path or "/copilot_internal/" in path:
        return "inline"
    return "other"


def prompt_bytes(body: dict[str, Any]) -> int:
    total = 0

    def walk(node: Any) -> None:
        nonlocal total
        if isinstance(node, str):
            total += len(node.encode("utf-8"))
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("model", "stream", "temperature", "max_tokens"):
                    continue
                walk(value)

    for field in ("system", "messages", "tools", "tool_choice"):
        if field in body:
            walk(body[field])
    return total


def extract_usage(payload: dict[str, Any]) -> dict[str, int]:
    """Normalize the Anthropic and OpenAI schemas to one vocabulary."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}

    out: dict[str, int] = {}
    if "input_tokens" in usage or "output_tokens" in usage:
        out["input"] = usage.get("input_tokens", 0)
        out["output"] = usage.get("output_tokens", 0)
        out["cache_write"] = usage.get("cache_creation_input_tokens", 0)
        out["cache_read"] = usage.get("cache_read_input_tokens", 0)
    elif "prompt_tokens" in usage or "completion_tokens" in usage:
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0)
        # OpenAI includes the cache in prompt_tokens; Anthropic excludes it from
        # input_tokens. Subtract to align the two definitions.
        out["input"] = max(usage.get("prompt_tokens", 0) - cached, 0)
        out["output"] = usage.get("completion_tokens", 0)
        out["cache_write"] = 0
        out["cache_read"] = cached
    return out


def merge(acc: dict[str, int], new: dict[str, int]) -> None:
    """SSE frames republish running totals, not deltas: max, not sum."""
    for key, value in new.items():
        if value:
            acc[key] = max(acc.get(key, 0), value)


def parse_stream(text: str) -> dict[str, int]:
    acc: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        merge(acc, extract_usage(payload))
        if isinstance(payload.get("message"), dict):
            merge(acc, extract_usage(payload["message"]))
    return acc


def billable_input(usage: dict[str, int]) -> float:
    """Single scalar, neutralizing divergent cache policies."""
    return (
        usage.get("input", 0)
        + usage.get("cache_write", 0) * CACHE_WRITE_MULT
        + usage.get("cache_read", 0) * CACHE_READ_MULT
    )


def _system_bytes(body: dict[str, Any]) -> int:
    """Size of the system prompt: a direct measure of the harness scaffolding.

    Claude Code uses a dedicated `system` field; Copilot puts the system prompt
    in messages[0]. Cover both.
    """
    system = body.get("system")
    if system:
        return prompt_bytes({"system": system})
    messages = body.get("messages") or []
    first = messages[0] if messages else None
    if isinstance(first, dict) and first.get("role") == "system":
        return len(str(first.get("content", "")).encode("utf-8"))
    return 0
