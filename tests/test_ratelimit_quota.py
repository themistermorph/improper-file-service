"""Tests für Brute-Force-Schutz, Benutzer-Enumeration und Quota/Upload-Limits."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pyftpdlib.exceptions import AuthenticationFailed

from ifs import main
from ifs.core import content, ratelimit
from ifs.ftp.server import IFSAuthorizer

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"data")


def test_login_rate_limit_and_no_enumeration(monkeypatch):
    ratelimit.install(
        ratelimit.LoginRateLimiter(2, 60, 30, 60),
        ratelimit.LoginRateLimiter(1000, 60, 30, 60),
    )
    calls: list = []

    def spy(password_hash, password):
        calls.append(password_hash)
        return False

    monkeypatch.setattr("ifs.api.auth.verify_password_safe", spy)

    with TestClient(main.app) as client:
        for _ in range(2):
            response = client.post(
                "/api/auth/login", json={"username": "ghost", "password": "x"}
            )
            assert response.status_code == 401

        # Dritter Versuch ist gesperrt (429 mit Retry-After)
        blocked = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
        assert blocked.status_code == 429
        assert blocked.headers.get("retry-after")

    # Auch für unbekannte Nutzer wird ein Hash-Vergleich ausgeführt (kein Timing-Leak).
    assert calls and all(value is None for value in calls)


def test_quota_enforced(monkeypatch):
    monkeypatch.setattr(
        "ifs.core.quota.get_settings", lambda: SimpleNamespace(default_quota_bytes=10)
    )
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "q"}).json()["id"]

        too_big = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=big.bin",
            headers=auth,
            content=b"x" * 20,
        )
        assert too_big.status_code == 413

        ok = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=small.txt", headers=auth, content=b"12345"
        )
        assert ok.status_code == 201

        second = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=more.txt", headers=auth, content=b"123456"
        )
        assert second.status_code == 413

        info = client.get("/api/quota", headers=auth).json()
        assert info == {"limit_bytes": 10, "usage_bytes": 5}


def test_upload_size_limit_rejected_early(monkeypatch):
    monkeypatch.setattr(
        "ifs.api.uploads.get_settings", lambda: SimpleNamespace(max_upload_size=5)
    )
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "lim"}).json()["id"]

        response = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=big.txt", headers=auth, content=b"x" * 10
        )
        assert response.status_code == 413
        # Keine Datei angelegt
        assert client.get(f"/api/entries?parent_id={folder}", headers=auth).json() == []

        # Resumable: zu große angekündigte Größe wird sofort abgelehnt
        session = client.post(
            "/api/uploads",
            headers=auth,
            json={"parent_id": folder, "name": "big2.txt", "size": 100},
        )
        assert session.status_code == 413


def test_ftp_authentication_uses_dummy_hash(monkeypatch):
    calls: list = []

    def spy(password_hash, password):
        calls.append(password_hash)
        return False

    monkeypatch.setattr("ifs.ftp.server.verify_password_safe", spy)
    authorizer = IFSAuthorizer()
    handler = SimpleNamespace(remote_ip="10.0.0.9")

    with pytest.raises(AuthenticationFailed):
        authorizer.validate_authentication("nobody", "secret", handler)

    assert calls == [None]
