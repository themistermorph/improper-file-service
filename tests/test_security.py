"""Sicherheitstests: JWT-Zweck, Header-Injection, sichere Defaults."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ifs import main
from ifs.config import Settings
from ifs.core import content
from ifs.utils import content_disposition, safe_mime

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"data")


def test_scoped_tokens_are_not_access_tokens(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        access = login(client)
        auth = {"Authorization": f"Bearer {access}"}
        assert client.get("/api/me", headers=auth).status_code == 200

        folder = client.post("/api/folders", headers=auth, json={"name": "sec"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        ).json()

        preview = client.post(f"/api/entries/{entry['id']}/preview-token", headers=auth).json()
        archive = client.post(f"/api/entries/{entry['id']}/archive-token", headers=auth).json()

        for scoped in (preview["token"], archive["token"]):
            bearer = {"Authorization": f"Bearer {scoped}"}
            # Zweckfremde Tokens dürfen NICHT als Access-Token funktionieren
            assert client.get("/api/me", headers=bearer).status_code == 401
            assert client.get("/api/entries", headers=bearer).status_code == 401

        # Der zugehörige Zweck funktioniert weiterhin (Token in der URL)
        assert client.get(preview["url"]).status_code == 200
        assert client.get(archive["url"]).status_code == 200


def test_filename_and_mime_header_injection(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "hdr"}).json()["id"]

        # Steuerzeichen im Dateinamen werden abgelehnt
        response = client.put(
            "/api/uploads/simple",
            headers=auth,
            params={"parent_id": folder, "name": "bad\nname.txt"},
            content=b"x",
        )
        assert response.status_code == 400

        # MIME mit CRLF wird neutralisiert
        entry = client.put(
            "/api/uploads/simple",
            headers=auth,
            params={"parent_id": folder, "name": "note.txt", "mime": "text/plain\r\nX-Injected: yes"},
            content=b"hello",
        ).json()
        detail = client.get(f"/api/entries/{entry['id']}", headers=auth).json()
        assert detail["mime"] == "application/octet-stream"

        download = client.get(
            f"/api/entries/{entry['id']}/content", headers=auth, params={"download": "true"}
        )
        assert download.status_code == 200
        assert "X-Injected" not in download.headers
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith('attachment; filename="')

        # Anführungszeichen im Namen bleiben erlaubt, brechen den Header aber nicht aus
        quoted = client.put(
            "/api/uploads/simple",
            headers=auth,
            params={"parent_id": folder, "name": 're"port.txt'},
            content=b"ok",
        ).json()
        assert quoted["name"] == 're"port.txt'
        head = client.get(f"/api/entries/{quoted['id']}/content", headers=auth, params={"download": "true"})
        disposition = head.headers["content-disposition"]
        assert "filename*=UTF-8''" in disposition
        assert "\r" not in disposition and "\n" not in disposition


def test_utils_header_helpers():
    assert safe_mime("text/plain") == "text/plain"
    assert safe_mime("text/plain\r\nX: y") == "application/octet-stream"
    assert safe_mime("nonsense") == "application/octet-stream"
    assert safe_mime(None) == "application/octet-stream"
    # MIME-Typen sind case-insensitiv und werden normalisiert (siehe XSS-Fix).
    assert safe_mime("TeXt/HtMl") == "text/html"
    assert safe_mime("  IMAGE/SVG+XML ") == "image/svg+xml"

    header = content_disposition('a"b\r\nc.txt')
    assert "\r" not in header and "\n" not in header
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header


def test_mime_case_does_not_bypass_active_content_protection(monkeypatch):
    """Gemischte Groß-/Kleinschreibung im MIME-Typ darf Attachment/Sandbox nicht aushebeln."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "case"}).json()["id"]

        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=evil.html&mime=TeXt/HtMl",
            headers=auth,
            content=b"<html><script>alert(1)</script></html>",
        ).json()
        detail = client.get(f"/api/entries/{entry['id']}", headers=auth).json()
        assert detail["mime"] == "text/html"

        response = client.get(f"/api/entries/{entry['id']}/content", headers=auth)
        assert response.headers["content-type"] == "text/html"
        assert response.headers.get("content-disposition", "").startswith("attachment")
        assert "sandbox" in response.headers.get("content-security-policy", "")

        # Der login-freie Vorschau-Link muss ebenfalls sandboxen.
        preview = client.post(
            f"/api/entries/{entry['id']}/preview-token", headers=auth
        ).json()
        preview_response = client.get(preview["url"])
        assert preview_response.headers["content-type"] == "text/html"
        assert "sandbox" in preview_response.headers.get("content-security-policy", "")

        # Gleiches gilt für SVG mit gemischt geschriebenem MIME-Typ
        # (%2B = '+', sonst dekodiert die Query den Plus als Leerzeichen).
        svg = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=x.svg&mime=ImAgE/SvG%2BXML",
            headers=auth,
            content=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        ).json()
        svg_response = client.get(f"/api/entries/{svg['id']}/content", headers=auth)
        assert svg_response.headers["content-type"] == "image/svg+xml"
        assert svg_response.headers.get("content-disposition", "").startswith("attachment")


def test_access_log_token_redaction_covers_archive_jobs():
    import logging

    from ifs.cli import _TokenRedactionFilter

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %s',
        args=(
            "127.0.0.1:1234",
            "GET",
            "/api/archive/jobs/eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "HTTP/1.1",
            200,
        ),
        exc_info=None,
    )
    assert _TokenRedactionFilter().filter(record) is True
    logged_path = record.args[2]
    assert "eyJhbGciOiJIUzI1NiJ9" not in logged_path
    assert "/api/archive/jobs/<redacted>" in logged_path


def test_production_requires_strong_secrets():
    with pytest.raises(ValidationError):
        Settings(environment="prod", jwt_secret="short", admin_password="strongpass", s3_secret_key="s3secret")
    with pytest.raises(ValidationError):
        Settings(environment="prod", jwt_secret="x" * 40, admin_password="admin", s3_secret_key="s3secret")
    with pytest.raises(ValidationError):
        Settings(environment="prod", jwt_secret="x" * 40, admin_password="strongpass", s3_secret_key="minioadmin")

    # Unsichere JWT-Algorithmen werden abgelehnt bzw. auf HS256 korrigiert.
    with pytest.raises(ValidationError):
        Settings(
            environment="prod",
            jwt_secret="x" * 40,
            admin_password="strongpass",
            s3_secret_key="s3secret",
            jwt_algorithm="none",
        )
    assert Settings(jwt_secret="x" * 40, jwt_algorithm="none").jwt_algorithm == "HS256"

    settings = Settings(
        environment="prod",
        jwt_secret="x" * 40,
        admin_password="strongpass",
        s3_secret_key="s3secret",
        database_url="postgresql+psycopg://ifs:strong-db-pass@db:5432/ifs",
    )
    assert settings.is_production is True

    # Default-DB-Zugangsdaten werden in Produktion abgelehnt.
    with pytest.raises(ValidationError):
        Settings(
            environment="prod",
            jwt_secret="x" * 40,
            admin_password="strongpass",
            s3_secret_key="s3secret",
            database_url="postgresql+psycopg://ifs:ifs@db:5432/ifs",
        )
