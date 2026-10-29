"""Optimierungstests für große HTTP-Transfers.

Belegt, dass

* Downloads optional mehrere S3-Range-Requests parallel lesen, aber streng
  in Dateireihenfolge und vollständig ausliefern,
* die Web-UI große Dateien über ein Download-Ticket nativ streamt,
* parallele Upload-Parts out-of-order, idempotent und mit Größenprüfung
  funktionieren und der Spool danach vollständig aufgeräumt ist.
"""

from __future__ import annotations

import hashlib
import io
import os
from uuid import UUID

from fastapi.testclient import TestClient

from ifs import main
from ifs.config import get_settings
from ifs.core import content
from ifs.db import session_scope
from ifs.models import UploadSession, UploadStatus

from .helpers import auth, fake_store_blob, range_get_object


def _patch_settings(monkeypatch, **values):
    settings = get_settings()
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    return settings


# --- Download: paralleles S3-Prefetch ------------------------------------------


def test_iter_object_parallel_liefert_geordnet_und_vollstaendig(monkeypatch):
    payload = bytes(range(256)) * 40960  # 10 MiB
    calls: list[tuple[int | None, int | None]] = []

    def fake_get(key, start=None, end=None):
        calls.append((start, end))
        data = payload[start or 0 : (end + 1 if end is not None else None)]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    _patch_settings(
        monkeypatch, http_stream_chunk_bytes=1024 * 1024, http_download_parallelism=4
    )
    monkeypatch.setattr(content, "get_object", fake_get)

    chunks = list(content.iter_object("k", start=0, end=len(payload) - 1, total_size=len(payload)))

    assert b"".join(chunks) == payload
    assert len(calls) > 1, "kein paralleles Prefetch erfolgt"
    # Alle Bereiche sind zusammenhängend, lückenlos und decken die Datei ab.
    calls.sort(key=lambda item: item[0] or 0)
    expected_start = 0
    for start, end in calls:
        assert start == expected_start
        assert end is not None and end >= start
        expected_start = end + 1
    assert expected_start == len(payload)


def test_iter_object_parallelism_eins_nutzt_einen_stream(monkeypatch):
    payload = b"x" * (5 * 1024 * 1024)
    calls: list[tuple[int | None, int | None]] = []

    def fake_get(key, start=None, end=None):
        calls.append((start, end))
        data = payload[start or 0 : (end + 1 if end is not None else None)]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    _patch_settings(
        monkeypatch, http_stream_chunk_bytes=1024 * 1024, http_download_parallelism=1
    )
    monkeypatch.setattr(content, "get_object", fake_get)

    data = b"".join(content.iter_object("k", start=0, end=len(payload) - 1, total_size=len(payload)))

    assert data == payload
    assert len(calls) == 1


def test_download_token_streamt_nativ_und_range(monkeypatch):
    payload = b"download-ticket" * 1000
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", range_get_object(payload))
    _patch_settings(
        monkeypatch, http_stream_chunk_bytes=64 * 1024, http_download_parallelism=2
    )

    with TestClient(main.app) as client:
        headers = auth(client)
        root = client.get("/api/root", headers=headers).json()["id"]
        created = client.put(
            f"/api/uploads/simple?parent_id={root}&name=ticket.bin",
            headers=headers,
            content=payload,
        )
        assert created.status_code == 201, created.text
        entry_id = created.json()["id"]

        ticket = client.post(f"/api/entries/{entry_id}/download-token", headers=headers)
        assert ticket.status_code == 200, ticket.text
        url = ticket.json()["url"]

        full = client.get(url)
        assert full.status_code == 200
        assert full.content == payload
        assert full.headers["cache-control"] == "no-store"

        partial = client.get(url, headers={"Range": "bytes=10-99"})
        assert partial.status_code == 206
        assert partial.content == payload[10:100]
        assert partial.headers["content-range"] == f"bytes 10-99/{len(payload)}"

        assert client.get("/api/download/kein-gueltiger-token").status_code == 401


# --- Upload: parallele Parts ---------------------------------------------------


def _part_upload_client(client, headers, size: int):
    root = client.get("/api/root", headers=headers).json()["id"]
    response = client.post(
        "/api/uploads",
        headers=headers,
        json={"parent_id": root, "name": "parts.bin", "size": size, "parallel": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_paralleler_upload_out_of_order_idempotent_und_cleanup(monkeypatch):
    uploads: list[tuple[tuple[str, ...], str]] = []

    def fake_upload_parts(paths, sha256, mime=None):
        uploads.append((tuple(paths), sha256))
        size = sum(os.path.getsize(path) for path in paths)
        return content.BlobInfo(
            storage_key=f"cas/{sha256[:2]}/{sha256}", sha256=sha256, size=size
        )

    monkeypatch.setattr(content, "object_exists", lambda key: False)
    monkeypatch.setattr(content, "upload_parts", fake_upload_parts)
    _patch_settings(monkeypatch, http_upload_part_bytes=5 * 1024 * 1024)

    payload = bytes(range(256)) * 45056  # 11 MiB -> 3 Parts à 5 MiB (letzter 1 MiB)
    with TestClient(main.app) as client:
        headers = auth(client)
        session = _part_upload_client(client, headers, len(payload))
        upload_id = session["id"]
        assert session["part_size"] >= 5 * 1024 * 1024
        part_size = session["part_size"]
        parts = [payload[i : i + part_size] for i in range(0, len(payload), part_size)]
        assert len(parts) == 3

        # Bewusst in umgekehrter Reihenfolge.
        for number in range(len(parts), 0, -1):
            response = client.put(
                f"/api/uploads/{upload_id}/parts/{number}",
                headers=headers,
                content=parts[number - 1],
            )
            assert response.status_code == 204, response.text

        # Idempotenter Retry überschreibt den Part atomar.
        retry = client.put(
            f"/api/uploads/{upload_id}/parts/1", headers=headers, content=parts[0]
        )
        assert retry.status_code == 204, retry.text

        head = client.head(f"/api/uploads/{upload_id}", headers=headers)
        assert head.status_code == 204
        assert head.headers["upload-part-size"] == str(part_size)
        assert head.headers["upload-parts"] == "1,2,3"
        assert head.headers["upload-offset"] == str(len(payload))

        done = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
        assert done.status_code == 201, done.text
        assert done.json()["sha256"] == hashlib.sha256(payload).hexdigest()
        assert done.json()["size"] == len(payload)

        with session_scope() as db:
            stored = db.get(UploadSession, UUID(upload_id))
            spool = stored.spool_path
            assert stored.status == UploadStatus.completed
        assert not os.path.exists(spool)
        assert not os.path.exists(spool + ".parts.json")
        assert not [p for p in os.listdir(os.path.dirname(spool)) if p.startswith(os.path.basename(spool) + ".p")]

    assert len(uploads) == 1
    assert uploads[0][1] == hashlib.sha256(payload).hexdigest()


def test_paralleler_upload_verweigert_falsche_partgroesse_und_luecke(monkeypatch):
    monkeypatch.setattr(content, "object_exists", lambda key: False)
    _patch_settings(monkeypatch, http_upload_part_bytes=5 * 1024 * 1024)

    payload = b"a" * (2 * 1024 * 1024 + 10)
    with TestClient(main.app) as client:
        headers = auth(client)
        session = _part_upload_client(client, headers, len(payload))
        upload_id = session["id"]

        # Zu kurz -> 400, Session bleibt offen.
        too_short = client.put(
            f"/api/uploads/{upload_id}/parts/1", headers=headers, content=b"x"
        )
        assert too_short.status_code == 400

        # Fehlende Parts -> Complete lehnt ab.
        missing = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
        assert missing.status_code == 400
        assert client.head(f"/api/uploads/{upload_id}", headers=headers).status_code == 204

        # Sequenzieller PATCH ist für Part-Sessions gesperrt.
        patch = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**headers, "Upload-Offset": "0"},
            content=b"x",
        )
        assert patch.status_code == 409


def test_paralleler_upload_in_vorhandenen_ordner(monkeypatch):
    """Part-Uploads respektieren `parent_id` und landen im Zielordner."""
    monkeypatch.setattr(content, "object_exists", lambda key: False)
    monkeypatch.setattr(
        content,
        "upload_parts",
        lambda paths, sha256, mime=None: content.BlobInfo(
            storage_key=f"cas/{sha256[:2]}/{sha256}",
            sha256=sha256,
            size=sum(os.path.getsize(path) for path in paths),
        ),
    )
    _patch_settings(monkeypatch, http_upload_part_bytes=5 * 1024 * 1024)

    payload = b"ordner" * (1024 * 1024)  # ~6 MiB -> 2 Parts
    with TestClient(main.app) as client:
        headers = auth(client)
        folder = client.post(
            "/api/folders", headers=headers, json={"name": "zielordner"}
        ).json()["id"]
        created = client.post(
            "/api/uploads",
            headers=headers,
            json={
                "parent_id": folder,
                "name": "drin.bin",
                "size": len(payload),
                "parallel": True,
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        part_size = session["part_size"]
        parts = [payload[i : i + part_size] for i in range(0, len(payload), part_size)]
        for number, chunk in enumerate(parts, start=1):
            response = client.put(
                f"/api/uploads/{session['id']}/parts/{number}",
                headers=headers,
                content=chunk,
            )
            assert response.status_code == 204, response.text
        done = client.post(
            f"/api/uploads/{session['id']}/complete", headers=headers
        )
        assert done.status_code == 201, done.text

        listing = client.get(
            f"/api/entries?parent_id={folder}", headers=headers
        ).json()
        assert [entry["name"] for entry in listing] == ["drin.bin"]
        assert listing[0]["size"] == len(payload)


def test_legacy_upload_bleibt_unveraendert(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    with TestClient(main.app) as client:
        headers = auth(client)
        root = client.get("/api/root", headers=headers).json()["id"]
        created = client.post(
            "/api/uploads",
            headers=headers,
            json={"parent_id": root, "name": "legacy.bin", "size": 6},
        )
        assert created.status_code == 201
        assert created.json()["part_size"] is None
        upload_id = created.json()["id"]
        first = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**headers, "Upload-Offset": "0"},
            content=b"abc",
        )
        assert first.status_code == 204
        second = client.patch(
            f"/api/uploads/{upload_id}",
            headers={**headers, "Upload-Offset": "3"},
            content=b"def",
        )
        assert second.status_code == 204
        done = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
        assert done.status_code == 201
        assert done.json()["size"] == 6


# --- PartReader / S3-Finalisierung ---------------------------------------------


def test_part_reader_liest_ueber_partgrenzen_und_seekt(tmp_path):
    first = tmp_path / "a.p1"
    second = tmp_path / "a.p2"
    first.write_bytes(b"12345")
    second.write_bytes(b"67890")
    with content.PartReader([str(first), str(second)]) as reader:
        assert reader.read(3) == b"123"
        assert reader.read(4) == b"4567"
        assert reader.tell() == 7
        assert reader.seek(0) == 0
        assert reader.read() == b"1234567890"
        reader.seek(-5, os.SEEK_END)
        assert reader.read() == b"67890"


def test_upload_parts_nutzt_put_object_fuer_kleine_objekte(monkeypatch, tmp_path):
    first = tmp_path / "b.p1"
    first.write_bytes(b"hello ")
    second = tmp_path / "b.p2"
    second.write_bytes(b"world")
    captured: dict = {}

    class FakeClient:
        def put_object(self, **kwargs):
            body = kwargs.pop("Body")
            captured["key"] = kwargs["Key"]
            captured["length"] = kwargs["ContentLength"]
            captured["data"] = body.read()

    monkeypatch.setattr(content, "get_s3_client", lambda: FakeClient())
    _patch_settings(
        monkeypatch,
        s3_multipart_threshold_mb=100,
        s3_multipart_chunksize_mb=16,
        s3_transfer_concurrency=1,
        s3_sse=None,
    )

    digest = hashlib.sha256(b"hello world").hexdigest()
    info = content.upload_parts([str(first), str(second)], digest)

    assert captured["data"] == b"hello world"
    assert captured["key"] == f"cas/{digest[:2]}/{digest}"
    assert captured["length"] == 11
    assert info.size == 11


def test_upload_parts_nutzt_upload_fileobj_fuer_grosse_objekte(monkeypatch, tmp_path):
    first = tmp_path / "c.p1"
    first.write_bytes(b"a" * (600 * 1024))
    second = tmp_path / "c.p2"
    second.write_bytes(b"b" * (600 * 1024))
    captured: dict = {}

    class FakeClient:
        def upload_fileobj(self, body, bucket, key, ExtraArgs=None, Config=None):
            captured["key"] = key
            captured["data"] = body.read()

    monkeypatch.setattr(content, "get_s3_client", lambda: FakeClient())
    _patch_settings(
        monkeypatch,
        s3_multipart_threshold_mb=1,
        s3_multipart_chunksize_mb=1,
        s3_transfer_concurrency=1,
        s3_sse=None,
    )

    digest = hashlib.sha256(b"a" * (600 * 1024) + b"b" * (600 * 1024)).hexdigest()
    info = content.upload_parts([str(first), str(second)], digest)

    assert captured["data"] == b"a" * (600 * 1024) + b"b" * (600 * 1024)
    assert captured["key"] == f"cas/{digest[:2]}/{digest}"
    assert info.size == 1200 * 1024
