"""Tests für die ZIP-Erstellung als Hintergrund-Job mit Fortschritt."""

from __future__ import annotations

import io
import time
import zipfile

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content
from ifs.models import Blob, BlobStatus

PAYLOAD = b"job zip"


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


def _login(client) -> str:
    return client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]


def _wait_ready(client, token, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    status = {}
    while time.time() < deadline:
        status = client.get(f"/api/archive/jobs/{token}").json()
        if status.get("status") in ("ready", "failed"):
            return status
        time.sleep(0.05)
    return status


def test_archive_job_progress_and_download(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "jobs"}).json()["id"]
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

        created = client.post(
            "/api/archive/jobs", headers=admin, json={"entry_ids": [folder]}
        )
        assert created.status_code == 201, created.text
        data = created.json()
        assert data["total_files"] == 2
        assert data["name"] == "jobs.zip"
        assert data["url"].startswith("/api/archive/")

        status = _wait_ready(client, data["token"])
        assert status["status"] == "ready", status
        assert status["files_done"] == 2
        assert status["total_files"] == 2

        response = client.get(data["url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        assert "jobs/" in names
        assert "jobs/a.txt" in names
        assert "jobs/sub/b.txt" in names


def test_archive_job_cancel(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "canc"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=a.txt",
            headers=admin,
            content=PAYLOAD,
        )
        token = client.post(
            "/api/archive/jobs", headers=admin, json={"entry_ids": [folder]}
        ).json()["token"]

        assert client.delete(f"/api/archive/jobs/{token}").status_code == 204
        assert client.get(f"/api/archive/jobs/{token}").status_code == 404


def test_archive_jobs_require_auth():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/archive/jobs",
            json={"entry_ids": ["00000000-0000-0000-0000-000000000000"]},
        )
        assert response.status_code == 401


def test_archive_job_status_unknown_token():
    with TestClient(main.app) as client:
        # Ungültiger/fehlender Token -> 401 (Authentifizierung vor Job-Lookup).
        assert client.get("/api/archive/jobs/gibtsnicht").status_code == 401

        # Gültiger Token, aber kein (mehr vorhandener) Job -> 404.
        admin = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post(
            "/api/folders", headers=admin, json={"name": "unknown-job"}
        ).json()["id"]
        token = client.post(
            "/api/archive/jobs", headers=admin, json={"entry_ids": [folder]}
        ).json()["token"]
        assert client.delete(f"/api/archive/jobs/{token}").status_code == 204
        assert client.get(f"/api/archive/jobs/{token}").status_code == 404
