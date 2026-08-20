"""Tests for cross-platform proxy configuration.

The failure mode that matters here is not "setup didn't work" - that is loud
and immediate. It is "teardown didn't fully restore", which is silent and
leaves the operator's editor broken after the experiment ends.
"""

from __future__ import annotations

import json

import pytest

from harness_meter import config

# --------------------------------------------------------------------------
# JSONC
# --------------------------------------------------------------------------


def test_line_comments_are_stripped():
    text = '{\n  // a comment\n  "editor.fontSize": 13\n}'
    assert json.loads(config.strip_jsonc(text)) == {"editor.fontSize": 13}


def test_block_comments_are_stripped():
    text = '{\n  /* multi\n     line */\n  "a": 1\n}'
    assert json.loads(config.strip_jsonc(text)) == {"a": 1}


def test_trailing_commas_are_removed():
    text = '{\n  "a": 1,\n  "b": [1, 2,],\n}'
    assert json.loads(config.strip_jsonc(text)) == {"a": 1, "b": [1, 2]}


def test_comment_markers_inside_strings_survive():
    """A regex-based stripper corrupts any setting containing a URL or path.

    This is the case that motivates scanning character by character.
    """
    text = '{"a": "https://example.com//x", "b": "/* not a comment */"}'
    parsed = json.loads(config.strip_jsonc(text))
    assert parsed["a"] == "https://example.com//x"
    assert parsed["b"] == "/* not a comment */"


def test_escaped_quote_does_not_end_string():
    text = r'{"win": "C:\\path\\with\"quote", "n": 1}'
    parsed = json.loads(config.strip_jsonc(text))
    assert parsed["n"] == 1


def test_bom_prefixed_settings_are_readable(tmp_path):
    """VS Code on Windows may write a UTF-8 BOM."""
    path = tmp_path / "settings.json"
    path.write_text('\ufeff{"a": 1}', encoding="utf-8")
    assert config.read_settings(path) == {"a": 1}


# --------------------------------------------------------------------------
# VS Code settings rendering
# --------------------------------------------------------------------------


def test_render_vscode_settings_has_exactly_the_owned_keys():
    rendered = config.render_vscode_settings(8081)
    assert rendered == {
        "http.proxy": "http://127.0.0.1:8081",
        "http.proxyStrictSSL": False,
    }
    assert set(rendered) == set(config.VSCODE_KEYS)


def test_vscode_settings_json_is_valid_json_with_the_port():
    parsed = json.loads(config.vscode_settings_json(9001))
    assert parsed["http.proxy"] == "http://127.0.0.1:9001"
    assert parsed["http.proxyStrictSSL"] is False


def test_apply_writes_the_rendered_settings(fake_vscode, tmp_path):
    """apply and the standalone generator must agree — one source of truth."""
    settings, _ = fake_vscode
    config.apply(root=tmp_path, quiet=True)
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    for key, value in config.render_vscode_settings(8081).items():
        assert parsed[key] == value


# --------------------------------------------------------------------------
# Apply / revert round trip
# --------------------------------------------------------------------------


@pytest.fixture
def fake_vscode(tmp_path, monkeypatch):
    settings = tmp_path / "Code" / "User" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = '{\n  // keep me\n  "editor.fontSize": 13,\n}'
    settings.write_text(original, encoding="utf-8")
    monkeypatch.setattr(config, "_vscode_roots", lambda: [("Code", settings)])
    monkeypatch.setattr(
        config, "ca_cert_path", lambda: tmp_path / "mitmproxy-ca-cert.pem"
    )
    (tmp_path / "mitmproxy-ca-cert.pem").write_text("fake")
    return settings, original


def test_apply_sets_proxy_keys(fake_vscode, tmp_path):
    settings, _ = fake_vscode
    config.apply(root=tmp_path, quiet=True)
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    assert parsed["http.proxy"] == "http://127.0.0.1:8081"
    assert parsed["http.proxyStrictSSL"] is False


def test_apply_preserves_unrelated_settings(fake_vscode, tmp_path):
    settings, _ = fake_vscode
    config.apply(root=tmp_path, quiet=True)
    parsed = json.loads(settings.read_text(encoding="utf-8"))
    assert parsed["editor.fontSize"] == 13


def test_revert_restores_file_byte_for_byte(fake_vscode, tmp_path):
    """Including the comment, which our own writer would have discarded.

    This is why the backup is a byte copy rather than a re-serialization.
    """
    settings, original = fake_vscode
    config.apply(root=tmp_path, quiet=True)
    assert settings.read_text(encoding="utf-8") != original
    config.revert(root=tmp_path, quiet=True)
    assert settings.read_text(encoding="utf-8") == original


def test_revert_removes_state_and_env_files(fake_vscode, tmp_path):
    config.apply(root=tmp_path, quiet=True)
    state = config.state_dir(tmp_path)
    assert (state / "env" / "claude_code.sh").exists()
    config.revert(root=tmp_path, quiet=True)
    assert not (state / config.STATE_FILENAME).exists()
    assert not (state / "env" / "claude_code.sh").exists()


def test_revert_without_state_is_harmless(tmp_path):
    result = config.revert(root=tmp_path, quiet=True)
    assert result["reverted"] is False


def test_revert_is_idempotent(fake_vscode, tmp_path):
    config.apply(root=tmp_path, quiet=True)
    config.revert(root=tmp_path, quiet=True)
    assert config.revert(root=tmp_path, quiet=True)["reverted"] is False


def test_is_active_tracks_lifecycle(fake_vscode, tmp_path):
    assert config.is_active(tmp_path) is False
    config.apply(root=tmp_path, quiet=True)
    assert config.is_active(tmp_path) is True
    config.revert(root=tmp_path, quiet=True)
    assert config.is_active(tmp_path) is False


def test_unparseable_settings_are_skipped_not_mangled(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    broken = '{"a": 1'
    settings.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(config, "_vscode_roots", lambda: [("Code", settings)])
    monkeypatch.setattr(config, "ca_cert_path", lambda: tmp_path / "ca.pem")
    result = config.apply(root=tmp_path, quiet=True)
    assert settings.read_text(encoding="utf-8") == broken
    assert any("unparseable" in note for note in result["notes"])


# --------------------------------------------------------------------------
# Env snippets
# --------------------------------------------------------------------------


def test_each_client_gets_its_own_port(tmp_path):
    ca = tmp_path / "ca.pem"
    config.write_env_snippets(config.DEFAULT_PORTS, ca, tmp_path)
    cli = (tmp_path / "env" / "copilot_cli.sh").read_text()
    claude = (tmp_path / "env" / "claude_code.sh").read_text()
    assert "8082" in cli and "8083" not in cli
    assert "8083" in claude and "8082" not in claude


def test_vscode_gets_no_env_file(tmp_path):
    """VS Code is configured through settings.json.

    Exporting a proxy in its shell too would route extension traffic through
    the same port and pollute the agentic totals.
    """
    config.write_env_snippets(config.DEFAULT_PORTS, tmp_path / "ca.pem", tmp_path)
    assert not (tmp_path / "env" / "copilot_vscode.sh").exists()


def test_powershell_variant_is_written(tmp_path):
    config.write_env_snippets(config.DEFAULT_PORTS, tmp_path / "ca.pem", tmp_path)
    text = (tmp_path / "env" / "claude_code.ps1").read_text()
    assert "$env:HTTPS_PROXY" in text
    assert "export" not in text


def test_localhost_is_excluded_from_proxying(tmp_path):
    config.write_env_snippets(config.DEFAULT_PORTS, tmp_path / "ca.pem", tmp_path)
    text = (tmp_path / "env" / "claude_code.sh").read_text()
    assert "127.0.0.1" in text and "NO_PROXY" in text


def test_claude_code_telemetry_is_muted(tmp_path):
    config.write_env_snippets(config.DEFAULT_PORTS, tmp_path / "ca.pem", tmp_path)
    text = (tmp_path / "env" / "claude_code.sh").read_text()
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" in text


def test_missing_ca_is_reported_not_silently_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_vscode_roots", lambda: [])
    monkeypatch.setattr(config, "ca_cert_path", lambda: tmp_path / "absent.pem")
    result = config.apply(root=tmp_path, quiet=True)
    assert any("CA not found" in note for note in result["notes"])
