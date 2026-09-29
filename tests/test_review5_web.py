"""Statische Regressionstests für die Web-UI (Review 5).

Kein Browser verfügbar: Die Tests laden ``src/ifs/web/index.html`` als Text
und prüfen die gemeldeten Fehlerbilder bzw. deren Fixes mit einfachen
Substring-/Regex-Checks. Sie dokumentieren die Regressionen, statt das HTML
zu parsen.
"""

from __future__ import annotations

import re
from pathlib import Path

HTML_PATH = Path(__file__).resolve().parents[1] / "src/ifs/web/index.html"


def _html() -> str:
    assert HTML_PATH.is_file(), f"Web-UI nicht gefunden: {HTML_PATH}"
    return HTML_PATH.read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    """Schneidet den Quelltext einer Top-Level-Funktion aus.

    Ende ist die nächste Top-Level-Funktion (4 Leerzeichen Einrückung) oder
    das Dateiende. Verschachtelte Funktionen sind tiefer eingerückt und
    beenden den Block daher nicht.
    """
    match = re.search(rf"\n    (?:async )?function {re.escape(name)}\s*\(", source)
    assert match, f"function {name} nicht gefunden"
    rest = source[match.end():]
    nxt = re.search(r"\n    (?:async )?function \w+\s*\(", rest)
    end = match.end() + nxt.start() if nxt else len(source)
    return source[match.start():end]


# --- 1) api(): Nicht-OK-Antworten müssen als Fehler geworfen werden ---

def test_api_throws_on_non_ok_response():
    block = _function_block(_html(), "api")
    assert "if (!res.ok)" in block, "api() behandelt Nicht-OK-Antworten wie Erfolg"
    assert "res.status" in block, "api() nennt den HTTP-Status im Fehler"
    assert "throw new Error(" in block, "api() wirft bei Nicht-OK nicht"


def test_api_json_relies_on_api_error_handling():
    # apiJson() darf nicht erneut res.ok prüfen – api() wirft bereits.
    block = _function_block(_html(), "apiJson")
    assert "res.ok" not in block


def test_download_and_revoke_share_do_not_double_check():
    # Beide nutzen api() und dürfen den Status nicht selbst auswerten.
    assert "res.ok" not in _function_block(_html(), "download")
    assert "response.ok" not in _function_block(_html(), "revokeShare")


def test_change_my_password_uses_api_json():
    # Falsches Alt-Passwort (HTTP 400) muss als Fehler ankommen – apiJson wirft.
    block = _function_block(_html(), "changeMyPassword")
    assert 'apiJson("POST", "/api/me/password"' in block


# --- 2) „Zugriff“-Dialog nur für Admins (admin-only Endpunkte) ---

def test_access_menu_item_is_admin_only():
    source = _html()
    guard = source.index("state.me && state.me.is_admin")
    item = source.index('label: "Zugriff"')
    assert 0 < item - guard < 300, "Menüpunkt „Zugriff“ ist nicht an is_admin gekoppelt"


# --- 3) logout(): Zustand, ZIP-Poll und Modal aufräumen ---

def test_logout_clears_transient_state():
    block = _function_block(_html(), "logout")
    assert "state.selected.clear()" in block, "Phantom-Auswahl überlebt den Logout"
    assert "clearArchivePoll()" in block, "ZIP-Poll läuft nach dem Logout weiter"
    assert "archiveJob = null" in block
    assert "closeModal()" in block, "offenes Modal bleibt auf dem Login-Screen stehen"


# --- 4) finishArchive(): kein fremdes Modal schließen ---

def test_finish_archive_does_not_close_foreign_modal():
    assert "closeModal()" not in _function_block(_html(), "finishArchive")


# --- 5) Breadcrumb: textContent statt doppeltem esc() ---

def test_breadcrumb_not_double_escaped():
    source = _html()
    assert "b.textContent = entry.name;" in source
    assert "b.textContent = esc(entry.name)" not in source


# --- 6) Textvorschau: kein Range bei 0-Byte-Dateien (sonst 416) ---

def test_text_preview_range_only_for_nonempty_files():
    block = _function_block(_html(), "openTextPreview")
    range_lines = [line for line in block.splitlines() if '"Range"' in line]
    assert range_lines, "Range-Header in openTextPreview nicht gefunden"
    assert all("entry.size > 0" in line for line in range_lines), (
        "Range-Header wird nicht an entry.size > 0 gekoppelt"
    )


# --- 7) Login: 429 bekommt eine eigene Meldung ---

def test_login_429_has_distinct_message():
    block = _function_block(_html(), "login")
    assert "res.status === 429" in block
    assert "Zu viele Fehlversuche" in block


# --- 8) Einzel-Löschen räumt die Auswahl auf ---

def test_remove_entry_clears_selection():
    block = _function_block(_html(), "removeEntry")
    assert "state.selected.delete(entry.id)" in block


# --- 9) Rollen-Zuweisungen: Liste nach dem Entfernen neu laden ---

def test_remove_role_assignment_reloads_dialog():
    block = _function_block(_html(), "removeRoleAssignment")
    assert "roleAssignmentsDialog(" in block, "Dialog wird nach dem Entfernen nicht neu geladen"
