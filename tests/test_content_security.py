"""Regressionstests fuer die strikte Content-Security-Policy.

Die App-Oberflaeche darf kein ``unsafe-inline`` mehr benoetigen: Inline-Bloecke
werden per SHA-256-Hash freigegeben, Event-Handler und Style-Attribute sind
vollstaendig durch ``data-*``-Attribute bzw. CSS-Klassen ersetzt.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from ifs import main
from ifs.security_headers import WEB_DIR, _inline_hashes, build_csp

HTML_FILES = sorted(WEB_DIR.glob("*.html"))
INLINE_HANDLER = re.compile(r'(?<![-\w])on[a-z]+\s*=\s*"')
INLINE_STYLE_ATTR = re.compile(r'<[^>]*\sstyle\s*=\s*"')


def _html(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def _action_keys(source: str, name: str) -> set[str]:
    block = re.search(rf"const {name} = \{{(.*?)\n    \}};", source, re.DOTALL)
    assert block, f"{name} nicht gefunden"
    return set(re.findall(r"^\s{6}([A-Za-z]+):", block.group(1), re.MULTILINE))


def test_csp_header_has_no_unsafe_inline():
    with TestClient(main.app) as client:
        for path in ("/", "/mobile", "/published"):
            response = client.get(path)
            assert response.status_code == 200, path
            csp = response.headers["Content-Security-Policy"]
            assert "unsafe-inline" not in csp, path
            assert "unsafe-hashes" not in csp, path
            assert "script-src-attr 'none'" in csp, path
            assert "style-src-attr 'none'" in csp, path
            assert "object-src 'none'" in csp, path
            assert "base-uri 'none'" in csp, path


def test_csp_hashes_match_all_inline_blocks():
    scripts, styles = _inline_hashes(WEB_DIR)
    assert scripts, "keine Inline-Skripte gehasht"
    assert styles, "keine Inline-Styles gehasht"

    csp = build_csp()
    for value in (*scripts, *styles):
        assert value in csp, f"Hash fehlt in CSP: {value}"


def test_csp_does_not_allow_remote_or_data_scripts():
    csp = main.CONTENT_SECURITY_POLICY
    script_src = csp.split("script-src ", 1)[1].split(";", 1)[0]
    assert "http:" not in script_src
    assert "https:" not in script_src
    assert "data:" not in script_src


def test_no_inline_event_handlers_in_web_pages():
    for path in HTML_FILES:
        source = path.read_text(encoding="utf-8")
        assert not INLINE_HANDLER.search(source), f"Inline-Handler in {path.name}"


def test_no_inline_style_attributes_in_web_pages():
    for path in HTML_FILES:
        source = path.read_text(encoding="utf-8")
        assert not INLINE_STYLE_ATTR.search(source), f"Inline-Style in {path.name}"


def test_every_data_action_is_implemented():
    source = _html("index.html")
    click_actions = set(re.findall(r'data-action="([^"]+)"', source))
    change_actions = set(re.findall(r'data-change="([^"]+)"', source))
    input_actions = set(re.findall(r'data-input="([^"]+)"', source))

    assert click_actions <= _action_keys(source, "clickActions")
    assert change_actions <= _action_keys(source, "changeActions")
    assert input_actions <= _action_keys(source, "inputActions")


def test_preview_csp_has_no_unsafe_inline():
    source = (
        Path(__file__).resolve().parents[1] / "src/ifs/api/preview.py"
    ).read_text(encoding="utf-8")
    sandbox_policies = [
        line for line in source.splitlines() if "sandbox;" in line
    ]
    assert sandbox_policies, "Sandbox-CSP nicht gefunden"
    for line in sandbox_policies:
        assert "unsafe-inline" not in line
