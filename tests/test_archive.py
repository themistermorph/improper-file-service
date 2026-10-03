"""Tests für den ZIP-Download von Ordnern."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content

from .helpers import fake_store_blob, fixed_get_object

PAYLOAD = b"hello zip"


_fake_get_object = fixed_get_object(PAYLOAD)


def test_folder_zip_download(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        folder = client.post("/api/folders", headers=headers, json={"name": "bundle"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=a.txt",
            headers=headers,
            content=PAYLOAD,
        )
        sub = client.post(
            "/api/folders", headers=headers, json={"parent_id": folder, "name": "sub"}
        ).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={sub}&name=b.txt",
            headers=headers,
            content=PAYLOAD + b" zwei",
        )

        created = client.post(f"/api/entries/{folder}/archive-token", headers=headers)
        assert created.status_code == 200, created.text
        url = created.json()["url"]
        assert created.json()["name"] == "bundle.zip"

        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        assert "bundle/" in names
        assert "bundle/a.txt" in names
        assert "bundle/sub/" in names
        assert "bundle/sub/b.txt" in names

        # Ungültiger Token
        assert client.get("/api/archive/ungueltig").status_code == 401
