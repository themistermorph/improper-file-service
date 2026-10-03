"""Optimierungstests: Uploads hashen Spool-Dateien nur einmal vollständig.

Belegt per Aufrufzähler (`monkeypatch`), dass

* der resumable Complete-Weg den bereits berechneten SHA-256 durchreicht und
* der einfache Upload den SHA-256 während des Schreibens bildet,

jeweils ohne die Spool-Datei erneut vollständig zu hashen. Zusätzlich bleibt
das Resume-/Dedup-Verhalten unverändert.
"""

from __future__ import annotations

import hashlib
import os

from fastapi.testclient import TestClient

from ifs import main
from ifs.core import content, namespace
from ifs.core import uploads as uploads_core
from ifs.db import session_scope

from .conftest import make_user


def _login(client: TestClient) -> str:
    return client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]


def _fake_upload(calls: list[tuple[str, str]]):
    """Ersetzt den S3-Upload und protokolliert (Pfad, Digest)."""

    def fake_upload(local_path, sha256, mime=None):
        calls.append((str(local_path), sha256))
        return content.BlobInfo(
            storage_key=f"cas/{sha256[:2]}/{sha256}",
            sha256=sha256,
            size=os.path.getsize(local_path),
        )

    return fake_upload


def _counting_sha256_file(paths: list[str]):
    """Zählt vollständige Hash-Läufe und delegiert an die echte Implementierung."""
    real = content.sha256_file

    def counting(path):
        paths.append(str(path))
        return real(path)

    return counting


# --- Nachweis: kein doppeltes Voll-Hashing -------------------------------------


def test_complete_hasht_spool_nur_einmal(monkeypatch):
    """Resumable Complete: Prüfsumme genau einmal, Digest geht an store_blob."""
    payload = b"optimierung-ist-messbar" * 512
    expected = hashlib.sha256(payload).hexdigest()
    hash_calls: list[str] = []
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload(uploads))

    with session_scope() as db:
        owner = make_user(db, "opt-complete")
        root = namespace.ensure_root(db, owner.id)
        session = uploads_core.create_session(
            db, root, "data.bin", owner.id, sha256_expected=expected
        )
        spool_path = session.spool_path
        uploads_core.append(db, session, 0, [payload])
        entry = uploads_core.complete(db, owner, session)

    # Vor der Optimierung lief sha256_file zweimal (Prüfsumme + store_blob).
    assert hash_calls == [spool_path]
    assert uploads == [(spool_path, expected)]
    assert entry.sha256 == expected
    assert entry.size == len(payload)
    assert not os.path.exists(spool_path)


def test_simple_upload_hasht_im_stream(monkeypatch):
    """Einfacher Upload: SHA-256 entsteht beim Schreiben, kein separater Read-Pass."""
    hash_calls: list[str] = []
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload(uploads))

    payload = b"kein zweiter vollstaendiger Lesedurchlauf" * 64
    expected = hashlib.sha256(payload).hexdigest()

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {_login(client)}"}
        root = client.get("/api/root", headers=auth).json()["id"]
        response = client.put(
            f"/api/uploads/simple?parent_id={root}&name=stream.bin",
            headers=auth,
            content=payload,
        )

    assert response.status_code == 201, response.text
    assert response.json()["sha256"] == expected
    assert hash_calls == []  # vorher: zusätzlicher Voll-Hashlauf in store_blob
    assert len(uploads) == 1
    assert uploads[0][1] == expected


# --- store_blob: expliziter Digest und SpoolRef ---------------------------------


def test_store_blob_nutzt_uebergebenen_digest(monkeypatch, tmp_path, db):
    data = b"vorberechneter digest"
    expected = hashlib.sha256(data).hexdigest()
    path = tmp_path / "spool.bin"
    path.write_bytes(data)

    hash_calls: list[str] = []
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload(uploads))

    blob = content.store_blob(db, str(path), sha256=expected)

    assert hash_calls == []  # Digest war bekannt -> kein erneutes Hashen
    assert blob.sha256 == expected
    assert blob.storage_key == f"cas/{expected[:2]}/{expected}"
    assert blob.size == len(data)
    assert uploads == [(str(path), expected)]


def test_store_blob_ohne_digest_hasht_weiterhin(monkeypatch, tmp_path, db):
    """Default-Verhalten bleibt: genau ein Hashlauf über die Datei."""
    data = b"ohne digest"
    path = tmp_path / "spool.bin"
    path.write_bytes(data)

    hash_calls: list[str] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload([]))

    blob = content.store_blob(db, str(path))

    assert hash_calls == [str(path)]
    assert blob.sha256 == hashlib.sha256(data).hexdigest()


def test_spoolref_verwendet_mitgefuehrten_digest(monkeypatch, tmp_path, db):
    data = b"mitgefuehrt"
    expected = hashlib.sha256(data).hexdigest()
    path = tmp_path / "spool.bin"
    path.write_bytes(data)

    hash_calls: list[str] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload([]))

    blob = content.store_blob(db, content.SpoolRef(str(path), expected, len(data)))

    assert hash_calls == []
    assert blob.sha256 == expected


def test_spoolref_mit_abweichender_groesse_hasht_sicher_nach(monkeypatch, tmp_path, db):
    """Ein veralteter Digest (Größe passt nicht) wird nicht übernommen."""
    data = b"sicher"
    path = tmp_path / "spool.bin"
    path.write_bytes(data)

    hash_calls: list[str] = []
    monkeypatch.setattr(content, "sha256_file", _counting_sha256_file(hash_calls))
    monkeypatch.setattr(content, "object_exists", lambda storage_key: False)
    monkeypatch.setattr(content, "upload_file", _fake_upload([]))

    ref = content.SpoolRef(str(path), "0" * 64, len(data) + 1)
    blob = content.store_blob(db, ref)

    assert hash_calls == [str(path)]
    assert blob.sha256 == hashlib.sha256(data).hexdigest()


# --- Verhalten unverändert: Resume + Dedup ---------------------------------------


def test_resume_und_dedup_verhalten_unveraendert(monkeypatch):
    first_part, second_part = b"teil-eins-", b"teil-zwei"
    payload = first_part + second_part
    expected = hashlib.sha256(payload).hexdigest()
    uploads: list[tuple[str, str]] = []
    monkeypatch.setattr(content, "object_exists", lambda storage_key: True)
    monkeypatch.setattr(content, "upload_file", _fake_upload(uploads))

    with TestClient(main.app) as client:
        auth = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post(
            "/api/folders", headers=auth, json={"name": "opt-resume"}
        ).json()["id"]
        created = client.post(
            "/api/uploads",
            headers=auth,
            json={
                "parent_id": folder,
                "name": "resume.bin",
                "size": len(payload),
                "sha256": expected,
            },
        )
        assert created.status_code == 201, created.text
        upload_id = created.json()["id"]

        first = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**auth, "Upload-Offset": "0"},
            content=first_part,
        )
        assert first.status_code == 204, first.text
        assert first.headers["upload-offset"] == str(len(first_part))

        second = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**auth, "Upload-Offset": str(len(first_part))},
            content=second_part,
        )
        assert second.status_code == 204, second.text
        assert second.headers["upload-offset"] == str(len(payload))

        done = client.post(f"/api/uploads/{upload_id}/complete", headers=auth)
        assert done.status_code == 201, done.text
        assert done.json()["sha256"] == expected
        assert done.json()["size"] == len(payload)

        # Dedup: identischer Inhalt ein zweites Mal -> kein weiterer S3-Upload.
        again = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=dup.bin",
            headers=auth,
            content=payload,
        )
        assert again.status_code == 201, again.text
        assert again.json()["sha256"] == expected

    assert len(uploads) == 1
    assert uploads[0][1] == expected
