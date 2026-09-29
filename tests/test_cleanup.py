"""Test für das CLI-Kommando cleanup-missing-blobs."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from ifs.cli import cmd_cleanup_missing_blobs
from ifs.core import content, namespace
from ifs.db import session_scope
from ifs.models import Blob, BlobStatus, Entry, EntryType, User
from ifs.security import hash_password


def _seed():
    with session_scope() as db:
        user = User(username="cleaner", password_hash=hash_password("geheim123"))
        db.add(user)
        db.flush()
        root = namespace.ensure_root(db, user.id)
        folder = namespace.create_folder(db, root, "d", user.id)
        blob = Blob(storage_key="cas/aa/deadbeef", sha256="a" * 64, size=1, status=BlobStatus.ready)
        db.add(blob)
        db.flush()
        namespace.apply_write(db, folder, "a.txt", user.id, blob)
        namespace.apply_write(db, folder, "b.txt", user.id, blob)
    return folder.id


def test_cleanup_removes_files_without_blob(monkeypatch):
    _seed()
    # Blob gilt als fehlend
    monkeypatch.setattr(content, "object_exists", lambda key: False)
    assert cmd_cleanup_missing_blobs(SimpleNamespace(remove_empty_folders=False)) == 0

    with session_scope() as db:
        files = db.execute(select(Entry).where(Entry.type == EntryType.file)).scalars().all()
        assert files == []
        # Ordner bleibt (ohne --remove-empty-folders)
        root = db.execute(
            select(Entry).where(Entry.parent_id.is_(None), Entry.type == EntryType.folder)
        ).scalars().first()
        assert namespace.resolve_path(db, "/d", root) is not None


def test_cleanup_keeps_files_with_present_blob(monkeypatch):
    _seed()
    monkeypatch.setattr(content, "object_exists", lambda key: True)
    assert cmd_cleanup_missing_blobs(SimpleNamespace(remove_empty_folders=False)) == 0

    with session_scope() as db:
        files = db.execute(select(Entry).where(Entry.type == EntryType.file)).scalars().all()
        assert len(files) == 2


def test_gc_blobs_deletes_only_unreferenced(monkeypatch):
    """Der Blob-GC entfernt nur unreferenzierte Blobs (DB zuerst, dann S3)."""
    from ifs.worker import gc_blobs

    deleted: list[str] = []
    monkeypatch.setattr(content, "delete_object", lambda key: deleted.append(key))
    used_key = "cas/11/" + "11" * 32
    orphan_key = "cas/22/" + "22" * 32

    with session_scope() as db:
        user = User(username="gc", password_hash=hash_password("geheim123"))
        db.add(user)
        db.flush()
        root = namespace.ensure_root(db, user.id)
        used_blob = Blob(storage_key=used_key, sha256="11" * 32, size=1, status=BlobStatus.ready)
        orphan_blob = Blob(
            storage_key=orphan_key, sha256="22" * 32, size=1, status=BlobStatus.ready
        )
        db.add_all([used_blob, orphan_blob])
        db.flush()
        namespace.apply_write(db, root, "used.txt", user.id, used_blob)
        orphan_id = orphan_blob.id

    assert gc_blobs() == 1
    assert deleted == [orphan_key]

    with session_scope() as db:
        assert db.get(Blob, orphan_id) is None
        assert (
            db.execute(select(Blob).where(Blob.storage_key == used_key)).scalars().first()
            is not None
        )
