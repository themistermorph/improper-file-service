"""Regressionstests: Blob-CAS-Dedup (content.store_blob) und Login-Rate-Limiter."""

from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

from sqlalchemy import select

from ifs.core import content, ratelimit
from ifs.models import Blob, BlobStatus


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_key(sha256: str) -> str:
    return f"cas/{sha256[:2]}/{sha256}"


def _write(tmp_path, data: bytes) -> str:
    path = tmp_path / "upload.bin"
    path.write_bytes(data)
    return str(path)


def _fake_upload(local_path, sha256, mime=None) -> content.BlobInfo:
    return content.BlobInfo(
        storage_key=_storage_key(sha256),
        sha256=sha256,
        size=os.path.getsize(local_path),
    )


def test_store_blob_updates_existing_row_when_object_missing(db, monkeypatch, tmp_path):
    """Fehlt das S3-Objekt, wird die vorhandene CAS-Zeile aktualisiert statt neu angelegt."""
    data = b"hello world"
    sha = _sha256(data)
    key = _storage_key(sha)

    existing = Blob(storage_key=key, sha256=sha, size=1, status=BlobStatus.ready)
    db.add(existing)
    db.flush()
    existing_id = existing.id

    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    uploads: list[str] = []

    def fake_upload(local_path, sha256, mime=None):
        uploads.append(local_path)
        return _fake_upload(local_path, sha256)

    monkeypatch.setattr(content, "upload_file", fake_upload)

    blob = content.store_blob(db, _write(tmp_path, data))

    assert blob.id == existing_id
    assert blob.sha256 == sha
    assert blob.size == len(data)
    assert blob.status == BlobStatus.ready
    assert uploads  # Objekt wurde neu hochgeladen
    rows = db.execute(select(Blob).where(Blob.storage_key == key)).scalars().all()
    assert len(rows) == 1  # keine zweite Zeile mit identischem storage_key


def test_store_blob_dedup_returns_existing_without_upload(db, monkeypatch, tmp_path):
    """Bei vorhandenem Objekt und aktivem Dedup wird die vorhandene Blob-ID geliefert."""
    path = _write(tmp_path, b"dedup me")
    uploads: list[str] = []

    def fake_upload(local_path, sha256, mime=None):
        uploads.append(local_path)
        return _fake_upload(local_path, sha256)

    monkeypatch.setattr(content, "upload_file", fake_upload)
    monkeypatch.setattr(content, "object_exists", lambda storage_key: True)

    first = content.store_blob(db, path)
    second = content.store_blob(db, path)

    assert second.id == first.id
    assert len(uploads) == 1  # zweiter Aufruf lädt nicht erneut hoch


def test_store_blob_without_dedup_updates_existing_row(db, monkeypatch, tmp_path):
    """Auch mit cas_dedup=False wird bei vorhandenem storage_key aktualisiert, nicht insertet."""
    data = b"no dedup"
    sha = _sha256(data)
    key = _storage_key(sha)

    existing = Blob(storage_key=key, sha256=sha, size=1, status=BlobStatus.ready)
    db.add(existing)
    db.flush()
    existing_id = existing.id

    monkeypatch.setattr(content, "get_settings", lambda: SimpleNamespace(cas_dedup=False))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: True)
    uploads: list[str] = []

    def fake_upload(local_path, sha256, mime=None):
        uploads.append(local_path)
        return _fake_upload(local_path, sha256)

    monkeypatch.setattr(content, "upload_file", fake_upload)

    blob = content.store_blob(db, _write(tmp_path, data))

    assert blob.id == existing_id
    assert blob.size == len(data)
    assert len(uploads) == 1  # Dedup aus -> es wird hochgeladen
    rows = db.execute(select(Blob).where(Blob.storage_key == key)).scalars().all()
    assert len(rows) == 1


def test_store_blob_parallel_insert_returns_existing_row(db, monkeypatch, tmp_path):
    """Ein paralleler Insert mit gleichem storage_key führt zu keinem Fehler."""
    data = b"race"
    sha = _sha256(data)
    key = _storage_key(sha)
    path = _write(tmp_path, data)

    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    inserted: list[Blob] = []

    def fake_upload(local_path, sha256, mime=None):
        # Simuliert einen parallelen Upload, der die Blob-Zeile bereits angelegt hat.
        row = Blob(storage_key=key, sha256=sha, size=1, status=BlobStatus.ready)
        db.add(row)
        db.flush()
        inserted.append(row)
        return _fake_upload(local_path, sha256)

    monkeypatch.setattr(content, "upload_file", fake_upload)

    blob = content.store_blob(db, path)

    assert inserted and blob.id == inserted[0].id
    rows = db.execute(select(Blob).where(Blob.storage_key == key)).scalars().all()
    assert len(rows) == 1


def test_record_success_only_unlocks_account_not_ip():
    """Ein erfolgreicher Login darf den IP-Zähler (Credential-Spraying) nicht löschen."""
    account = ratelimit.LoginRateLimiter(2, 60, 30, 60)
    by_ip = ratelimit.LoginRateLimiter(2, 60, 30, 60)
    ratelimit.install(account, by_ip)

    account_key_1, ip_key = ratelimit.keys("10.0.0.1", "alice")
    account_key_2, _ = ratelimit.keys("10.0.0.1", "bob")

    # Fehlversuche: Konto 1 ist gesperrt, derselbe IP-Zähler sammelt über beide Namen.
    ratelimit.record_failure(account_key_1, ip_key)
    ratelimit.record_failure(account_key_1, ip_key)
    ratelimit.record_failure(account_key_2, ip_key)
    assert ratelimit.retry_after(account_key_2, ip_key) > 0

    # Erfolgreicher Login von Konto 1: nur das Konto wird entsperrt.
    ratelimit.record_success(account_key_1, ip_key)

    assert account.retry_after(account_key_1) == 0  # Konto entsperrt
    assert by_ip.retry_after(ip_key) > 0  # IP-Sperre bleibt bestehen
    assert ratelimit.retry_after(account_key_2, ip_key) > 0  # Spray-Schutz weiter aktiv
