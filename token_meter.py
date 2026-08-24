"""token_meter.py — single mitmproxy addon, three clients.

Compares Copilot/VS Code, Copilot CLI and Claude Code on the same model and the
same task. One process, one parser: no instrumentation bias.

The parsing core lives in `harness_meter.parsing` (importable without mitmproxy,
so it is unit-tested in isolation); this file is the thin addon around it —
traffic attribution, per-request I/O, and the setup/teardown lifecycle.

Attribution is by listen port, not by host, because VS Code and Copilot CLI
share the same destination.

Both transports are measured. Most traffic is HTTP; Copilot CLI streams its
agentic turns over a WebSocket (the `/responses` endpoint), whose token usage
the HTTP hook never sees. Both paths route their `usage` through the same
`harness_meter.parsing.extract_usage`, so the numbers stay comparable.

Launch (one process, three listeners):

    MEASURE_RUN=r01 MEASURE_TASK=T04 mitmdump -s token_meter.py \
      --mode regular@8081 \
      --mode regular@8082 \
      --mode regular@8083 \
      --set stream_large_bodies=10m

Then each client on ITS port:
    8081 -> VS Code        ("http.proxy": "http://127.0.0.1:8081")
    8082 -> Copilot CLI    (HTTPS_PROXY=http://127.0.0.1:8082)
    8083 -> Claude Code    (HTTPS_PROXY=http://127.0.0.1:8083)

Output: ./measurements/<run>.jsonl
"""

from __future__ import annotations

import json
import os
import pathlib
import time

from harness_meter import config, parsing

try:
    from mitmproxy import http
except ImportError:  # pragma: no cover
    # Annotations are strings (PEP 563), so `http.HTTPFlow` in signatures is
    # never evaluated; the module stays importable without mitmproxy installed.
    http = None  # type: ignore[assignment]

# Set MEASURE_AUTOCONFIG=0 to manage client configuration yourself.
AUTOCONFIG = os.environ.get("MEASURE_AUTOCONFIG", "1") != "0"

PORT_MAP = {
    8081: "copilot_vscode",
    8082: "copilot_cli",
    8083: "claude_code",
}

PORT_MAP_BY_CLIENT = {client: port for port, client in PORT_MAP.items()}

RUN = os.environ.get("MEASURE_RUN", time.strftime("%Y%m%d-%H%M%S"))
TASK = os.environ.get("MEASURE_TASK", "unspecified")
OUTDIR = pathlib.Path(os.environ.get("MEASURE_DIR", "./measurements"))
OUTDIR.mkdir(parents=True, exist_ok=True)
OUTFILE = OUTDIR / f"{RUN}.jsonl"

# No prompt content persisted by default. MEASURE_KEEP_BODIES=1 for debug.
KEEP_BODIES = os.environ.get("MEASURE_KEEP_BODIES") == "1"


def client_of(flow: http.HTTPFlow) -> str | None:
    try:
        port = flow.client_conn.sockname[1]
    except (AttributeError, IndexError, TypeError):
        return None
    return PORT_MAP.get(port)


def _write(record: dict) -> None:
    with OUTFILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


class TokenMeter:
    """The addon. WebSocket hooks must be methods on an addon object for
    mitmproxy to discover them, which is why this is a class rather than the
    module-level functions the HTTP-only version used.
    """

    def __init__(self) -> None:
        # Per-WebSocket-flow accumulators, keyed by id(flow).
        self.ws: dict[int, dict] = {}

    # -- HTTP ---------------------------------------------------------------

    def response(self, flow: http.HTTPFlow) -> None:
        client = client_of(flow)
        if client is None:
            return

        kind = parsing.kind_of(flow.request.pretty_host, flow.request.path)
        if kind == "other":
            return

        try:
            req_body = json.loads(flow.request.get_text() or "{}")
        except json.JSONDecodeError:
            req_body = {}

        raw = flow.response.get_text(strict=False) or ""
        if raw.lstrip().startswith("data:"):
            usage = parsing.parse_stream(raw)
        else:
            try:
                usage = parsing.extract_usage(json.loads(raw))
            except json.JSONDecodeError:
                usage = {}

        record = {
            "ts": time.time(),
            "run": RUN,
            "task": TASK,
            "client": client,
            "kind": kind,
            # Alias announced by the client. On the Copilot side this is a
            # routing alias, not a verifiable snapshot: treat it as declarative.
            "model_declared": req_body.get("model"),
            "status": flow.response.status_code,
            "latency_ms": int(
                (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
            ),
            "tokens": usage,
            "billable_input": round(parsing.billable_input(usage), 1),
            "prompt_bytes": parsing.prompt_bytes(req_body),
            "response_bytes": len(raw.encode("utf-8")),
            "n_messages": len(req_body.get("messages") or []),
            "n_tools": len(req_body.get("tools") or []),
            "system_bytes": parsing._system_bytes(req_body),
        }

        if KEEP_BODIES:
            record["_request"] = req_body

        _write(record)

    # -- WebSocket ----------------------------------------------------------

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Accumulate the newest frame only.

        mitmproxy calls this once per message and appends to
        `flow.websocket.messages`, so looking only at the last entry keeps this
        O(1) per call (re-scanning the whole list here is what inflated the
        earlier prototype's byte counts into the gigabytes).

        WebSocket usage is cumulative like SSE, so we keep the single `usage`
        block with the largest `input_tokens` and normalize it once at the end
        through the shared parser — never a second, divergent parser.
        """
        if client_of(flow) is None or not flow.websocket or not flow.websocket.messages:
            return

        ws = self.ws.setdefault(
            id(flow),
            {"best_input": -1, "best_usage": None, "response_bytes": 0, "n_client": 0},
        )
        msg = flow.websocket.messages[-1]
        if msg.from_client:
            ws["n_client"] += 1
            return

        ws["response_bytes"] += len(msg.content)
        try:
            payload = json.loads(msg.content.decode("utf-8", errors="ignore"))
        except (json.JSONDecodeError, ValueError):
            return

        response = payload.get("response") if isinstance(payload, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict) and usage:
            total_in = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            if total_in >= ws["best_input"]:
                ws["best_input"] = total_in
                ws["best_usage"] = usage

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        ws = self.ws.pop(id(flow), None)
        if ws is None:
            return

        client = client_of(flow)
        kind = parsing.kind_of(flow.request.pretty_host, flow.request.path)
        if client is None or kind == "other" or ws["best_usage"] is None:
            return

        usage = parsing.extract_usage({"usage": ws["best_usage"]})
        if not usage.get("input") and not usage.get("output"):
            return

        latency = 0
        if flow.response and flow.response.timestamp_end:
            latency = int(
                (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
            )

        record = {
            "ts": time.time(),
            "run": RUN,
            "task": TASK,
            "client": client,
            "kind": kind,
            "model_declared": None,
            "status": 101,
            "latency_ms": latency,
            "tokens": usage,
            "billable_input": round(parsing.billable_input(usage), 1),
            "prompt_bytes": 0,
            "response_bytes": ws["response_bytes"],
            "n_messages": ws["n_client"],
            "n_tools": 0,
            "system_bytes": 0,
            "_source": "websocket",
        }

        if KEEP_BODIES:
            record["_response_usage"] = ws["best_usage"]

        _write(record)

    # -- Lifecycle ----------------------------------------------------------

    def running(self) -> None:
        """mitmproxy calls this once the proxy servers accept connections.

        Configuring here rather than earlier guarantees the CA already exists on
        disk - mitmproxy generates it during startup - so no bootstrap step is
        needed on a first run.
        """
        if not AUTOCONFIG:
            return
        if config.is_active():
            print(
                "harness-meter: existing state file found, leaving it alone.\n"
                "  A previous session did not tear down. Run scripts/proxy_stop.py\n"
                "  before measuring: the current settings.json is already proxied,\n"
                "  and backing it up again would make the original unrecoverable."
            )
            return
        config.apply(ports=PORT_MAP_BY_CLIENT)

    def done(self) -> None:
        """Called on graceful shutdown, including Ctrl-C.

        Not called on SIGKILL or a power loss, which is exactly why every change
        is journalled to a state file and scripts/proxy_stop.py exists.
        """
        if not AUTOCONFIG:
            return
        config.revert()


addons = [TokenMeter()]
