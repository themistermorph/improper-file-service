"""Tests für Versionshistorie und Rollback."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_get_object, fake_store_blob


def test_version_history_and_rollback(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", fake_get_object)

    with TestClient(main.app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        folder = client.post("/api/folders", headers=headers, json={"name": "ver"}).json()["id"]
        url = f"/api/uploads/simple?parent_id={folder}&name=note.txt"
        entry_id = client.put(url, headers=headers, content=b"v1").json()["id"]
        client.put(url, headers=headers, content=b"v2")  # überschreibt -> neue Version

        versions = client.get(f"/api/entries/{entry_id}/versions", headers=headers).json()
        assert [v["seq"] for v in versions] == [2, 1]
        assert versions[0]["is_current"] is True
        assert versions[1]["is_current"] is False
        first_version_id = versions[1]["id"]

        # Aktueller Inhalt ist v2
        assert client.get(f"/api/entries/{entry_id}/content", headers=headers).content == b"v2"

        # Alte Version einzeln herunterladen
        old = client.get(
            f"/api/entries/{entry_id}/versions/{first_version_id}/content", headers=headers
        )
        assert old.status_code == 200
        assert old.content == b"v1"

        # Rollback auf Version 1 -> neue Version (seq 3), Inhalt wieder v1
        restored = client.post(
            f"/api/entries/{entry_id}/versions/{first_version_id}/restore", headers=headers
        ).json()
        assert restored["seq"] == 3
        assert restored["is_current"] is True
        assert client.get(f"/api/entries/{entry_id}/content", headers=headers).content == b"v1"
        after = client.get(f"/api/entries/{entry_id}/versions", headers=headers).json()
        assert [v["seq"] for v in after] == [3, 2, 1]

        # Ordner haben keine Versionen
        assert client.get(f"/api/entries/{folder}/versions", headers=headers).status_code == 400
