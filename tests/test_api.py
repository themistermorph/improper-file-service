"""End-to-End-Tests der REST-API (SQLite, S3-Aufrufe gemockt)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import auth, fake_store_blob, range_get_object

_fake_get_object = range_get_object(b"hello world")


def test_api_flow(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = auth(client)

        response = client.get("/api/entries", headers=headers)
        assert response.status_code == 200
        assert response.json() == []

        root = client.get("/api/root", headers=headers)
        assert root.status_code == 200
        assert root.json()["type"] == "folder"
        assert root.json()["path"] == "/"

        response = client.post("/api/folders", headers=headers, json={"name": "docs"})
        assert response.status_code == 201
        folder_id = response.json()["id"]
        assert response.json()["path"] == "/docs"

        response = client.put(
            f"/api/uploads/simple?parent_id={folder_id}&name=hello.txt",
            headers=headers,
            content=b"hello world",
        )
        assert response.status_code == 201
        entry_id = response.json()["id"]

        response = client.get(f"/api/entries/{entry_id}/content", headers=headers)
        assert response.status_code == 200
        assert response.content == b"hello world"
        assert response.headers["accept-ranges"] == "bytes"

        response = client.get(
            f"/api/entries/{entry_id}/content",
            headers={**headers, "Range": "bytes=0-4"},
        )
        assert response.status_code == 206
        assert response.content == b"hello"

        response = client.head(f"/api/entries/{entry_id}/content", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-length"] == "11"

        assert client.get("/api/entries").status_code == 401


def test_resumable_upload(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = auth(client)
        folder_id = client.post(
            "/api/folders", headers=headers, json={"name": "res"}
        ).json()["id"]

        response = client.post(
            "/api/uploads",
            headers=headers,
            json={"parent_id": folder_id, "name": "big.bin", "size": 11},
        )
        assert response.status_code == 201
        upload_id = response.json()["id"]
        assert response.json()["offset"] == 0

        response = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**headers, "Upload-Offset": "0"},
            content=b"hello ",
        )
        assert response.status_code == 204
        assert response.headers["upload-offset"] == "6"

        response = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**headers, "Upload-Offset": "6"},
            content=b"world",
        )
        assert response.status_code == 204
        assert response.headers["upload-offset"] == "11"

        response = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
        assert response.status_code == 201
        entry_id = response.json()["id"]

        response = client.get(f"/api/entries/{entry_id}/content", headers=headers)
        assert response.content == b"hello world"
