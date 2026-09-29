"""Regressionstests für Review 5: Share-Upload-TOCTOU, Revoke-Audit, Validierung."""

from __future__ import annotations

import hashlib
import io
from uuid import UUID

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content, namespace
from ifs.db import session_scope
from ifs.models import Blob, BlobStatus


def _fake_store_blob(db, local_path, mime=None):
    data = open(local_path, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}", sha256=sha, size=len(data), status=BlobStatus.ready
    )
    db.add(blob)
    db.flush()
    return blob


def _fake_get_object(key, start=None, end=None):
    payload = b"payload"
    return {"Body": io.BytesIO(payload), "ContentLength": len(payload)}


def _make_blob(db, data: bytes) -> Blob:
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}", sha256=sha, size=len(data), status=BlobStatus.ready
    )
    db.add(blob)
    db.flush()
    return blob


def _login(client, username="admin", password="admin") -> str:
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def test_upload_toctou_renames_instead_of_overwrite(monkeypatch):
    """Konkurrierende Datei während des Streamens -> Umbenennen statt Überschreiben."""
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "race"}).json()["id"]
        token = client.post(
            "/api/shares",
            headers=admin,
            json={"entry_id": folder, "allow_upload": True, "overwrite": False},
        ).json()["token"]

        folder_id = UUID(folder)
        detail = next(
            item
            for item in client.get("/api/shares", headers=admin).json()
            if item["token"] == token
        )
        admin_id = UUID(detail["created_by"])
        competing = b"konkurrenz"

        def racing_store_blob(db, local_path, mime=None):
            """Legt kurz vor dem Blob-Anlegen eine gleichnamige Datei parallel an."""
            with session_scope() as other:
                parent = namespace.get_entry(other, folder_id)
                blob = _make_blob(other, competing)
                namespace.apply_write(other, parent, "x.txt", admin_id, blob, "text/plain")
            return _fake_store_blob(db, local_path, mime)

        monkeypatch.setattr(content, "store_blob", racing_store_blob)

        # `x.txt` existiert beim Start noch nicht; es entsteht erst mittendrin.
        response = client.post(f"/api/shares/{token}/upload?name=x.txt", content=b"neu")
        assert response.status_code == 201, response.text
        assert response.json()["name"] == "x (2).txt"

        entries = {
            item["name"]: item
            for item in client.get(f"/api/entries?parent_id={folder}", headers=admin).json()
        }
        # Beide Dateien existieren; die konkurrierende Datei blieb unverändert.
        assert set(entries) == {"x.txt", "x (2).txt"}
        assert entries["x.txt"]["sha256"] == hashlib.sha256(competing).hexdigest()
        assert entries["x (2).txt"]["sha256"] == hashlib.sha256(b"neu").hexdigest()


def test_delete_share_writes_audit_entry(monkeypatch):
    """`DELETE /api/shares/{token}` muss als `share.revoke` auditieren."""
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post(
            "/api/folders", headers=admin, json={"name": "audit-share"}
        ).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=b"inhalt",
        ).json()
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": entry["id"]}
        ).json()["token"]

        assert client.delete(f"/api/shares/{token}", headers=admin).status_code == 204

        rows = client.get("/api/audit?action=share.revoke", headers=admin).json()
        revoked = [row for row in rows if row["target_entry"] == entry["id"]]
        assert len(revoked) == 1
        assert revoked[0]["actor_username"] == "admin"
        assert revoked[0]["details"]["token_prefix"] == token[:8]


def test_negative_max_downloads_rejected(monkeypatch):
    """Negatives `max_downloads` wird bei Create und PATCH mit 422 abgelehnt."""
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "limit"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=f.txt",
            headers=admin,
            content=b"x",
        ).json()

        created = client.post(
            "/api/shares",
            headers=admin,
            json={"entry_id": entry["id"], "max_downloads": -1},
        )
        assert created.status_code == 422

        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": entry["id"]}
        ).json()["token"]
        patched = client.patch(
            f"/api/shares/{token}", headers=admin, json={"max_downloads": -1}
        )
        assert patched.status_code == 422


def test_overwrite_share_replaces_existing_file(monkeypatch):
    """`overwrite=true` an der Freigabe ersetzt weiterhin bestehende Dateien."""
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "over"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=keep.txt",
            headers=admin,
            content=b"alt",
        )
        token = client.post(
            "/api/shares",
            headers=admin,
            json={"entry_id": folder, "allow_upload": True, "overwrite": True},
        ).json()["token"]

        replaced = client.post(
            f"/api/shares/{token}/upload?name=keep.txt", content=b"neuer Inhalt"
        )
        assert replaced.status_code == 201, replaced.text
        assert replaced.json()["name"] == "keep.txt"
        assert replaced.json()["size"] == len(b"neuer Inhalt")

        entries = client.get(f"/api/entries?parent_id={folder}", headers=admin).json()
        assert [item["name"] for item in entries] == ["keep.txt"]
        assert entries[0]["size"] == len(b"neuer Inhalt")
