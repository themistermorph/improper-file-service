"""Tests für den Papierkorb (Namespace und API)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content, namespace
from ifs.errors import Conflict
from ifs.models import Blob, BlobStatus, Entry

from .conftest import make_blob, make_user


def test_soft_delete_marks_subtree(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    blob = make_blob(db, b"inhalt")
    file_entry = namespace.apply_write(db, folder, "a.txt", user.id, blob)

    namespace.soft_delete(db, folder, user.id)

    assert folder.trashed_at is not None
    assert folder.trashed_by == user.id
    assert folder.original_parent_id == root.id
    assert file_entry.trashed_at is not None


def test_list_trash_top_level_only(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    blob = make_blob(db, b"x")
    namespace.apply_write(db, folder, "a.txt", user.id, blob)
    namespace.soft_delete(db, folder, user.id)

    trash = namespace.list_trash(db, user)
    assert [e.id for e in trash] == [folder.id]
    files, size = namespace.subtree_stats(db, folder)
    assert files == 1 and size == 1


def test_restore_restores_subtree(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    blob = make_blob(db, b"hi")
    file_entry = namespace.apply_write(db, folder, "a.txt", user.id, blob)
    namespace.soft_delete(db, folder, user.id)

    namespace.restore(db, folder, user.id)

    assert folder.trashed_at is None
    assert file_entry.trashed_at is None
    assert namespace.resolve_path(db, "/docs/a.txt", root).id == file_entry.id


def test_restore_name_conflict(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    docs = namespace.create_folder(db, root, "docs", user.id)
    namespace.soft_delete(db, docs, user.id)

    namespace.create_folder(db, root, "docs", user.id)  # neuer, aktiver Ordner

    with pytest.raises(Conflict):
        namespace.restore(db, docs, user.id)

    namespace.restore(db, docs, user.id, new_name="docs-alt")
    assert docs.name == "docs-alt"
    assert docs.trashed_at is None


def test_purge_removes_subtree(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    blob = make_blob(db, b"x")
    file_entry = namespace.apply_write(db, folder, "a.txt", user.id, blob)
    file_id = file_entry.id
    namespace.soft_delete(db, folder, user.id)

    files = namespace.purge(db, folder)
    assert files == 1
    assert db.get(Entry, file_id) is None
    assert namespace.resolve_path(db, "/docs", root) is None


# --- API ------------------------------------------------------------------


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
    payload = b"hello trash"
    return {"Body": io.BytesIO(payload), "ContentLength": len(payload)}


def test_trash_api_flow(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        folder_id = client.post(
            "/api/folders", headers=headers, json={"name": "trashme"}
        ).json()["id"]
        entry_id = client.put(
            f"/api/uploads/simple?parent_id={folder_id}&name=a.txt",
            headers=headers,
            content=b"hello trash",
        ).json()["id"]

        # Löschen -> Papierkorb
        assert client.delete(f"/api/entries/{folder_id}", headers=headers).status_code == 204

        listing = client.get("/api/trash", headers=headers).json()
        assert len(listing) == 1
        item = listing[0]
        assert item["name"] == "trashme"
        assert item["path"] == "/trashme"
        assert item["files"] == 1
        assert item["size"] == len(b"hello trash")
        assert item["days_left"] >= 28
        assert client.get("/api/trash/count", headers=headers).json()["count"] == 1

        # Wiederherstellen
        restored = client.post(f"/api/trash/{folder_id}/restore", headers=headers, json={})
        assert restored.status_code == 200
        assert restored.json()["path"] == "/trashme"
        assert client.get("/api/trash/count", headers=headers).json()["count"] == 0
        assert client.get(f"/api/entries/{entry_id}", headers=headers).status_code == 200

        # Erneut löschen und endgültig entfernen
        client.delete(f"/api/entries/{folder_id}", headers=headers)
        assert client.delete(f"/api/trash/{folder_id}", headers=headers).status_code == 204
        assert client.get("/api/trash", headers=headers).json() == []
        assert client.get(f"/api/entries/{folder_id}", headers=headers).status_code == 404


def _login(client) -> str:
    return client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]


def _make_trashed_folder(client, headers, name):
    folder_id = client.post("/api/folders", headers=headers, json={"name": name}).json()["id"]
    entry_id = client.put(
        f"/api/uploads/simple?parent_id={folder_id}&name=a.txt",
        headers=headers,
        content=f"hello {name}".encode(),
    ).json()["id"]
    assert client.delete(f"/api/entries/{folder_id}", headers=headers).status_code == 204
    return folder_id, entry_id


def test_bulk_restore_trash(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        first, first_file = _make_trashed_folder(client, headers, "bulk-restore-1")
        second, _second_file = _make_trashed_folder(client, headers, "bulk-restore-2")

        response = client.post(
            "/api/trash/bulk/restore", headers=headers, json={"ids": [first, second]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] == 2
        assert body["failed"] == []

        assert client.get("/api/trash/count", headers=headers).json()["count"] == 0
        assert client.get(f"/api/entries/{first}", headers=headers).status_code == 200
        assert client.get(f"/api/entries/{second}", headers=headers).status_code == 200
        assert client.get(f"/api/entries/{first_file}", headers=headers).status_code == 200


def test_bulk_purge_trash(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        first, first_file = _make_trashed_folder(client, headers, "bulk-purge-1")
        second, _ = _make_trashed_folder(client, headers, "bulk-purge-2")

        response = client.post(
            "/api/trash/bulk/purge", headers=headers, json={"ids": [first, second]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] == 2
        assert body["files"] == 2
        assert body["failed"] == []

        assert client.get("/api/trash", headers=headers).json() == []
        assert client.get(f"/api/entries/{first_file}", headers=headers).status_code == 404


def test_bulk_restore_reports_failures(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        trashed, _ = _make_trashed_folder(client, headers, "bulk-mixed")
        active = client.post("/api/folders", headers=headers, json={"name": "aktiv"}).json()["id"]

        response = client.post(
            "/api/trash/bulk/restore", headers=headers, json={"ids": [trashed, active]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] == 1
        assert body["failed"] == [active]
        assert client.get(f"/api/entries/{trashed}", headers=headers).status_code == 200
        assert client.get("/api/trash/count", headers=headers).json()["count"] == 0
