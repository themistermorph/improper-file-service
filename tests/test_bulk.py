"""Tests für Bulk-Aktionen (Verschieben, Löschen, Sammel-ZIP, Ordnerliste)."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"bulk")


def test_bulk_move_delete_and_archive(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}

        src = client.post("/api/folders", headers=headers, json={"name": "src"}).json()["id"]
        dst = client.post("/api/folders", headers=headers, json={"name": "dst"}).json()["id"]
        f1 = client.put(
            f"/api/uploads/simple?parent_id={src}&name=a.txt", headers=headers, content=b"a"
        ).json()["id"]
        f2 = client.put(
            f"/api/uploads/simple?parent_id={src}&name=b.txt", headers=headers, content=b"bb"
        ).json()["id"]

        # Ordnerliste enthält src und dst
        folders = client.get("/api/folders", headers=headers).json()
        paths = {folder["path"] for folder in folders}
        assert {"/src", "/dst"} <= paths

        # Bulk-Verschieben
        moved = client.post(
            "/api/bulk/move", headers=headers, json={"ids": [f1, f2], "parent_id": dst}
        ).json()
        assert moved == {"ok": 2, "failed": []}
        names = {e["name"] for e in client.get(f"/api/entries?parent_id={dst}", headers=headers).json()}
        assert names == {"a.txt", "b.txt"}

        # Sammel-ZIP über die Ordner
        token = client.post(
            "/api/archive/bulk-token", headers=headers, json={"entry_ids": [src, dst]}
        ).json()["url"]
        response = client.get(token)
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        assert "src/" in names and "dst/" in names and "dst/a.txt" in names

        # Bulk-Löschen
        deleted = client.post("/api/bulk/delete", headers=headers, json={"ids": [f1, f2]}).json()
        assert deleted == {"ok": 2, "failed": []}
        assert client.get(f"/api/entries?parent_id={dst}", headers=headers).json() == []
