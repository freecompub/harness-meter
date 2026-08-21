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
import sys
import time

# Add current directory to path so mitmproxy can find harness_meter
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from harness_meter import config, parsing

try:
    from mitmproxy import http
except ImportError:  # pragma: no cover
    http = None  # type: ignore[assignment]

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

KEEP_BODIES = os.environ.get("MEASURE_KEEP_BODIES") == "1"


def client_of(flow: http.HTTPFlow) -> str | None:
    try:
        port = flow.client_conn.sockname[1]
    except (AttributeError, IndexError, TypeError):
        return None
    return PORT_MAP.get(port)


class TokenMeter:
    """mitmproxy addon for measuring token consumption across three harnesses."""
    
    def __init__(self):
        # Track active WebSocket flows to accumulate messages
        self.ws_flows: dict[int, dict] = {}
    
    def response(self, flow: http.HTTPFlow) -> None:
        """Called when an HTTP response is received."""
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

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Called when a WebSocket message arrives."""
        flow_id = id(flow)
        print(f"[✅ WS] websocket_message() called! messages: {len(flow.websocket.messages) if flow.websocket else 0}")
        
        if flow_id not in self.ws_flows:
            client = client_of(flow)
            if client is None:
                return
            
            self.ws_flows[flow_id] = {
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
        
        ws = self.ws_flows[flow_id]
        
        if flow.websocket and flow.websocket.messages:
            msg = flow.websocket.messages[-1]
            
            if msg.from_client:
                ws["messages_from_client"] += 1
                return
            
            ws["response_bytes"] += len(msg.content)
            
            try:
                content = msg.content.decode('utf-8', errors='ignore')
                payload = json.loads(content)
                
                if isinstance(payload, dict) and 'response' in payload:
                    resp = payload['response']
                    if isinstance(resp, dict) and 'usage' in resp:
                        usage = resp['usage']
                        if isinstance(usage, dict) and usage:
                            ws["found_usage"] = True
                            ws["total_input"] = max(ws["total_input"], usage.get('input_tokens', 0))
                            ws["total_output"] = max(ws["total_output"], usage.get('output_tokens', 0))
                            
                            details = usage.get('input_tokens_details', {})
                            ws["total_cache_write"] = max(ws["total_cache_write"], details.get('cache_write_tokens', 0))
                            ws["total_cache_read"] = max(ws["total_cache_read"], details.get('cached_tokens', 0))
                            ws["last_message_data"] = payload
                            print(f"[✅ WS] Found tokens: {ws['total_input']} input")
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
                pass

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        """Called when a WebSocket connection ends."""
        flow_id = id(flow)
        print(f"[✅ WS] websocket_end() called! path={flow.request.path}")
        
        if flow_id not in self.ws_flows:
            return
        
        ws = self.ws_flows.pop(flow_id)
        client = ws["client"]
        
        if not ws["found_usage"] or ws["total_input"] == 0 or "/responses" not in flow.request.path:
            print(f"[✅ WS] Skipping: found_usage={ws['found_usage']}, input={ws['total_input']}")
            return
        
        print(f"[✅ WS] ✅✅✅ WRITING WebSocket record: {ws['total_input']} input tokens ✅✅✅")
        
        record = {
            "ts": time.time(),
            "run": RUN,
            "task": TASK,
            "client": client,
            "kind": "agentic",
            "model_declared": None,
            "status": 101,
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
            "prompt_bytes": 0,
            "response_bytes": ws["response_bytes"],
            "n_messages": ws["messages_from_client"],
            "n_tools": 0,
            "system_bytes": 0,
            "_source": "websocket",
        }
        
        if KEEP_BODIES and ws["last_message_data"]:
            record["_response"] = ws["last_message_data"]
        
        with OUTFILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def running() -> None:
    """mitmproxy calls this once the proxy servers are accepting connections."""
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
    """Called on graceful shutdown, including Ctrl-C."""
    if not AUTOCONFIG:
        return
    config.revert()
