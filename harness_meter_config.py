"""Cross-platform proxy configuration for harness-meter.

Configures exactly three clients and nothing else. Deliberately does *not*
touch the system proxy (WinINET registry, networksetup, gsettings) nor the
system certificate store:

- A system-wide proxy would capture unrelated traffic and destroy the
  port-based attribution the measurement depends on.
- A system CA install is the one change that leaves durable residue if
  teardown fails. NODE_EXTRA_CA_CERTS and proxyStrictSSL avoid needing it.

Every mutation is recorded in a state file so teardown works even after a
hard kill, when the addon's shutdown hook never ran.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import sys
import time
from typing import Any

STATE_DIRNAME = ".harness-meter"
STATE_FILENAME = "state.json"

# Keys harness-meter owns in VS Code settings. Anything else is left alone.
VSCODE_KEYS = ("http.proxy", "http.proxyStrictSSL")

DEFAULT_PORTS = {
    "copilot_vscode": 8081,
    "copilot_cli": 8082,
    "claude_code": 8083,
}


# --------------------------------------------------------------------------
# Platform paths
# --------------------------------------------------------------------------


def ca_cert_path() -> pathlib.Path:
    """mitmproxy stores its CA under ~/.mitmproxy on every platform."""
    return pathlib.Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def _vscode_roots() -> list[tuple[str, pathlib.Path]]:
    """Candidate VS Code user-settings directories, per platform.

    Includes Insiders and VSCodium: a developer running the stable build is
    not evidence that the others are absent, and configuring the wrong one
    silently produces zero VS Code traffic.
    """
    home = pathlib.Path.home()
    system = platform.system()
    flavors = ("Code", "Code - Insiders", "VSCodium")

    if system == "Windows":
        appdata = pathlib.Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        base = appdata
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))

    return [(flavor, base / flavor / "User" / "settings.json") for flavor in flavors]


def state_dir(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or pathlib.Path.cwd()) / STATE_DIRNAME


# --------------------------------------------------------------------------
# JSONC handling
# --------------------------------------------------------------------------


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas from VS Code's JSONC settings.

    A plain json.loads fails on real settings files, which routinely contain
    // comments. Scans character by character so that // and /* inside string
    literals survive - a naive regex would corrupt any path containing them.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False

    while index < length:
        char = text[index]

        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "/":
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if nxt == "*":
                index += 2
                while index + 1 < length and not (
                    text[index] == "*" and text[index + 1] == "/"
                ):
                    index += 1
                index += 2
                continue

        out.append(char)
        index += 1

    cleaned = "".join(out)

    # Trailing commas, again skipping string contents.
    result: list[str] = []
    in_string = False
    escaped = False
    for position, char in enumerate(cleaned):
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            result.append(char)
            continue
        if char == ",":
            rest = cleaned[position + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                continue
        result.append(char)
    return "".join(result)


def read_settings(path: pathlib.Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(strip_jsonc(raw))


# --------------------------------------------------------------------------
# Environment snippets
# --------------------------------------------------------------------------


def _posix_snippet(port: int, ca: pathlib.Path, extra: dict[str, str]) -> str:
    lines = [
        "# harness-meter - source this before launching the client",
        f'export HTTP_PROXY="http://127.0.0.1:{port}"',
        f'export HTTPS_PROXY="http://127.0.0.1:{port}"',
        f'export http_proxy="http://127.0.0.1:{port}"',
        f'export https_proxy="http://127.0.0.1:{port}"',
        'export NO_PROXY="localhost,127.0.0.1,::1"',
        f'export NODE_EXTRA_CA_CERTS="{ca}"',
    ]
    lines += [f'export {key}="{value}"' for key, value in extra.items()]
    return "\n".join(lines) + "\n"


def _powershell_snippet(port: int, ca: pathlib.Path, extra: dict[str, str]) -> str:
    lines = [
        "# harness-meter - dot-source this before launching the client",
        f'$env:HTTP_PROXY = "http://127.0.0.1:{port}"',
        f'$env:HTTPS_PROXY = "http://127.0.0.1:{port}"',
        '$env:NO_PROXY = "localhost,127.0.0.1,::1"',
        f'$env:NODE_EXTRA_CA_CERTS = "{ca}"',
    ]
    lines += [f'$env:{key} = "{value}"' for key, value in extra.items()]
    return "\n".join(lines) + "\n"


def write_env_snippets(
    ports: dict[str, int], ca: pathlib.Path, target: pathlib.Path
) -> list[pathlib.Path]:
    """Emit per-client env files.

    A child process cannot mutate its parent's environment, so these are
    written to disk for the operator to source. One file per client keeps the
    port binding explicit and makes a mix-up visible.
    """
    env_dir = target / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []

    extras = {
        # Removes telemetry and update pings from the measured stream.
        "claude_code": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
        "copilot_cli": {},
    }

    for client, port in ports.items():
        if client == "copilot_vscode":
            continue  # configured via settings.json, not the environment
        extra = extras.get(client, {})
        posix = env_dir / f"{client}.sh"
        posix.write_text(_posix_snippet(port, ca, extra), encoding="utf-8")
        powershell = env_dir / f"{client}.ps1"
        powershell.write_text(_powershell_snippet(port, ca, extra), encoding="utf-8")
        written.extend([posix, powershell])

    return written


# --------------------------------------------------------------------------
# Apply / revert
# --------------------------------------------------------------------------


def apply(
    ports: dict[str, int] | None = None,
    root: pathlib.Path | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    ports = ports or DEFAULT_PORTS
    target = state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    ca = ca_cert_path()

    state: dict[str, Any] = {
        "created_at": time.time(),
        "pid": os.getpid(),
        "ports": ports,
        "vscode": [],
        "env_files": [],
    }
    notes: list[str] = []

    if not ca.exists():
        notes.append(
            f"CA not found at {ca}. Run mitmdump once to generate it, "
            "then re-run setup."
        )

    port = ports.get("copilot_vscode")
    for flavor, settings_path in _vscode_roots():
        if not settings_path.exists():
            continue
        backup = target / f"vscode-{flavor.replace(' ', '_')}.settings.bak"
        # Byte-exact backup: restore never depends on our own serializer.
        shutil.copy2(settings_path, backup)
        try:
            settings = read_settings(settings_path)
        except json.JSONDecodeError as exc:
            notes.append(f"{flavor}: settings.json unparseable ({exc}); skipped.")
            backup.unlink(missing_ok=True)
            continue

        settings["http.proxy"] = f"http://127.0.0.1:{port}"
        settings["http.proxyStrictSSL"] = False
        settings_path.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        state["vscode"].append(
            {"flavor": flavor, "path": str(settings_path), "backup": str(backup)}
        )
        notes.append(f"{flavor}: proxy set to port {port}. Restart it fully.")

    if not state["vscode"]:
        notes.append("No VS Code settings.json found; VS Code left unconfigured.")

    state["env_files"] = [str(p) for p in write_env_snippets(ports, ca, target)]

    (target / STATE_FILENAME).write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )

    if not quiet:
        _report("configured", state, notes)
    return {"state": state, "notes": notes}


def revert(root: pathlib.Path | None = None, quiet: bool = False) -> dict[str, Any]:
    target = state_dir(root)
    state_path = target / STATE_FILENAME
    notes: list[str] = []

    if not state_path.exists():
        if not quiet:
            print("harness-meter: nothing to revert (no state file).")
        return {"reverted": False, "notes": ["no state file"]}

    state = json.loads(state_path.read_text(encoding="utf-8"))

    for entry in state.get("vscode", []):
        settings_path = pathlib.Path(entry["path"])
        backup = pathlib.Path(entry["backup"])
        if backup.exists():
            shutil.copy2(backup, settings_path)
            backup.unlink()
            notes.append(f"{entry['flavor']}: settings.json restored byte-for-byte.")
        else:
            notes.append(
                f"{entry['flavor']}: backup missing; remove {VSCODE_KEYS} by hand."
            )

    for path_str in state.get("env_files", []):
        pathlib.Path(path_str).unlink(missing_ok=True)
    env_dir = target / "env"
    if env_dir.exists() and not any(env_dir.iterdir()):
        env_dir.rmdir()

    state_path.unlink()
    notes.append(
        "Environment variables persist in shells you already opened. "
        "Close them or unset HTTP_PROXY/HTTPS_PROXY/NODE_EXTRA_CA_CERTS."
    )

    if not quiet:
        _report("reverted", state, notes)
    return {"reverted": True, "notes": notes}


def is_active(root: pathlib.Path | None = None) -> bool:
    return (state_dir(root) / STATE_FILENAME).exists()


def _report(action: str, state: dict[str, Any], notes: list[str]) -> None:
    print(f"harness-meter: proxy {action}")
    if action == "configured":
        for client, port in state["ports"].items():
            print(f"  {port}  {client}")
    for note in notes:
        print(f"  - {note}")
    sys.stdout.flush()
