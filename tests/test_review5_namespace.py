"""Regressionstests für Review 5: Ordner-Kopie, Quota-Besitzer, Rollback, Last-Modified."""

from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content, namespace
from ifs.core import quota as quota_core
from ifs.db import session_scope
from ifs.errors import QuotaExceeded
from ifs.models import Entry, Version
from ifs.utils import as_utc

from .conftest import make_blob, make_user
from .helpers import auth, fake_store_blob, range_get_object

_fake_get_object = range_get_object(b"review5-inhalt")


def _set_quota_limit(monkeypatch, value: int) -> None:
    """Ersetzt die globale Quota-Grenze im Quota-Modul (0 = unbegrenzt)."""
    monkeypatch.setattr(
        quota_core, "get_settings", lambda: SimpleNamespace(default_quota_bytes=value)
    )


# --- Ordner-Kopie -----------------------------------------------------------


def test_folder_copy_copies_subtree(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    src = namespace.create_folder(db, root, "src", user.id)
    sub = namespace.create_folder(db, src, "sub", user.id)
    blob_a = make_blob(db, b"inhalt a")
    blob_b = make_blob(db, b"inhalt b")
    file_a = namespace.apply_write(db, src, "a.txt", user.id, blob_a)
    file_b = namespace.apply_write(db, sub, "b.txt", user.id, blob_b)

    clone = namespace.copy(db, src, root, "src-kopie", user.id)

    assert clone.id != src.id
    assert clone.type == src.type
    assert clone.owner_id == user.id
    cloned_a = namespace.resolve_path(db, "/src-kopie/a.txt", root)
    cloned_sub = namespace.resolve_path(db, "/src-kopie/sub", root)
    cloned_b = namespace.resolve_path(db, "/src-kopie/sub/b.txt", root)
    assert cloned_a is not None and cloned_a.id != file_a.id
    assert cloned_sub is not None and cloned_sub.id != sub.id
    assert cloned_b is not None and cloned_b.id != file_b.id
    assert cloned_a.size == file_a.size

    version_a = db.get(Version, cloned_a.current_version_id)
    version_b = db.get(Version, cloned_b.current_version_id)
    # Blob-Dedup: Die Kopie referenziert denselben Inhalt, jeweils als Version 1.
    assert version_a.blob_id == blob_a.id
    assert version_b.blob_id == blob_b.id
    assert version_a.seq == 1 and version_b.seq == 1
    assert version_a.created_by == user.id


def test_folder_copy_quota_counts_whole_subtree(monkeypatch, db):
    _set_quota_limit(monkeypatch, 9)
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    src = namespace.create_folder(db, root, "src", user.id)
    sub = namespace.create_folder(db, src, "sub", user.id)
    namespace.apply_write(db, src, "a.txt", user.id, make_blob(db, b"1234"))
    namespace.apply_write(db, sub, "b.txt", user.id, make_blob(db, b"5678"))

    with pytest.raises(QuotaExceeded):
        namespace.copy(db, src, root, "src-kopie", user.id)

    # Die Quota-Prüfung erfolgt vor dem Kopieren -> keine (Teil-)Kopie.
    assert namespace.resolve_path(db, "/src-kopie", root) is None


def test_folder_copy_quota_not_double_counted(monkeypatch, db):
    # Genau die Summe des Teilbaums passt in die Restquota. Würde die Quota für
    # jedes Kind erneut angerechnet, schlüge die Kopie fälschlich fehl.
    _set_quota_limit(monkeypatch, 16)
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    src = namespace.create_folder(db, root, "src", user.id)
    sub = namespace.create_folder(db, src, "sub", user.id)
    namespace.apply_write(db, src, "a.txt", user.id, make_blob(db, b"1234"))
    namespace.apply_write(db, sub, "b.txt", user.id, make_blob(db, b"5678"))

    clone = namespace.copy(db, src, root, "src-kopie", user.id)

    assert clone.id != src.id
    assert namespace.resolve_path(db, "/src-kopie/sub/b.txt", root) is not None


def test_folder_copy_quota_api(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    _set_quota_limit(monkeypatch, 10)

    with TestClient(main.app) as client:
        headers = auth(client)
        root = client.get("/api/root", headers=headers).json()["id"]
        src = client.post("/api/folders", headers=headers, json={"name": "src"}).json()["id"]
        sub = client.post(
            "/api/folders", headers=headers, json={"parent_id": src, "name": "sub"}
        ).json()["id"]
        for parent, name, payload in (
            (src, "a.bin", b"12345"),
            (sub, "b.bin", b"67890"),
        ):
            created = client.put(
                f"/api/uploads/simple?parent_id={parent}&name={name}",
                headers=headers,
                content=payload,
            )
            assert created.status_code == 201, created.text

        copy = client.post(
            f"/api/entries/{src}/copy",
            headers=headers,
            json={"parent_id": root, "name": "src-kopie"},
        )
        assert copy.status_code == 413, copy.text
        assert client.get(f"/api/entries/{root}", headers=headers).status_code == 200


# --- Quota beim Überschreiben ----------------------------------------------


def test_overwrite_quota_uses_existing_owner(monkeypatch, db):
    _set_quota_limit(monkeypatch, 10)
    alice = make_user(db, "alice")
    bob = make_user(db, "bob")
    root = namespace.ensure_root(db, alice.id)
    folder = namespace.create_folder(db, root, "geteilt", alice.id)
    namespace.apply_write(db, folder, "shared.bin", alice.id, make_blob(db, b"a" * 9))

    # Bob ersetzt die 9 Bytes von Alice durch 11: Das Delta gehört Alice -> 9+2 > 10.
    with pytest.raises(QuotaExceeded):
        namespace.apply_write(db, folder, "shared.bin", bob.id, make_blob(db, b"b" * 11))

    # Bobs eigenes neues 11-Byte-File wird weiterhin abgelehnt.
    with pytest.raises(QuotaExceeded):
        namespace.apply_write(db, folder, "bob.bin", bob.id, make_blob(db, b"c" * 11))


# --- Quota beim Versions-Rollback ------------------------------------------


def test_restore_version_quota(monkeypatch, db):
    _set_quota_limit(monkeypatch, 0)
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    entry = namespace.apply_write(db, folder, "big.bin", user.id, make_blob(db, b"x" * 20))
    entry = namespace.apply_write(db, folder, "big.bin", user.id, make_blob(db, b"yy"))
    old_version = namespace.list_versions(db, entry)[-1]  # seq 1, 20 Bytes

    _set_quota_limit(monkeypatch, 10)
    with pytest.raises(QuotaExceeded):
        namespace.restore_version(db, entry, old_version, user.id)


def test_restore_version_quota_api(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    _set_quota_limit(monkeypatch, 0)

    with TestClient(main.app) as client:
        headers = auth(client)
        folder = client.post("/api/folders", headers=headers, json={"name": "roll"}).json()["id"]
        url = f"/api/uploads/simple?parent_id={folder}&name=f.bin"
        entry_id = client.put(url, headers=headers, content=b"x" * 20).json()["id"]
        client.put(url, headers=headers, content=b"yy")
        versions = client.get(f"/api/entries/{entry_id}/versions", headers=headers).json()
        old_version_id = versions[-1]["id"]

        # Limit erst für den Rollback senken (Uploads waren vorher unbegrenzt).
        _set_quota_limit(monkeypatch, 10)
        response = client.post(
            f"/api/entries/{entry_id}/versions/{old_version_id}/restore", headers=headers
        )
        assert response.status_code == 413, response.text


# --- Last-Modified mit naiven SQLite-Zeitstempeln --------------------------


def test_last_modified_header_uses_utc(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = auth(client)
        folder = client.post("/api/folders", headers=headers, json={"name": "lm"}).json()["id"]
        entry_id = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=lm.txt",
            headers=headers,
            content=b"zeitstempel",
        ).json()["id"]

        with session_scope() as db:
            entry = db.get(Entry, UUID(entry_id))
            assert entry is not None
            expected = as_utc(entry.updated_at)

        response = client.get(f"/api/entries/{entry_id}/content", headers=headers)
        assert response.status_code == 200
        parsed = parsedate_to_datetime(response.headers["last-modified"])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        # SQLite liefert naive Werte; sie sind UTC und nicht Lokalzeit.
        assert abs((parsed - expected).total_seconds()) <= 1
