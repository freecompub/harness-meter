"""
token_meter.py — Addon mitmproxy unique, trois clients.

Compare Copilot/VS Code, Copilot CLI et Claude Code sur le même modèle et la
même tâche. Un seul process, un seul parseur : aucun biais d'instrumentation.

Discrimination par port d'écoute plutôt que par host, car VS Code et Copilot
CLI partagent la même destination.

Lancement (un seul process, trois listeners) :

    MEASURE_RUN=r01 MEASURE_TASK=T04 mitmdump -s token_meter.py \
      --mode regular@8081 \
      --mode regular@8082 \
      --mode regular@8083 \
      --set stream_large_bodies=10m

Puis chaque client sur SON port :
    8081 -> VS Code        ("http.proxy": "http://127.0.0.1:8081")
    8082 -> Copilot CLI    (HTTPS_PROXY=http://127.0.0.1:8082)
    8083 -> Claude Code    (HTTPS_PROXY=http://127.0.0.1:8083)

CA pour les clients Node :
    export NODE_EXTRA_CA_CERTS=$HOME/.mitmproxy/mitmproxy-ca-cert.pem
VS Code en plus : "http.proxyStrictSSL": false

Sortie : ./measurements/<run>.jsonl
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any

try:
    from mitmproxy import http
except ImportError:  # pragma: no cover
    # The parsing core is deliberately importable without mitmproxy so it can
    # be unit-tested and reused. Only `response()` needs the real dependency.
    http = None  # type: ignore[assignment]

PORT_MAP = {
    8081: "copilot_vscode",
    8082: "copilot_cli",
    8083: "claude_code",
}

RUN = os.environ.get("MEASURE_RUN", time.strftime("%Y%m%d-%H%M%S"))
TASK = os.environ.get("MEASURE_TASK", "unspecified")
OUTDIR = pathlib.Path(os.environ.get("MEASURE_DIR", "./measurements"))
OUTDIR.mkdir(parents=True, exist_ok=True)
OUTFILE = OUTDIR / f"{RUN}.jsonl"

# Aucun contenu de prompt persisté par défaut. MEASURE_KEEP_BODIES=1 pour debug.
KEEP_BODIES = os.environ.get("MEASURE_KEEP_BODIES") == "1"

# Multiplicateurs de facturation Anthropic. Permettent de réduire un usage
# ventilé (frais / write / read) à un scalaire unique comparable entre clients
# dont les politiques de cache diffèrent.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def client_of(flow: http.HTTPFlow) -> str | None:
    try:
        port = flow.client_conn.sockname[1]
    except (AttributeError, IndexError, TypeError):
        return None
    return PORT_MAP.get(port)


def kind_of(host: str, path: str) -> str:
    """Sépare le trafic agentique du bruit de complétion inline.

    Critique : VS Code émet des complétions inline en continu, déclenchées par
    la frappe et sans rapport avec la tâche. Les agréger au total agentique
    rend le comparatif faux.
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
    """Normalise les schémas Anthropic et OpenAI vers un vocabulaire unique."""
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
        # OpenAI inclut le cache dans prompt_tokens ; Anthropic l'exclut
        # d'input_tokens. On soustrait pour aligner les deux définitions.
        out["input"] = max(usage.get("prompt_tokens", 0) - cached, 0)
        out["output"] = usage.get("completion_tokens", 0)
        out["cache_write"] = 0
        out["cache_read"] = cached
    return out


def merge(acc: dict[str, int], new: dict[str, int]) -> None:
    """Les frames SSE republient des cumuls, pas des deltas : max, pas somme."""
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
    """Scalaire unique, neutralise les politiques de cache divergentes."""
    return (
        usage.get("input", 0)
        + usage.get("cache_write", 0) * CACHE_WRITE_MULT
        + usage.get("cache_read", 0) * CACHE_READ_MULT
    )


def _system_bytes(body: dict[str, Any]) -> int:
    """Taille du prompt système : mesure directe du scaffolding du harness.

    Claude Code utilise un champ `system` dédié ; Copilot place le système
    dans messages[0]. On couvre les deux.
    """
    system = body.get("system")
    if system:
        return prompt_bytes({"system": system})
    messages = body.get("messages") or []
    if messages and isinstance(messages[0], dict):
        if messages[0].get("role") == "system":
            return len(str(messages[0].get("content", "")).encode("utf-8"))
    return 0


def response(flow: http.HTTPFlow) -> None:
    client = client_of(flow)
    if client is None:
        return

    kind = kind_of(flow.request.pretty_host, flow.request.path)
    if kind == "other":
        return

    try:
        req_body = json.loads(flow.request.get_text() or "{}")
    except json.JSONDecodeError:
        req_body = {}

    raw = flow.response.get_text(strict=False) or ""
    if raw.lstrip().startswith("data:"):
        usage = parse_stream(raw)
    else:
        try:
            usage = extract_usage(json.loads(raw))
        except json.JSONDecodeError:
            usage = {}

    record = {
        "ts": time.time(),
        "run": RUN,
        "task": TASK,
        "client": client,
        "kind": kind,
        # Alias annoncé par le client. Chez Copilot c'est un alias de routage,
        # pas un snapshot vérifiable : à traiter comme déclaratif.
        "model_declared": req_body.get("model"),
        "status": flow.response.status_code,
        "latency_ms": int(
            (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
        ),
        "tokens": usage,
        "billable_input": round(billable_input(usage), 1),
        "prompt_bytes": prompt_bytes(req_body),
        "response_bytes": len(raw.encode("utf-8")),
        "n_messages": len(req_body.get("messages") or []),
        "n_tools": len(req_body.get("tools") or []),
        "system_bytes": _system_bytes(req_body),
    }

    if KEEP_BODIES:
        record["_request"] = req_body

    with OUTFILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
