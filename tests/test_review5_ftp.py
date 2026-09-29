"""Regressionstests für die Findings aus dem fünften Review (FTP-Gateway)."""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pyftpdlib.exceptions import FilesystemError

from ifs.core import content, namespace
from ifs.db import session_scope
from ifs.ftp.fs import IFSFilesystem, _BlobRef, _S3Reader
from ifs.ftp.server import IFSFTPHandler

from .conftest import make_blob, make_user


def _fs_for(username: str) -> IFSFilesystem:
    """IFSFilesystem ohne asyncore-Initialisierung (Muster aus test_security_fixes3)."""
    fs = IFSFilesystem.__new__(IFSFilesystem)
    fs.cmd_channel = SimpleNamespace(username=username)
    fs._pending = {}
    return fs


def _range_body(payload: bytes):
    """Fake-content.get_object mit korrektem Range-Slicing."""

    calls: list[tuple[int | None, int | None]] = []

    def _get_object(key, start=None, end=None):
        calls.append((start, end))
        begin = start or 0
        finish = end if end is not None else len(payload) - 1
        return {"Body": io.BytesIO(payload[begin : finish + 1])}

    return _get_object, calls


# --- F1: Downloads >64 KiB werden vollständig gelesen ---------------------------


def test_s3_reader_reads_more_than_64kib(monkeypatch):
    payload = bytes(range(256)) * 400  # 102_400 Bytes
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/ab/" + "ab" * 32, len(payload), "application/octet-stream", 0.0)
    reader = _S3Reader(blob, "big.bin")
    try:
        chunks: list[bytes] = []
        while True:
            chunk = reader.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        reader.close()

    assert data == payload
    assert len(data) == 102_400
    assert len(calls) >= 2


def test_s3_reader_small_read_uses_single_range(monkeypatch):
    """Passt der Inhalt in einen Range, bleibt es bei genau einem S3-Aufruf."""
    payload = b"x" * 100
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/cd/" + "cd" * 32, len(payload), None, 0.0)
    reader = _S3Reader(blob, "small.bin")
    try:
        assert reader.read(100) == payload
    finally:
        reader.close()

    assert calls == [(0, 99)]


def test_s3_reader_stops_on_shorter_object(monkeypatch):
    """Ein kürzeres Objekt als im Blob vermerkt darf keine Endlosschleife auslösen."""
    payload = b"kurz"
    get_object, _calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/ef/" + "ef" * 32, len(payload) + 10, None, 0.0)
    reader = _S3Reader(blob, "short.bin")
    try:
        assert reader.read(64 * 1024) == payload
    finally:
        reader.close()


# --- F2: `_open_writer` lehnt absehbare Konflikte früh ab -----------------------


def test_open_writer_rejects_folder_conflict_early():
    with session_scope() as db:
        user = make_user(db, "fs-writer")
        root = namespace.ensure_root(db, user.id)
        docs = namespace.create_folder(db, root, "docs", user.id)
        namespace.create_folder(db, docs, "sub", user.id)

    fs = _fs_for("fs-writer")
    with pytest.raises(FilesystemError):
        fs._open_writer("/docs/sub", "w")
    # Es darf kein Pending-Eintrag (und damit kein Spool) entstanden sein.
    assert fs._pending == {}


# --- F3: Domänenfehler werden zu FilesystemError („550“) ------------------------


def test_mkdir_conflict_raises_filesystem_error():
    with session_scope() as db:
        user = make_user(db, "fs-mkdir")
        root = namespace.ensure_root(db, user.id)
        namespace.create_folder(db, root, "docs", user.id)

    fs = _fs_for("fs-mkdir")
    with pytest.raises(FilesystemError):
        fs.mkdir("/docs")


def test_rmdir_root_raises_filesystem_error():
    with session_scope() as db:
        user = make_user(db, "fs-rmdir")
        namespace.ensure_root(db, user.id)

    fs = _fs_for("fs-rmdir")
    with pytest.raises(FilesystemError):
        fs.rmdir("/")


def test_rename_conflict_raises_filesystem_error():
    with session_scope() as db:
        user = make_user(db, "fs-rename")
        root = namespace.ensure_root(db, user.id)
        docs = namespace.create_folder(db, root, "docs", user.id)
        namespace.apply_write(db, docs, "a.txt", user.id, make_blob(db, b"a"))
        namespace.apply_write(db, docs, "b.txt", user.id, make_blob(db, b"b"))

    fs = _fs_for("fs-rename")
    with pytest.raises(FilesystemError):
        fs.rename("/docs/a.txt", "/docs/b.txt")


# --- F4: Kein Spool-Leck bei abgebrochenen/überschriebenen Uploads --------------


def _prepare_folder(username: str) -> None:
    with session_scope() as db:
        user = make_user(db, username)
        root = namespace.ensure_root(db, user.id)
        namespace.create_folder(db, root, "docs", user.id)


def test_discard_pending_removes_spool():
    _prepare_folder("fs-discard")
    fs = _fs_for("fs-discard")
    writer = fs._open_writer("/docs/broken.bin", "w")
    spool = writer._path
    writer.close()
    assert os.path.exists(spool)

    fs.discard_pending("/docs/broken.bin")

    assert not os.path.exists(spool)
    assert fs._pending == {}


def test_second_writer_removes_previous_spool():
    _prepare_folder("fs-overwrite")
    fs = _fs_for("fs-overwrite")
    first = fs._open_writer("/docs/same.bin", "w")
    first_spool = first._path
    first.close()
    assert os.path.exists(first_spool)

    second = fs._open_writer("/docs/same.bin", "w")
    try:
        assert second._path != first_spool
        assert not os.path.exists(first_spool)
    finally:
        second.close()
        fs.cleanup()


def test_incomplete_upload_discards_spool():
    _prepare_folder("fs-incomplete")
    fs = _fs_for("fs-incomplete")
    writer = fs._open_writer("/docs/aborted.bin", "w")
    spool = writer._path
    writer.close()

    # Handler-Override direkt aufrufen (asyncore-Initialisierung nicht nötig).
    IFSFTPHandler.on_incomplete_file_received(SimpleNamespace(fs=fs), "/docs/aborted.bin")

    assert not os.path.exists(spool)
    assert fs._pending == {}


def test_incomplete_upload_swallows_discard_errors():
    class _Broken:
        def discard_pending(self, path):
            raise RuntimeError("kaputt")

    # Fehler beim Aufräumen dürfen die FTP-Verbindung nicht sprengen.
    IFSFTPHandler.on_incomplete_file_received(SimpleNamespace(fs=_Broken()), "/docs/x.bin")


# --- F5: Naive Zeitstempel werden als UTC interpretiert -------------------------


def test_naive_timestamps_are_read_as_utc():
    naive = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).replace(tzinfo=None)
    expected = naive.replace(tzinfo=UTC).timestamp()

    with session_scope() as db:
        user = make_user(db, "fs-time")
        root = namespace.ensure_root(db, user.id)
        file_entry = namespace.apply_write(db, root, "zeit.txt", user.id, make_blob(db, b"x"))
        file_entry.updated_at = naive
        db.flush()

    fs = _fs_for("fs-time")
    assert fs.getmtime("/zeit.txt") == pytest.approx(expected)
    assert fs.stat("/zeit.txt").st_mtime == pytest.approx(expected)

    reader = fs._open_reader("/zeit.txt")
    try:
        assert reader._blob.mtime == pytest.approx(expected)
    finally:
        reader.close()
