"""Statische Prüfungen der Performance-/Robustheits-Optimierungen der Web-UI.

Kein Browser verfügbar: Die Tests prüfen ``src/ifs/web/index.html`` als Text
auf die eingebauten Maßnahmen (Timer-Sichtbarkeit, Request-Deduplizierung,
AbortController, Debounce, Dokument-Fragmente, Objekt-URL-Aufräumen).
Die bestehenden Tests in ``tests/test_review5_web.py`` bleiben unverändert
und müssen weiter grün sein.
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


# --- Netzwerk: In-Flight-Deduplizierung + kurzer GET-Cache ---

def test_api_json_dedupes_identical_get_requests():
    block = _function_block(_html(), "apiJson")
    assert "jsonInflight" in block, "keine In-Flight-Deduplizierung"
    assert "jsonInflight.get(path)" in block, "laufende Anfragen werden nicht wiederverwendet"
    assert "jsonCache.get(path)" in block, "kein kurzer GET-Cache"
    assert "JSON_CACHE_TTL" in block, "Cache ohne TTL"
    assert 'method === "GET"' in block, "Cache nicht auf GET begrenzt"


def test_api_invalidates_cache_on_mutation():
    block = _function_block(_html(), "api")
    assert "bustJsonCache()" in block, "Mutationen verwerfen den GET-Cache nicht"
    assert 'method !== "GET"' in block


def test_cache_is_busted_on_logout_refresh_and_upload():
    source = _html()
    assert "bustJsonCache()" in _function_block(source, "logout")
    assert "bustJsonCache()" in _function_block(source, "refreshCurrent")
    # Uploads laufen per XHR an api() vorbei -> Liste muss frisch geladen werden.
    assert "bustJsonCache()" in _function_block(source, "uploadFiles")


def test_abort_controller_for_navigation():
    load = _function_block(_html(), "load")
    assert "new AbortController()" in load, "kein Abbruch bei schnellem Ordnerwechsel"
    assert ".abort()" in load, "vorherige Anfrage wird nicht abgebrochen"
    assert "{ signal }" in load, "Signal wird nicht an den Request gereicht"
    assert "AbortError" in load, "Abbruch wird als Fehler gemeldet"

    shared = _function_block(_html(), "loadShared")
    assert "new AbortController()" in shared
    assert "AbortError" in shared


def test_api_passes_signal_to_fetch():
    block = _function_block(_html(), "api")
    assert "signal" in block
    assert "fetch(" in block


# --- Timer: nur bei sichtbarer, aktiver Ansicht laufen lassen ---

def test_visibilitychange_stops_background_timers():
    source = _html()
    assert 'document.addEventListener("visibilitychange"' in source
    idx = source.index('document.addEventListener("visibilitychange"')
    handler = source[idx:idx + 700]
    assert 'document.visibilityState === "hidden"' in handler
    assert "clearSystemTimer()" in handler
    assert "clearArchivePoll()" in handler
    # Beim Zurückkehren wird der ZIP-Poll wieder aufgenommen.
    assert "startArchivePoll()" in handler


def test_all_intervals_have_visibility_checks():
    source = _html()
    assert source.count("setInterval(") == 2, "unerwartete setInterval-Aufrufe"

    system = _function_block(source, "syncSystemTimer")
    assert "document.visibilityState" in system
    assert 'state.view !== "system"' in system
    assert "setInterval(loadSystem" in system

    archive = _function_block(source, "startArchivePoll")
    assert "document.visibilityState" in archive
    assert "clearArchivePoll()" in archive
    assert "setInterval(pollArchiveJob" in archive


def test_load_system_does_not_own_the_timer():
    block = _function_block(_html(), "loadSystem")
    assert "setInterval(" not in block, "loadSystem startet weiterhin ungeprüft einen Timer"
    assert "syncSystemTimer()" in block


def test_archive_poll_is_single_flight_and_uncached():
    block = _function_block(_html(), "pollArchiveJob")
    assert "archivePollBusy" in block, "keine Überlappungssperre für Status-Abfragen"
    assert "noCache" in block, "Fortschritt käme aus dem GET-Cache"
    assert "archiveJob !== job" in block, "alte Antworten könnten neue Zustände überschreiben"


# --- Eingaben: Debounce ---

def test_debounce_helper_exists():
    block = _function_block(_html(), "debounce")
    assert "clearTimeout(" in block
    assert "setTimeout(" in block


def test_debounced_handlers_are_wired():
    source = _html()
    assert 'oninput="scheduleRender()"' in source, "Suche rendert weiter pro Tastendruck"
    assert 'oninput="scheduleLoadUsers()"' in source, "Benutzersuche lädt weiter pro Tastendruck"
    assert 'oninput="scheduleRenderAudit()"' in source, "Audit-Filter rendert weiter pro Tastendruck"
    assert "oninput=\"render()\"" not in source
    assert "oninput=\"loadUsers()\"" not in source
    assert "oninput=\"renderAudit()\"" not in source
    assert "const scheduleRender = debounce(" in source
    assert "const scheduleLoadUsers = debounce(" in source
    assert "const scheduleRenderAudit = debounce(" in source


# --- DOM: Massen-Rendering ohne Append-Schleifen ---

def test_row_renderers_use_document_fragment():
    source = _html()
    assert source.count("createDocumentFragment()") >= 6
    for name in ("render", "renderTrash", "renderUsers", "renderRoles",
                 "renderShared", "renderShares"):
        block = _function_block(source, name)
        assert "createDocumentFragment()" in block, f"{name} nutzt kein DocumentFragment"
        assert "replaceChildren(frag)" in block, f"{name} ersetzt den Inhalt nicht in einem Schritt"


def test_audit_renders_with_single_innerhtml():
    block = _function_block(_html(), "renderAudit")
    assert "createElement" not in block, "Audit-Zeilen entstehen weiter Zelle für Zelle"
    assert "innerHTML = items.map(" in block


def test_renderers_register_no_persistent_listeners():
    # Zeilen-Listener hängen an den (verworfenen) Knoten; kein addEventListener
    # in Renderpfaden, damit Re-Render keine Listener anhäuft.
    source = _html()
    for name in ("render", "renderTrash", "renderUsers", "renderShares", "renderShared"):
        assert "addEventListener" not in _function_block(source, name), (
            f"{name} registriert Listener, die bei Re-Render dupliziert werden könnten"
        )


# --- Speicher: Objekt-URLs ---

def test_object_urls_are_always_revoked():
    source = _html()
    created = source.count("URL.createObjectURL(")
    revoked = source.count("URL.revokeObjectURL(")
    assert created >= 2
    assert created == revoked, "Blob-URL ohne revokeObjectURL (Speicherleck)"


def test_upload_progress_caches_dom_nodes():
    block = _function_block(_html(), "progressEl")
    assert "const pctEl" in block
    after_return = block.split("return {", 1)[1]
    assert "querySelector" not in after_return, "querySelector läuft weiter pro Fortschritts-Event"


# --- Unabhängige Fetches parallel ---

def test_independent_fetches_run_in_parallel():
    source = _html()
    for name in ("start", "removeEntry", "bulkDelete", "bulkRestoreTrash"):
        assert "Promise.all" in _function_block(source, name), f"{name} wartet weiter sequenziell"


# --- Stabilität: IDs/Funktionen der bestehenden Tests bleiben erhalten ---

def test_key_dom_ids_are_preserved():
    source = _html()
    for element_id in ("entries", "trash-entries", "user-rows", "role-rows", "share-rows",
                       "shared-rows", "audit-rows", "system-cards", "search", "user-q",
                       "audit-q", "bulkbar", "toasts", "modal-root"):
        assert f'id="{element_id}"' in source, f"ID {element_id} fehlt"
