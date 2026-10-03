"""Regressionstests für die Upload-Findings aus dem fünften Review."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.config import get_settings
from ifs.core import content, namespace
from ifs.core import uploads as uploads_core
from ifs.db import session_scope
from ifs.errors import BadRequest, Conflict
from ifs.models import UploadSession, UploadStatus

from .conftest import make_user
from .helpers import fake_store_blob, login


def _make_user(client, admin, username="bob", password="geheim123"):
    user_id = client.post(
        "/api/users", headers=admin, json={"username": username, "password": password}
    ).json()["id"]
    headers = {"Authorization": f"Bearer {login(client, username, password)}"}
    return user_id, headers


def _upload_state(upload_id: str) -> tuple[UploadStatus, str]:
    with session_scope() as db:
        session = db.get(UploadSession, UUID(upload_id))
        return session.status, session.spool_path


# --- 413 im PATCH: Abbruch muss dauerhaft persistiert werden -------------------


def test_patch_413_persists_abort(monkeypatch):
    """Ein Größenlimit-Treffer darf nicht per Rollback verloren gehen."""
    monkeypatch.setattr(
        "ifs.api.uploads.get_settings", lambda: SimpleNamespace(max_upload_size=5)
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "limit"}).json()["id"]
        upload_id = client.post(
            "/api/uploads", headers=admin, json={"parent_id": folder, "name": "big.bin"}
        ).json()["id"]

        first = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**admin, "Upload-Offset": "0"},
            content=b"abc",
        )
        assert first.status_code == 204, first.text
        assert first.headers["upload-offset"] == "3"

        too_big = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**admin, "Upload-Offset": "3"},
            content=b"abcd",
        )
        assert too_big.status_code == 413, too_big.text

        # Dauerhaft abgebrochen: HEAD/PATCH liefern keinen Zugriff mehr.
        assert client.head(f"/api/uploads/{upload_id}", headers=admin).status_code == 404
        again = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**admin, "Upload-Offset": "3"},
            content=b"x",
        )
        assert again.status_code == 404

    status, spool_path = _upload_state(upload_id)
    assert status == UploadStatus.aborted
    assert not os.path.exists(spool_path)


def test_patch_quota_exceeded_persists_abort(monkeypatch):
    """Auch der Limit-Treffer in `finish_append` (Core) wird persistiert."""
    settings = get_settings()
    monkeypatch.setattr(
        "ifs.api.uploads.get_settings", lambda: SimpleNamespace(max_upload_size=1000)
    )
    monkeypatch.setattr(
        "ifs.core.uploads.get_settings",
        lambda: SimpleNamespace(
            max_upload_size=5, upload_spool_dir=settings.upload_spool_dir
        ),
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post(
            "/api/folders", headers=admin, json={"name": "core-limit"}
        ).json()["id"]
        upload_id = client.post(
            "/api/uploads", headers=admin, json={"parent_id": folder, "name": "big2.bin"}
        ).json()["id"]

        too_big = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**admin, "Upload-Offset": "0"},
            content=b"x" * 10,
        )
        assert too_big.status_code == 413, too_big.text
        assert client.head(f"/api/uploads/{upload_id}", headers=admin).status_code == 404

    status, spool_path = _upload_state(upload_id)
    assert status == UploadStatus.aborted
    assert not os.path.exists(spool_path)


# --- Core-Integrität des Spools ------------------------------------------------


def test_begin_append_normalizes_spool(monkeypatch):
    """Reste abgebrochener Versuche werden auf den Offset gekürzt."""
    captured: dict = {}

    def capturing_store_blob(db, local_path, mime=None):
        with open(local_path, "rb") as handle:
            captured["data"] = handle.read()
        return fake_store_blob(db, local_path, mime)

    monkeypatch.setattr(content, "store_blob", capturing_store_blob)

    with session_scope() as db:
        owner = make_user(db, "spool-owner")
        root = namespace.ensure_root(db, owner.id)
        session = uploads_core.create_session(db, root, "data.bin", owner.id)
        path = session.spool_path
        uploads_core.append(db, session, 0, [b"abcd"])
        # Simulierter Client-Abbruch: Bytes im Spool, Offset nicht erhöht.
        with open(path, "ab") as handle:
            handle.write(b"XYZ")
        assert os.path.getsize(path) == 7

        assert uploads_core.begin_append(db, session, 4) == path
        assert os.path.getsize(path) == 4

        entry = uploads_core.complete(db, owner, session)
        assert entry.size == 4

    assert captured["data"] == b"abcd"


def test_begin_append_rejects_missing_or_short_spool():
    with session_scope() as db:
        owner = make_user(db, "spool-conflict")
        root = namespace.ensure_root(db, owner.id)

        missing = uploads_core.create_session(db, root, "missing.bin", owner.id)
        os.remove(missing.spool_path)
        with pytest.raises(Conflict, match="Upload-Spool fehlt"):
            uploads_core.begin_append(db, missing, 0)

        short = uploads_core.create_session(db, root, "short.bin", owner.id)
        uploads_core.append(db, short, 0, [b"abcd"])
        os.truncate(short.spool_path, 2)
        with pytest.raises(Conflict, match="Spool kleiner"):
            uploads_core.begin_append(db, short, 4)


def test_complete_rejects_manipulated_spool(monkeypatch):
    """Ein Spool größer als der Offset darf nicht nach S3 hochgeladen werden."""

    def _bomb(db, local_path, mime=None):
        raise AssertionError("store_blob darf bei Größen-Mismatch nicht aufgerufen werden")

    monkeypatch.setattr(content, "store_blob", _bomb)

    with session_scope() as db:
        owner = make_user(db, "spool-mismatch")
        root = namespace.ensure_root(db, owner.id)
        session = uploads_core.create_session(db, root, "boom.bin", owner.id)
        uploads_core.append(db, session, 0, [b"abcd"])
        with open(session.spool_path, "ab") as handle:
            handle.write(b"XYZ")

        with pytest.raises(BadRequest, match="Spool-Größe"):
            uploads_core.complete(db, owner, session)


# --- Sessions sind nutzergebunden, auch für Systemadmins -----------------------


def test_admin_cannot_abort_foreign_upload(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _bob_id, bob = _make_user(client, admin, "bob-r5")

        folder = client.post("/api/folders", headers=bob, json={"name": "bobs"}).json()["id"]
        upload_id = client.post(
            "/api/uploads", headers=bob, json={"parent_id": folder, "name": "f.bin"}
        ).json()["id"]

        # Kein Dateizugriff für Admins: HEAD/PATCH/DELETE auf fremder Session -> 403.
        assert client.head(f"/api/uploads/{upload_id}", headers=admin).status_code == 403
        patched = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**admin, "Upload-Offset": "0"},
            content=b"x",
        )
        assert patched.status_code == 403
        assert client.delete(f"/api/uploads/{upload_id}", headers=admin).status_code == 403

        # Der Besitzer darf weiterhin abbrechen.
        assert client.delete(f"/api/uploads/{upload_id}", headers=bob).status_code == 204
        assert client.head(f"/api/uploads/{upload_id}", headers=bob).status_code == 404


# --- Keine negativen Upload-Größen ---------------------------------------------


def test_upload_create_rejects_negative_size():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        root = client.get("/api/root", headers=admin).json()["id"]

        response = client.post(
            "/api/uploads",
            headers=admin,
            json={"parent_id": root, "name": "neg.bin", "size": -5},
        )
        assert response.status_code == 422, response.text
