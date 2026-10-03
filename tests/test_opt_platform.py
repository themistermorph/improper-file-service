"""Plattform-Optimierungen: Golden-Cases, Parität und Mess-/Strukturnachweise.

Deckt die optimierten Pfade ab:
- ``normalize_path`` (Fast Path) inkl. exaktem Vergleich zur bisherigen Semantik,
- Header-Helfer (``content_disposition``, ``safe_mime``) und ``as_utc``/``utcnow``,
- JWT-Zwecktrennung nach Deduplizierung der Decode-Logik,
- Start (genau ein Bucket-Check, leichte Security-Header-Middleware),
- Worker (Sleep-Intervall und Outbox-Batch über Settings steuerbar).
"""

from __future__ import annotations

import itertools
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ifs import main
from ifs.config import get_settings
from ifs.core import content as content_module
from ifs.db import session_scope
from ifs.models import Entry, EntryType, Outbox, User
from ifs.security import hash_password
from ifs.utils import as_utc, content_disposition, normalize_path, safe_mime, utcnow


def _reference_normalize(path: str | None) -> str:
    """Bisherige normalize_path-Implementierung als eingefrorene Referenz."""
    if not path:
        return "/"
    parts: list[str] = []
    for segment in str(path).replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError("Pfad-Traversal ist nicht erlaubt")
        parts.append(segment)
    return "/" + "/".join(parts)


GOLDEN_CASES = [
    (None, "/"),
    ("", "/"),
    ("/", "/"),
    ("//", "/"),
    ("///", "/"),
    (".", "/"),
    ("./.", "/"),
    ("/./", "/"),
    ("a", "/a"),
    ("a/", "/a"),
    ("/a/", "/a"),
    ("a/b", "/a/b"),
    ("/a/b", "/a/b"),
    ("/a/b/", "/a/b"),
    ("//a//b//", "/a/b"),
    ("\\a\\b\\", "/a/b"),
    ("a\\b", "/a/b"),
    ("\\\\", "/"),
    ("a/./b", "/a/b"),
    ("...", "/..."),
    ("..a", "/..a"),
    ("a..", "/a.."),
    (".hidden", "/.hidden"),
    ("/a/.hidden/b", "/a/.hidden/b"),
    ("/folder/sub/file.txt", "/folder/sub/file.txt"),
    ("Ordner mit Leerzeichen/datei.pdf", "/Ordner mit Leerzeichen/datei.pdf"),
    ("/ünï/cödé.txt", "/ünï/cödé.txt"),
]


def test_normalize_path_golden_cases() -> None:
    for raw, expected in GOLDEN_CASES:
        assert normalize_path(raw) == expected, repr(raw)


def test_normalize_path_rejects_traversal() -> None:
    cases = ["..", "../", "../a", "a/../b", "a/..", "/..", "/../", "\\..", "..\\a", "a//../b"]
    for raw in cases:
        with pytest.raises(ValueError, match="Pfad-Traversal"):
            normalize_path(raw)


def test_normalize_path_matches_reference_exhaustively() -> None:
    """Alle Kombinationen aus 'a', '.', '/', '\\' bis Länge 5 sind äquivalent."""
    for length in range(6):
        for chars in itertools.product("a./\\", repeat=length):
            path = "".join(chars)
            try:
                expected = _reference_normalize(path)
            except ValueError:
                with pytest.raises(ValueError, match="Pfad-Traversal"):
                    normalize_path(path)
                continue
            assert normalize_path(path) == expected, repr(path)


def test_normalize_path_fast_path_returns_same_object() -> None:
    """Strukturnachweis: bereits normalisierte Pfade werden ohne Allokation zurückgegeben."""
    for path in ("/a", "/folder/sub/file.txt", "/ünï/cödé.txt"):
        assert normalize_path(path) is path
        assert normalize_path(path) == _reference_normalize(path)


_REF_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _reference_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Bisherige content_disposition-Implementierung als eingefrorene Referenz."""
    safe = _REF_CONTROL_RE.sub("", filename or "")
    fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in safe
    )
    fallback = fallback.strip() or "download"
    encoded = quote(safe, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def test_content_disposition_unchanged() -> None:
    filenames = [
        "",
        "a.txt",
        'a"b\r\nc.txt',
        "ümlaut.txt",
        "tab\tname",
        "del\x7fchar",
        "quote'and\\backslash",
        "  spaced  ",
        "日本語.csv",
        "a" * 300,
    ]
    for filename in filenames:
        assert content_disposition(filename) == _reference_content_disposition(
            filename
        ), repr(filename)
        assert content_disposition(filename, "inline") == _reference_content_disposition(
            filename, "inline"
        ), repr(filename)
        header = content_disposition(filename)
        assert "\r" not in header and "\n" not in header


def test_safe_mime_control_and_default_semantics() -> None:
    assert safe_mime("text/plain") == "text/plain"
    # Äußere Whitespace/Newlines werden gestrippt (bisheriges Verhalten).
    assert safe_mime("text/plain\n") == "text/plain"
    # Interne Steuerzeichen bleiben abgelehnt.
    assert safe_mime("text/pl\nain") == "application/octet-stream"
    assert safe_mime("TeXt/HtMl") == "text/html"
    assert safe_mime("  IMAGE/SVG+XML ") == "image/svg+xml"
    assert safe_mime("") == "application/octet-stream"
    assert safe_mime(None) == "application/octet-stream"
    assert safe_mime("a/b", default="x/y") == "a/b"
    assert safe_mime("nonsense", default="x/y") == "x/y"


def test_as_utc_and_utcnow_unchanged() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)
    assert as_utc(None) is None
    assert as_utc(aware) is aware
    assert as_utc(naive) == aware
    assert utcnow().tzinfo is UTC


def test_token_decode_deduplication_keeps_purpose_isolation() -> None:
    from ifs.security import (
        create_access_token,
        create_archive_token,
        create_preview_token,
        decode_access_token,
        decode_archive_token,
        decode_preview_token,
    )

    access = create_access_token("user-1", extra={"tv": 0})
    preview = create_preview_token("entry-1", "user-1", 0)
    archive = create_archive_token(["entry-1", "entry-2"], "user-1", 0)

    assert decode_access_token(access)["purpose"] == "access"
    assert decode_preview_token(preview)["purpose"] == "preview"
    archive_payload = decode_archive_token(archive)
    assert archive_payload["purpose"] == "archive"
    assert archive_payload["entries"] == ["entry-1", "entry-2"]

    # Zweckfremde Tokens bleiben unverändert ungültig.
    assert decode_access_token(preview) is None
    assert decode_access_token(archive) is None
    assert decode_preview_token(access) is None
    assert decode_archive_token(preview) is None
    assert decode_access_token("kein.jwt.token") is None


def test_app_startup_checks_bucket_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startkosten: ensure_bucket wird pro Lifespan genau einmal aufgerufen."""
    calls: list[int] = []
    monkeypatch.setattr(content_module, "ensure_bucket", lambda: calls.append(1))
    with TestClient(main.app):
        pass
    assert len(calls) == 1


EXPECTED_STATIC_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
    "content-security-policy": main.CONTENT_SECURITY_POLICY,
}


def test_security_headers_middleware_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content_module, "ensure_bucket", lambda: None)
    with TestClient(main.app) as client:
        healthy = client.get("/healthz")
        missing = client.get("/does-not-exist")
    assert healthy.status_code == 200
    assert missing.status_code == 404
    for response in (healthy, missing):
        for key, value in EXPECTED_STATIC_HEADERS.items():
            assert response.headers[key] == value
        # Unverschlüsselte Anfrage: kein HSTS-Header.
        assert "strict-transport-security" not in response.headers

    # TLS-Anfrage (hier via base_url simuliert) setzt HSTS weiterhin.
    with TestClient(main.app, base_url="https://testserver") as secure_client:
        secure = secure_client.get("/healthz")
    assert secure.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_security_headers_avoid_base_http_middleware() -> None:
    """Strukturnachweis: keine BaseHTTPMiddleware (Task-Wrapper) mehr im Stack."""
    from starlette.middleware.base import BaseHTTPMiddleware

    classes = [middleware.cls for middleware in main.app.user_middleware]
    assert main.SecurityHeadersMiddleware in classes
    assert not any(
        isinstance(cls, type) and issubclass(cls, BaseHTTPMiddleware) for cls in classes
    )


def test_run_worker_interval_from_settings_and_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ifs import worker

    slept: list[int] = []

    class _StopLoop(Exception):
        pass

    def fake_sleep(seconds: int) -> None:
        slept.append(seconds)
        raise _StopLoop

    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    monkeypatch.setattr(worker, "run_once", lambda: None)

    with pytest.raises(_StopLoop):
        worker.run_worker()
    assert slept == [10]  # Default unverändert

    monkeypatch.setenv("IFS_WORKER_INTERVAL_SECONDS", "3")
    get_settings.cache_clear()
    slept.clear()
    with pytest.raises(_StopLoop):
        worker.run_worker()
    assert slept == [3]

    slept.clear()
    with pytest.raises(_StopLoop):
        worker.run_worker(interval_seconds=7)
    assert slept == [7]


def test_process_outbox_batch_size_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from ifs.worker import process_outbox

    with session_scope() as db:
        for index in range(5):
            db.add(Outbox(event_type="test.event", payload={"index": index}))
        db.flush()

    monkeypatch.setenv("IFS_WORKER_OUTBOX_BATCH_SIZE", "2")
    get_settings.cache_clear()

    assert process_outbox() == 2
    with session_scope() as db:
        first_batch = db.execute(
            select(Outbox).where(Outbox.published_at.isnot(None))
        ).scalars().all()
    # Ein Zeitstempel pro Batch: beide Zeilen des ersten Batches sind identisch.
    assert len(first_batch) == 2
    assert first_batch[0].published_at == first_batch[1].published_at

    assert process_outbox() == 2
    assert process_outbox() == 1
    assert process_outbox() == 0


def test_purge_trash_keeps_entries_with_trashed_parent() -> None:
    from ifs.worker import purge_trash

    old = utcnow() - timedelta(days=40)
    recent = utcnow() - timedelta(days=1)
    with session_scope() as db:
        user = User(username="trash-opt", password_hash=hash_password("geheim123"))
        db.add(user)
        db.flush()
        root = Entry(name="/", type=EntryType.folder, owner_id=user.id)
        db.add(root)
        db.flush()
        old_folder = Entry(
            name="alt",
            type=EntryType.folder,
            owner_id=user.id,
            parent_id=root.id,
            trashed_at=old,
        )
        recent_folder = Entry(
            name="neu",
            type=EntryType.folder,
            owner_id=user.id,
            parent_id=root.id,
            trashed_at=recent,
        )
        db.add_all([old_folder, recent_folder])
        db.flush()  # IDs erst nach dem Flush verfügbar (default=uuid7)
        old_child = Entry(
            name="kind.txt",
            type=EntryType.file,
            owner_id=user.id,
            parent_id=old_folder.id,
            trashed_at=old,
        )
        old_child_of_recent = Entry(
            name="kind2.txt",
            type=EntryType.file,
            owner_id=user.id,
            parent_id=recent_folder.id,
            trashed_at=old,
        )
        db.add_all([old_child, old_child_of_recent])
        db.flush()
        old_folder_id = old_folder.id
        old_child_id = old_child.id
        recent_folder_id = recent_folder.id
        old_child_of_recent_id = old_child_of_recent.id

    # Nur die Wurzel des alten Papierkorb-Teilbaums zählt; das Kind folgt per Kaskade.
    assert purge_trash() == 1

    with session_scope() as db:
        assert db.get(Entry, old_folder_id) is None
        assert db.get(Entry, old_child_id) is None
        # Parent selbst noch im Papierkorb (nicht alt genug): Kind bleibt erhalten.
        assert db.get(Entry, recent_folder_id) is not None
        assert db.get(Entry, old_child_of_recent_id) is not None


def test_token_redaction_filter_skips_unrelated_log_lines() -> None:
    from ifs.cli import _TokenRedactionFilter

    redactor = _TokenRedactionFilter()

    def make_record(url: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/1.1" %s',
            args=("127.0.0.1", "GET", url, "200"),
            exc_info=None,
        )

    # Ohne Schlüsselwörter bleibt das args-Tupel unverändert (kein Regex-Scan).
    untouched = make_record("/api/entries/123")
    original_args = untouched.args
    assert redactor.filter(untouched) is True
    assert untouched.args is original_args
    assert untouched.args[2] == "/api/entries/123"

    # Freigabe-Token wird weiterhin unkenntlich gemacht.
    shares = make_record("/api/shares/tok3n-value?download=true")
    assert redactor.filter(shares) is True
    assert shares.args[2] == "/api/shares/<redacted>?download=true"

    # Passwort in Query-Strings wird weiterhin redigiert.
    password = make_record("/api/entries?password=secret")
    assert redactor.filter(password) is True
    assert password.args[2] == "/api/entries?password=<redacted>"

    # Archiv-Jobs (mehrsegmentiger Pfad) ebenfalls.
    archive = make_record("/api/archive/jobs/abc.def")
    assert redactor.filter(archive) is True
    assert archive.args[2] == "/api/archive/jobs/<redacted>"
