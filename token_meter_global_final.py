"""token_meter.py — single mitmproxy addon, three clients.

Compares Copilot/VS Code, Copilot CLI and Claude Code on the same model and the
same task. One process, one parser: no instrumentation bias.

The parsing core lives in `harness_meter.parsing` (importable without mitmproxy,
so it is unit-tested in isolation); this file is the thin addon around it —
traffic attribution, per-request I/O, and the setup/teardown lifecycle.

Attribution is by listen port, not by host, because VS Code and Copilot CLI
share the same destination.

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


def response(flow: http.HTTPFlow) -> None:
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
        # Alias announced by the client. On the Copilot side this is a routing
        # alias, not a verifiable snapshot: treat it as declarative.
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
        try:
            record["_response"] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            record["_response"] = raw

    with OUTFILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")



# Track active WebSocket flows to accumulate messages
_ws_flows: dict[int, dict] = {}


def websocket_message(flow: http.HTTPFlow) -> None:
    """Called when a WebSocket message arrives."""
    flow_id = id(flow)
    print(f"[DEBUG] websocket_message() called! flow_id={flow_id}, messages={len(flow.websocket.messages) if flow.websocket else 0}")
    
    # Initialize tracking if needed
    if flow_id not in _ws_flows:
        client = client_of(flow)
        if client is None:
            return
        
        _ws_flows[flow_id] = {
            "client": client,
            "total_input": 0,
            "total_output": 0,
            "total_cache_write": 0,
            "total_cache_read": 0,
            "found_usage": False,
            "last_message_data": None,
            "response_bytes": 0,
            "messages_from_client": 0,
        }
    
    ws = _ws_flows[flow_id]
    
    # Get the most recent message
    if flow.websocket and flow.websocket.messages:
        msg = flow.websocket.messages[-1]
        
        # Skip client-to-server messages
        if msg.from_client:
            ws["messages_from_client"] += 1
            return
        
        # Accumulate response bytes
        ws["response_bytes"] += len(msg.content)
        
        try:
            content = msg.content.decode('utf-8', errors='ignore')
            payload = json.loads(content)
            
            # Search for usage data in the message
            if isinstance(payload, dict) and 'response' in payload:
                resp = payload['response']
                if isinstance(resp, dict) and 'usage' in resp:
                    usage = resp['usage']
                    if isinstance(usage, dict) and usage:  # Non-empty usage dict
                        ws["found_usage"] = True
                        ws["total_input"] = max(ws["total_input"], usage.get('input_tokens', 0))
                        ws["total_output"] = max(ws["total_output"], usage.get('output_tokens', 0))
                        
                        # Handle cache details
                        details = usage.get('input_tokens_details', {})
                        ws["total_cache_write"] = max(ws["total_cache_write"], details.get('cache_write_tokens', 0))
                        ws["total_cache_read"] = max(ws["total_cache_read"], details.get('cached_tokens', 0))
                        ws["last_message_data"] = payload
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            pass


def websocket_end(flow: http.HTTPFlow) -> None:
    """Called when a WebSocket connection ends."""
    flow_id = id(flow)
    print(f"[DEBUG] websocket_end() called! flow_id={flow_id}, path={flow.request.path}")
    
    if flow_id not in _ws_flows:
        return
    
    ws = _ws_flows.pop(flow_id)  # Remove from tracking
    client = ws["client"]
    
    # Skip if no usage found or endpoint isn't /responses
    if not ws["found_usage"] or ws["total_input"] == 0 or "/responses" not in flow.request.path:
        return
    
    record = {
        "ts": time.time(),
        "run": RUN,
        "task": TASK,
        "client": client,
        "kind": "agentic",
        "model_declared": None,  # Model not declared in WebSocket
        "status": 101,  # WebSocket upgrade status
        "latency_ms": int(
            (flow.response.timestamp_end - flow.request.timestamp_start) * 1000
        ) if flow.response and flow.response.timestamp_end else 0,
        "tokens": {
            "input": ws["total_input"],
            "output": ws["total_output"],
            "cache_write": ws["total_cache_write"],
            "cache_read": ws["total_cache_read"],
        },
        "billable_input": round(parsing.billable_input({
            "input": ws["total_input"],
            "cache_write": ws["total_cache_write"],
            "cache_read": ws["total_cache_read"],
        }), 1),
        "prompt_bytes": 0,  # Not tracked for WebSocket
        "response_bytes": ws["response_bytes"],
        "n_messages": ws["messages_from_client"],
        "n_tools": 0,
        "system_bytes": 0,
        "_source": "websocket",  # Mark as WebSocket source
    }
    
    if KEEP_BODIES and ws["last_message_data"]:
        record["_response"] = ws["last_message_data"]
    
    with OUTFILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Lifecycle: configure clients on startup, restore them on shutdown
# --------------------------------------------------------------------------


def running() -> None:
    """mitmproxy calls this once the proxy servers are accepting connections.

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


def done() -> None:
    """Called on graceful shutdown, including Ctrl-C.

    Not called on SIGKILL or a power loss, which is exactly why every change
    is journalled to a state file and scripts/proxy_stop.py exists.
    """
    if not AUTOCONFIG:
        return
    config.revert()


# Create a global addon instance for mitmproxy to discover and use
addons = [TokenMeter()]
