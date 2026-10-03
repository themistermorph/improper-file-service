"""Regressionstests für die Findings des Sicherheits-Reports (H1, M1, M2, N1, N5)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"<html><body>hi</body></html>")


def test_h1_restore_requires_authorization(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id = client.post(
            "/api/users", headers=admin, json={"username": "bob", "password": "geheim123"}
        ).json()["id"]
        bob = {"Authorization": f"Bearer {login(client, 'bob', 'geheim123')}"}

        folder = client.post("/api/folders", headers=admin, json={"name": "shared"}).json()["id"]
        assert client.post(
            f"/api/entries/{folder}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["read", "write"]},
        ).status_code == 201
        file_entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=bob, content=b"data"
        ).json()

        # Admin (Eigentümer) löscht den Ordner
        assert client.delete(f"/api/entries/{folder}", headers=admin).status_code == 204

        # Bob darf weder die (verschachtelte) Datei noch den Ordner wiederherstellen
        nested = client.post(f"/api/trash/{file_entry['id']}/restore", headers=bob, json={})
        assert nested.status_code == 403
        top = client.post(f"/api/trash/{folder}/restore", headers=bob, json={})
        assert top.status_code == 403
        # Ordner weiterhin im Papierkorb
        assert client.get(f"/api/entries/{folder}", headers=admin).status_code == 404

        # Admin darf wiederherstellen
        assert client.post(
            f"/api/trash/{folder}/restore", headers=admin, json={}
        ).status_code == 200


def test_m1_active_content_forced_to_attachment(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "web"}).json()["id"]

        html = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=page.html",
            headers=auth,
            content=b"<html><script>alert(1)</script></html>",
        ).json()
        response = client.get(f"/api/entries/{html['id']}/content", headers=auth)
        assert response.headers["content-type"] == "text/html"
        assert response.headers.get("content-disposition", "").startswith("attachment")
        assert "sandbox" in response.headers.get("content-security-policy", "")

        svg = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=pic.svg",
            headers=auth,
            content=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        ).json()
        svg_resp = client.get(f"/api/entries/{svg['id']}/content", headers=auth)
        assert svg_resp.headers.get("content-disposition", "").startswith("attachment")

        # Unkritischer Typ bleibt inline
        txt = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=note.txt", headers=auth, content=b"hi"
        ).json()
        txt_resp = client.get(f"/api/entries/{txt['id']}/content", headers=auth)
        assert "content-disposition" not in {k.lower() for k in txt_resp.headers}


def test_m2_share_password_and_limit(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "s"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        ).json()

        token = client.post(
            "/api/shares",
            headers=auth,
            json={"entry_id": entry["id"], "password": "pw", "max_downloads": 1},
        ).json()["token"]

        # Passwort per Header (nicht mehr in der URL)
        wrong = client.get(f"/api/shares/{token}/content", headers={"X-Share-Password": "falsch"})
        assert wrong.status_code == 401
        ok = client.get(f"/api/shares/{token}/content", headers={"X-Share-Password": "pw"})
        assert ok.status_code == 200
        # Limit atomar durchgesetzt
        assert client.get(
            f"/api/shares/{token}/content", headers={"X-Share-Password": "pw"}
        ).status_code == 410

        # Brute-Force-Schutz auf Freigabe-Passwörter
        token2 = client.post(
            "/api/shares",
            headers=auth,
            json={"entry_id": entry["id"], "password": "pw2"},
        ).json()["token"]
        for _ in range(5):
            client.get(f"/api/shares/{token2}/content", headers={"X-Share-Password": "x"})
        blocked = client.get(f"/api/shares/{token2}/content", headers={"X-Share-Password": "pw2"})
        assert blocked.status_code == 429


def test_n1_scoped_tokens_invalid_after_password_change(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        token = login(client)
        auth = {"Authorization": f"Bearer {token}"}
        folder = client.post("/api/folders", headers=auth, json={"name": "t"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt", headers=auth, content=b"data"
        ).json()

        preview = client.post(f"/api/entries/{entry['id']}/preview-token", headers=auth).json()
        archive = client.post(f"/api/entries/{entry['id']}/archive-token", headers=auth).json()
        assert client.get(preview["url"]).status_code == 200

        # Passwort ändern -> token_version erhöht
        assert client.post(
            "/api/me/password",
            headers=auth,
            json={"current_password": "admin", "new_password": "neuespass9"},
        ).status_code == 204

        assert client.get(preview["url"]).status_code == 401
        assert client.get(archive["url"]).status_code == 401


def test_n5_upload_rechecks_target_permission(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id = client.post(
            "/api/users", headers=admin, json={"username": "bob2", "password": "geheim123"}
        ).json()["id"]
        bob = {"Authorization": f"Bearer {login(client, 'bob2', 'geheim123')}"}

        folder = client.post("/api/folders", headers=admin, json={"name": "toctou"}).json()["id"]
        client.post(
            f"/api/entries/{folder}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["read", "write"]},
        )
        session = client.post(
            "/api/uploads", headers=bob, json={"parent_id": folder, "name": "x.txt"}
        ).json()

        # Admin entfernt den Zielordner zwischen Init und Transfer
        client.delete(f"/api/entries/{folder}", headers=admin)
        patched = client.patch(
            f"/api/uploads/{session['id']}", headers={**bob, "Upload-Offset": "0"}, content=b"abc"
        )
        assert patched.status_code in (403, 404)
