"""Tests für Freigaben-Übersicht und Audit-Log."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content
from ifs.models import Blob, BlobStatus

PAYLOAD = b"freigabe"


def _fake_store_blob(db, local_path, mime=None):
    import hashlib

    data = open(local_path, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}", sha256=sha, size=len(data), status=BlobStatus.ready
    )
    db.add(blob)
    db.flush()
    return blob


def _fake_get_object(key, start=None, end=None):
    return {"Body": io.BytesIO(PAYLOAD), "ContentLength": len(PAYLOAD)}


def _login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def test_share_overview_and_revoke(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "share-me"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=PAYLOAD,
        ).json()

        created = client.post("/api/shares", headers=admin, json={"entry_id": entry["id"]}).json()
        token = created["token"]

        listing = client.get("/api/shares", headers=admin).json()
        item = next(s for s in listing if s["token"] == token)
        assert item["entry_name"] == "f.txt"
        assert item["entry_path"] == "/share-me/f.txt"
        assert item["created_by_username"] == "admin"

        # Nicht-Admin sieht fremde Freigaben nicht
        client.post(
            "/api/users", headers=admin, json={"username": "sam", "password": "geheim123"}
        )
        sam = {"Authorization": f"Bearer {_login(client, 'sam', 'geheim123')}"}
        assert client.get("/api/shares", headers=sam).json() == []

        # Widerrufen
        assert client.delete(f"/api/shares/{token}", headers=admin).status_code == 204
        assert all(s["token"] != token for s in client.get("/api/shares", headers=admin).json())


def test_share_update_and_revoke(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "upd"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=PAYLOAD,
        ).json()
        token = client.post("/api/shares", headers=admin, json={"entry_id": entry["id"]}).json()["token"]

        # Passwort nachträglich setzen
        updated = client.patch(f"/api/shares/{token}", headers=admin, json={"password": "geheim"})
        assert updated.status_code == 200
        assert updated.json()["has_password"] is True
        assert client.get(f"/api/shares/{token}/content").status_code == 401
        assert client.get(f"/api/shares/{token}/content?password=geheim").status_code == 200

        # Passwort entfernen
        assert client.patch(
            f"/api/shares/{token}", headers=admin, json={"password": ""}
        ).json()["has_password"] is False
        assert client.get(f"/api/shares/{token}/content").status_code == 200

        # Limit setzen
        assert client.patch(
            f"/api/shares/{token}", headers=admin, json={"max_downloads": 10}
        ).json()["max_downloads"] == 10

        # Widerrufen -> Link ist tot
        assert client.delete(f"/api/shares/{token}", headers=admin).status_code == 204
        assert client.get(f"/api/shares/{token}").status_code == 404
        assert client.get(f"/api/shares/{token}/content").status_code == 404


def test_delete_entry_revokes_links_automatically(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "del"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=PAYLOAD,
        ).json()
        token = client.post("/api/shares", headers=admin, json={"entry_id": entry["id"]}).json()["token"]

        # Datei löschen -> Link wird automatisch widerrufen
        assert client.delete(f"/api/entries/{entry['id']}", headers=admin).status_code == 204
        assert all(s["token"] != token for s in client.get("/api/shares", headers=admin).json())
        assert client.get(f"/api/shares/{token}").status_code == 404
        assert client.get(f"/api/shares/{token}/content").status_code == 404


def test_revoke_share_of_trashed_entry(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    from uuid import UUID

    from ifs.db import session_scope
    from ifs.models import Entry
    from ifs.utils import utcnow

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "legacy"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=PAYLOAD,
        ).json()
        token = client.post("/api/shares", headers=admin, json={"entry_id": entry["id"]}).json()["token"]

        # Altbestand simulieren: Eintrag ist im Papierkorb, Link existiert noch
        with session_scope() as db:
            db.get(Entry, UUID(entry["id"])).trashed_at = utcnow()

        # Widerruf muss trotz gelöschtem Eintrag funktionieren
        assert client.delete(f"/api/shares/{token}", headers=admin).status_code == 204
        assert client.get(f"/api/shares/{token}").status_code == 404


def test_share_folder_downloads_as_zip(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "team"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=a.txt",
            headers=admin,
            content=PAYLOAD,
        )
        sub = client.post(
            "/api/folders", headers=admin, json={"parent_id": folder, "name": "sub"}
        ).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={sub}&name=b.txt",
            headers=admin,
            content=PAYLOAD + b" zwei",
        )

        token = client.post("/api/shares", headers=admin, json={"entry_id": folder}).json()["token"]

        response = client.get(f"/api/shares/{token}/content")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-disposition"].endswith(".zip\"") or ".zip" in response.headers["content-disposition"]

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        assert "team/" in names
        assert "team/a.txt" in names
        assert "team/sub/" in names
        assert "team/sub/b.txt" in names

        # Dateiname im Pfad – damit `curl -O` als .zip speichert
        named = client.get(f"/api/shares/{token}/content/team.zip")
        assert named.status_code == 200
        assert named.headers["content-type"] == "application/zip"
        assert ".zip" in named.headers["content-disposition"]

        # Download-Zähler wurde erhöht (zwei Abrufe)
        assert client.get(f"/api/shares/{token}").json()["downloads"] == 2


def test_share_folder_upload(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "drop"}).json()["id"]
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder, "allow_upload": True}
        ).json()["token"]

        detail = next(
            s for s in client.get("/api/shares", headers=admin).json() if s["token"] == token
        )
        assert detail["allow_upload"] is True
        assert detail["overwrite"] is False
        assert detail["entry_type"] == "folder"

        first = client.post(f"/api/shares/{token}/upload?name=drop.txt", content=b"eins")
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "drop.txt"

        # Namenskonflikt -> automatisch umbenannt (kein Überschreiben)
        second = client.post(f"/api/shares/{token}/upload?name=drop.txt", content=b"zwei")
        assert second.status_code == 201
        assert second.json()["name"] == "drop (2).txt"

        # Der Request-Parameter `overwrite` allein darf nicht überschreiben.
        attempted = client.post(
            f"/api/shares/{token}/upload?name=drop.txt&overwrite=true", content=b"drei"
        )
        assert attempted.json()["name"] == "drop (3).txt"

        # Erst die Freigabe-Eigenschaft erlaubt das Ersetzen.
        patched = client.patch(
            f"/api/shares/{token}", headers=admin, json={"overwrite": True}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["overwrite"] is True
        forced = client.post(
            f"/api/shares/{token}/upload?name=drop.txt", content=b"vier"
        )
        assert forced.json()["name"] == "drop.txt"

        # Upload erscheint in der ZIP-Freigabe
        response = client.get(f"/api/shares/{token}/content")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        assert "drop/drop.txt" in names
        assert "drop/drop (2).txt" in names
        assert "drop/drop (3).txt" in names


def test_share_upload_requires_permission(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "closed"}).json()["id"]
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder}
        ).json()["token"]
        # Ohne allow_upload verboten
        assert client.post(
            f"/api/shares/{token}/upload?name=x.txt", content=b"x"
        ).status_code == 403

        # Datei-Freigabe: Upload nicht möglich, auch wenn erlaubt
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=b"y",
        ).json()
        ftoken = client.post(
            "/api/shares", headers=admin, json={"entry_id": entry["id"], "allow_upload": True}
        ).json()["token"]
        assert client.post(
            f"/api/shares/{ftoken}/upload?name=z.txt", content=b"z"
        ).status_code == 400


def test_share_upload_password_protected(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "pwdrop"}).json()["id"]
        token = client.post(
            "/api/shares",
            headers=admin,
            json={"entry_id": folder, "allow_upload": True, "password": "geheim"},
        ).json()["token"]

        assert client.post(
            f"/api/shares/{token}/upload?name=a.txt", content=b"a"
        ).status_code == 401
        ok = client.post(
            f"/api/shares/{token}/upload?name=a.txt",
            headers={"X-Share-Password": "geheim"},
            content=b"a",
        )
        assert ok.status_code == 201
        assert ok.json()["name"] == "a.txt"


def test_bulk_revoke_shares(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client, 'admin', 'admin')}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "bulk-share"}).json()["id"]
        first = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=a.txt", headers=admin, content=b"eins"
        ).json()
        second = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=b.txt", headers=admin, content=b"zwei"
        ).json()
        t1 = client.post("/api/shares", headers=admin, json={"entry_id": first["id"]}).json()["token"]
        t2 = client.post("/api/shares", headers=admin, json={"entry_id": second["id"]}).json()["token"]

        response = client.post(
            "/api/shares/bulk/revoke", headers=admin, json={"tokens": [t1, t2, "unbekannt"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] == 2
        assert body["failed"] == ["unbekannt"]

        remaining = [s["token"] for s in client.get("/api/shares", headers=admin).json()]
        assert t1 not in remaining and t2 not in remaining
        assert client.get(f"/api/shares/{t1}").status_code == 404


def test_audit_includes_actor_username():
    with TestClient(main.app) as client:
        token = _login(client, "admin", "admin")
        headers = {"Authorization": f"Bearer {token}"}
        rows = client.get("/api/audit?limit=50", headers=headers).json()
        login_events = [r for r in rows if r["action"] == "auth.login"]
        assert login_events
        assert login_events[0]["actor_username"] == "admin"

        filtered = client.get("/api/audit?action=user.create", headers=headers).json()
        assert all(r["action"] == "user.create" for r in filtered)
