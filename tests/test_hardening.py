"""Tests für die Härtungen 6–11 (Proxy-IP, Info-Endpunkte, ValueError, Plausibilität, Shares)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.requests import Request

from ifs import main
from ifs.api import deps
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"data")


def _request_with(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


def test_client_ip_respects_trusted_proxies(monkeypatch):
    # Ohne vertrauenswürdige Proxies zählt die Peer-IP (XFF wird ignoriert)
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(trusted_proxies=""))
    assert deps.client_ip(_request_with("10.0.0.5", "1.2.3.4")) == "10.0.0.5"

    # Peer ist vertrauenswürdig -> XFF wird genutzt
    monkeypatch.setattr(
        deps, "get_settings", lambda: SimpleNamespace(trusted_proxies="10.0.0.0/8")
    )
    assert deps.client_ip(_request_with("10.0.0.5", "1.2.3.4")) == "1.2.3.4"

    # Nicht vertrauenswürdiger Peer -> XFF bleibt ignoriert
    assert deps.client_ip(_request_with("192.168.1.9", "1.2.3.4")) == "192.168.1.9"


def test_info_endpoints_locked_down():
    with TestClient(main.app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/docs").status_code == 404  # expose_docs default False
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/metrics").status_code == 401

        admin = {"Authorization": f"Bearer {login(client)}"}
        assert client.get("/metrics", headers=admin).status_code == 200

        client.post("/api/users", headers=admin, json={"username": "meter", "password": "geheim123"})
        user = {"Authorization": f"Bearer {login(client, 'meter', 'geheim123')}"}
        assert client.get("/metrics", headers=user).status_code == 403


def test_invalid_range_and_offset_do_not_500(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "r"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        ).json()

        bad_range = client.get(
            f"/api/entries/{entry['id']}/content", headers={**auth, "Range": "bytes=abc-def"}
        )
        assert bad_range.status_code == 416

        session = client.post(
            "/api/uploads", headers=auth, json={"parent_id": folder, "name": "u.txt"}
        ).json()
        bad_offset = client.patch(
            f"/api/uploads/{session['id']}", headers={**auth, "Upload-Offset": "abc"}, content=b"x"
        )
        assert bad_offset.status_code == 400


def test_principal_and_copy_plausibility(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        parent = client.post("/api/folders", headers=auth, json={"name": "p"}).json()["id"]
        child = client.post(
            "/api/folders", headers=auth, json={"parent_id": parent, "name": "c"}
        ).json()["id"]

        bogus = str(uuid4())
        acl = client.post(
            f"/api/entries/{parent}/acl",
            headers=auth,
            json={"principal_type": "user", "principal_id": bogus, "perms": ["read"]},
        )
        assert acl.status_code == 400

        role = client.get("/api/roles?scope=resource", headers=auth).json()[0]["id"]
        assign = client.post(
            f"/api/entries/{parent}/roles",
            headers={**auth, "Content-Type": "application/json"},
            json={"role_id": role, "principal_type": "user", "principal_id": bogus},
        )
        assert assign.status_code == 400

        # Ordner in sich selbst kopieren -> 400
        copy = client.post(
            f"/api/entries/{parent}/copy",
            headers=auth,
            json={"parent_id": child, "name": "x"},
        )
        assert copy.status_code == 400


def test_shares_revoked_on_deactivation(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        carol = client.post(
            "/api/users", headers=admin, json={"username": "carol", "password": "geheim123", "is_admin": True}
        ).json()["id"]
        carol_auth = {"Authorization": f"Bearer {login(client, 'carol', 'geheim123')}"}

        folder = client.post("/api/folders", headers=carol_auth, json={"name": "cs"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=carol_auth,
            content=b"data",
        ).json()
        token = client.post(
            "/api/shares", headers=carol_auth, json={"entry_id": entry["id"]}
        ).json()["token"]

        assert any(s["token"] == token for s in client.get("/api/shares", headers=admin).json())
        assert client.post(f"/api/users/{carol}/deactivate", headers=admin).status_code == 204
        assert all(s["token"] != token for s in client.get("/api/shares", headers=admin).json())
