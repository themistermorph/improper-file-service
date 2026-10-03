"""Durchsatz-Optimierungen des FTP-Gateways.

Prüft, dass Downloads das S3-Objekt mit einem Read-Ahead streamen (statt pro
64-KiB-Block einen Request zu öffnen) und dass der Upload-Pfad das Größenlimit
ohne ``fstat``-Syscall je Block durchsetzt. Ergänzend wird die Konfiguration des
Datenkanals (Puffergröße) abgesichert.
"""

from __future__ import annotations

import io

import pytest
from pyftpdlib.exceptions import FilesystemError

from ifs.config import get_settings
from ifs.core import content
from ifs.ftp import fs as fs_module
from ifs.ftp.fs import _BlobRef, _S3Reader, _UploadWriter


def _range_body(payload: bytes):
    """Fake-content.get_object mit korrektem Range-Slicing; zeichnet Calls auf."""

    calls: list[tuple[int | None, int | None]] = []

    def _get_object(key, start=None, end=None):
        calls.append((start, end))
        begin = start or 0
        finish = end if end is not None else len(payload) - 1
        return {"Body": io.BytesIO(payload[begin : finish + 1])}

    return _get_object, calls


def _read_all(reader: _S3Reader, chunk: int = 64 * 1024) -> bytes:
    parts: list[bytes] = []
    while True:
        part = reader.read(chunk)
        if not part:
            break
        parts.append(part)
    return b"".join(parts)


# --- Download: ein Stream statt ein Request je 64-KiB-Block -----------------


def test_download_uses_single_request_for_contiguous_reads(monkeypatch):
    payload = bytes(range(256)) * 4096  # 1 MiB = 16 64-KiB-Blöcke
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/aa/" + "aa" * 32, len(payload), None, 0.0)
    reader = _S3Reader(blob, "gross.bin")
    try:
        data = _read_all(reader)
    finally:
        reader.close()

    assert data == payload
    # Genau ein Range-Request trotz 16 Leseaufrufen.
    assert calls == [(0, len(payload) - 1)]


def test_download_composes_readahead_boundaries(monkeypatch):
    """Über die Read-Ahead-Grenze hinweg bleibt der Inhalt vollständig."""
    payload = bytes(range(256)) * 800  # 204 800 Bytes
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/bb/" + "bb" * 32, len(payload), None, 0.0)
    reader = _S3Reader(blob, "grenze.bin", readahead=64 * 1024)
    try:
        data = _read_all(reader)
    finally:
        reader.close()

    assert data == payload
    # 200_000 / 64KiB -> vier Bereiche, nicht 4 Requests pro 64-KiB-Block.
    assert len(calls) == 4
    assert calls[0] == (0, 65535)
    assert calls[-1][0] == 3 * 65536


def test_download_seek_resets_stream(monkeypatch):
    payload = b"0123456789" * 20_000  # 200 000 Bytes
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/cc/" + "cc" * 32, len(payload), None, 0.0)
    reader = _S3Reader(blob, "resume.bin", readahead=64 * 1024)
    try:
        assert reader.read(10) == payload[:10]
        offset = 100_000
        reader.seek(offset)
        assert reader.tell() == offset
        rest = _read_all(reader, chunk=16 * 1024)
    finally:
        reader.close()

    assert rest == payload[offset:]
    # Nach dem Seek wird ein Bereich ab dem Offset neu geöffnet.
    assert any(start == offset for start, _end in calls)


def test_download_readall_matches_chunked(monkeypatch):
    payload = bytes(range(256)) * 500
    get_object, _calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/dd/" + "dd" * 32, len(payload), None, 0.0)

    chunked = _S3Reader(blob, "a.bin", readahead=64 * 1024)
    try:
        assert b"".join(iter(lambda: chunked.read(4096), b"")) == payload
    finally:
        chunked.close()

    readall = _S3Reader(blob, "b.bin", readahead=64 * 1024)
    try:
        assert readall.read(-1) == payload
    finally:
        readall.close()


def test_download_readahead_is_clamped_to_minimum(monkeypatch):
    payload = bytes(range(256)) * 800  # 204 800 Bytes
    get_object, calls = _range_body(payload)
    monkeypatch.setattr(content, "get_object", get_object)

    blob = _BlobRef("cas/ee/" + "ee" * 32, len(payload), None, 0.0)
    reader = _S3Reader(blob, "min.bin", readahead=1024)  # < 64 KiB
    try:
        assert _read_all(reader) == payload
    finally:
        reader.close()

    # Der zu kleine Wert wird auf die Untergrenze (64 KiB) angehoben.
    assert calls[0] == (0, 64 * 1024 - 1)


# --- Upload: Größenlimit ohne fstat je Block --------------------------------


def test_upload_limit_checked_without_fstat(monkeypatch, tmp_path):
    boom_calls = {"count": 0}

    def _boom(*_args, **_kwargs):
        boom_calls["count"] += 1
        raise AssertionError("os.fstat darf im Schreib-Hot-Path nicht aufgerufen werden")

    monkeypatch.setattr(fs_module.os, "fstat", _boom)

    writer = _UploadWriter(str(tmp_path / "spool.bin"), "spool.bin", limit=10)
    try:
        assert writer.write(b"12345") == 5
        assert writer.write(b"6789") == 4
        with pytest.raises(FilesystemError):
            writer.write(b"xy")  # 11 > 10
    finally:
        writer.close()

    assert boom_calls["count"] == 0


def test_upload_append_accounts_existing_size(tmp_path):
    path = str(tmp_path / "appe.bin")
    writer = _UploadWriter(path, "appe.bin", limit=10)
    try:
        # Vorhandenen Inhalt nachstellen (wie beim APPE-Vorladen) und die
        # Größe wie in ``_open_writer`` übernehmen.
        with open(path, "wb") as handle:
            handle.write(b"A" * 8)
        writer._handle.close()
        writer._handle = open(path, "ab")
        writer.sync_size()
        assert writer.tell() == 8

        assert writer.write(b"BB") == 2  # genau am Limit
        with pytest.raises(FilesystemError):
            writer.write(b"C")  # 11 > 10
    finally:
        writer.close()


# --- Konfiguration des Datenkanals ------------------------------------------


def test_build_handler_configures_transfer_buffers(monkeypatch):
    from ifs.ftp.server import (
        _MIN_TRANSFER_BUFFER,
        FileProducer,
        IFSDTPHandler,
        IFSFTPHandler,
        build_handler,
    )

    monkeypatch.setenv("IFS_FTP_CERTFILE", "/tmp/ftps.crt")
    monkeypatch.setenv("IFS_FTP_KEYFILE", "/tmp/ftps.key")
    monkeypatch.setenv("IFS_FTP_TRANSFER_BUFFER_BYTES", str(512 * 1024))
    get_settings.cache_clear()
    try:
        handler = build_handler()
        assert handler is IFSFTPHandler
        assert IFSFTPHandler.dtp_handler is IFSDTPHandler
        assert IFSDTPHandler.ac_in_buffer_size == 512 * 1024
        assert IFSDTPHandler.ac_out_buffer_size == 512 * 1024
        assert FileProducer.buffer_size == 512 * 1024
    finally:
        get_settings.cache_clear()

    # Zu kleine Werte werden auf die Untergrenze angehoben.
    monkeypatch.setenv("IFS_FTP_TRANSFER_BUFFER_BYTES", "1024")
    get_settings.cache_clear()
    try:
        build_handler()
        assert IFSDTPHandler.ac_out_buffer_size == _MIN_TRANSFER_BUFFER
    finally:
        get_settings.cache_clear()
