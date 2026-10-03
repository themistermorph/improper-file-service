"""Performance-/Verhaltenstests für Shares-Liste, Bulk-Aktionen und ZIP-Archiv.

Der Query-Zähler nutzt SQLAlchemys ``before_cursor_execute``: Die Zahl der
SQL-Abfragen darf bei der Shares-Liste bzw. beim Bulk-Widerruf nicht mehr
linear mit der Anzahl der Einträge wachsen. Die Verhaltenstests stellen
sicher, dass ZIP-Inhalte (Struktur, Ausweichnamen) und Bulk-Ergebnisse
(Teilerfolge) unverändert bleiben.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from ifs import main
from ifs.core import archive, content, namespace
from ifs.db import get_engine, session_scope
from ifs.models import Blob, BlobStatus, Entry, Share, User

from .conftest import make_user


@pytest.fixture
def query_counter():
    """Zählt die SQL-Statements der Test-Engine (bis das Fixture endet)."""
    engine = get_engine()
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _login(client) -> str:
    return client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["access_token"]


def _admin(db) -> User:
    return db.execute(select(User).where(User.username == "admin")).scalars().one()


def _make_blob(db, name: str) -> Blob:
    blob = Blob(
        storage_key=f"cas/{name}",
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        size=1,
        status=BlobStatus.ready,
    )
    db.add(blob)
    db.flush()
    return blob


# --- Shares-Liste: Query-Zahl darf nicht mit N wachsen -------------------------


def test_shares_liste_queryzahl_konstant(query_counter):
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}

        with session_scope() as db:
            admin = _admin(db)
            root = namespace.ensure_root(db, admin.id)
            perf = namespace.create_folder(db, root, "perf", admin.id)
            mid = namespace.create_folder(db, perf, "mid", admin.id)
            blob = _make_blob(db, "opt-liste")
            for index in range(15):
                entry = namespace.apply_write(
                    db, mid, f"f{index:03d}.txt", admin.id, blob, "text/plain"
                )
                db.add(
                    Share(token=f"tok{index:04d}", entry_id=entry.id, created_by=admin.id)
                )
            mid_id = mid.id

        query_counter.clear()
        first = client.get("/api/shares", headers=headers)
        first_count = len(query_counter)
        assert first.status_code == 200 and len(first.json()) == 15
        # Untergrenze: Der Zähler muss die Abfragen der Anfrage wirklich sehen.
        assert first_count >= 4, first_count

        # 30 weitere Freigaben: Die Query-Zahl muss konstant bleiben.
        with session_scope() as db:
            admin = _admin(db)
            mid = namespace.get_entry(db, mid_id)
            blob = db.execute(
                select(Blob).where(Blob.storage_key == "cas/opt-liste")
            ).scalars().one()
            for index in range(15, 45):
                entry = namespace.apply_write(
                    db, mid, f"f{index:03d}.txt", admin.id, blob, "text/plain"
                )
                db.add(
                    Share(token=f"tok{index:04d}", entry_id=entry.id, created_by=admin.id)
                )

        query_counter.clear()
        second = client.get("/api/shares", headers=headers)
        second_count = len(query_counter)
        assert second.status_code == 200, second.text
        listing = {item["token"]: item for item in second.json()}
        assert len(listing) == 45

        # Inhaltlich unverändert: Name, Pfad, Typ und Ersteller je Zeile.
        sample = listing["tok0000"]
        assert sample["entry_name"] == "f000.txt"
        assert sample["entry_path"] == "/perf/mid/f000.txt"
        assert sample["entry_type"] == "file"
        assert sample["created_by_username"] == "admin"
        assert all(item["entry_path"].startswith("/perf/mid/") for item in listing.values())

        # Vorher: ~2 Abfragen je Freigabe; jetzt konstant (Baumtiefe + 5).
        assert second_count <= first_count + 3, (first_count, second_count)
        assert second_count <= 15, f"{second_count} Queries für 45 Freigaben"


# --- Bulk-Widerruf: Query-Zahl je Token entfällt -------------------------------


def test_bulk_revoke_queryzahl_ergebnis_und_audit(query_counter):
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}

        with session_scope() as db:
            admin = _admin(db)
            root = namespace.ensure_root(db, admin.id)
            folder = namespace.create_folder(db, root, "revoke", admin.id)
            blob = _make_blob(db, "opt-revoke")
            tokens = []
            for index in range(20):
                entry = namespace.apply_write(
                    db, folder, f"r{index:03d}.txt", admin.id, blob, "text/plain"
                )
                token = f"rev-{index:04d}"
                db.add(Share(token=token, entry_id=entry.id, created_by=admin.id))
                tokens.append(token)

        payload = {"tokens": [*tokens, "unbekannt", tokens[0]]}
        query_counter.clear()
        response = client.post("/api/shares/bulk/revoke", headers=headers, json=payload)
        measured = len(query_counter)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] == 20
        # Unbekanntes Token und Duplikat landen (wie zuvor) in `failed`.
        assert body["failed"] == ["unbekannt", tokens[0]]
        # Vorher: ~5 Abfragen je Token; jetzt nur noch die Löschoperationen.
        assert measured >= 10, measured
        assert measured <= 3 * len(tokens) + 25, f"{measured} Queries für 20 Tokens"

        assert client.get("/api/shares", headers=headers).json() == []
        rows = client.get("/api/audit?action=share.bulk_revoke", headers=headers).json()
        assert rows and rows[0]["details"] == {"ok": 20, "failed": 2}


# --- Bulk-Löschen: kein Einzel-Lookup je Eintrag, Partialergebnis bleibt -------


def test_bulk_delete_queryzahl_und_partialsemantik(query_counter):
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}

        with session_scope() as db:
            admin = _admin(db)
            root = namespace.ensure_root(db, admin.id)
            folder = namespace.create_folder(db, root, "bulk", admin.id)
            bob = make_user(db, "bob-perf")
            bob_root = namespace.ensure_root(db, bob.id)
            blob = _make_blob(db, "opt-bulk")
            ids = []
            for index in range(28):
                entry = namespace.apply_write(
                    db, folder, f"d{index:03d}.txt", admin.id, blob, "text/plain"
                )
                ids.append(str(entry.id))
            foreign = namespace.apply_write(
                db, bob_root, "secret.txt", bob.id, blob, "text/plain"
            )
            foreign_id = str(foreign.id)

        unknown = str(uuid4())
        query_counter.clear()
        response = client.post(
            "/api/bulk/delete",
            headers=headers,
            json={"ids": [*ids, foreign_id, unknown]},
        )
        measured = list(query_counter)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] == 28
        assert body["failed"] == [foreign_id, unknown]

        # Einzel-Lookups der Form `entries.id = ?` gibt es nicht mehr; nur die
        # Rechteprüfung des fremden Eintrags darf die Tabelle einzeln lesen.
        single_lookups = [
            statement
            for statement in measured
            if "FROM entries" in statement
            and "entries.id = ?" in statement
            and "parent_id" not in statement
        ]
        assert len(measured) >= 20, len(measured)
        assert len(single_lookups) <= 5, single_lookups

        # Teilerfolg unverändert: eigene Einträge gelöscht, fremder bleibt aktiv.
        with session_scope() as db:
            assert db.get(Entry, UUID(ids[0])).trashed_at is not None
            assert db.get(Entry, UUID(foreign_id)).trashed_at is None


# --- ZIP-Erzeugung: Struktur/Inhalt/Ausweichnamen unverändert ------------------


def _zip_helpers():
    """Fake-Objektspeicher: speichert Inhalte je storage_key für get_object."""
    by_key: dict[str, bytes] = {}

    def fake_store_blob(db, local_path, mime=None):
        data = open(local_path, "rb").read()
        sha = hashlib.sha256(data).hexdigest()
        key = f"cas/{sha[:2]}/{sha}"
        by_key[key] = data
        blob = Blob(storage_key=key, sha256=sha, size=len(data), status=BlobStatus.ready)
        db.add(blob)
        db.flush()
        return blob

    def fake_get_object(key, start=None, end=None):
        data = by_key[key]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    return by_key, fake_store_blob, fake_get_object


def test_build_zip_struktur_inhalt_und_queryzahl(monkeypatch, query_counter):
    _, fake_store_blob, fake_get_object = _zip_helpers()
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", fake_get_object)

    contents: dict[str, bytes] = {}
    with session_scope() as db:
        owner = make_user(db, "zip-owner")
        root = namespace.ensure_root(db, owner.id)
        p1 = namespace.create_folder(db, root, "p1", owner.id)
        p2 = namespace.create_folder(db, root, "p2", owner.id)
        first = namespace.create_folder(db, p1, "bundle", owner.id)
        second = namespace.create_folder(db, p2, "bundle", owner.id)
        sub = namespace.create_folder(db, first, "sub", owner.id)

        def write(parent, name: str, data: bytes) -> None:
            spool = content.new_spool_path()
            try:
                with open(spool, "wb") as handle:
                    handle.write(data)
                blob = content.store_blob(db, spool, "text/plain")
            finally:
                content.remove_spool(spool)
            namespace.apply_write(db, parent, name, owner.id, blob, "text/plain")
            contents[name] = data

        for index in range(5):
            write(first, f"f{index:02d}.txt", f"inhalt-{index}".encode())
        write(sub, "g.txt", b"sub-inhalt")
        for index in range(2):
            write(second, f"f1{index}.txt", f"inhalt-1{index}".encode())
        first_id, second_id, sub_name = first.id, second.id, sub.name

    # Bauen in einer frischen Session, wie es der Hintergrund-Job tut.
    with session_scope() as db:
        first = namespace.get_entry(db, first_id)
        second = namespace.get_entry(db, second_id)
        query_counter.clear()
        path = archive.build_zip(db, [first, second])
        measured = len(query_counter)
        try:
            with zipfile.ZipFile(path) as zf:
                expected_names = {
                    "bundle/",
                    *{f"bundle/f{i:02d}.txt" for i in range(5)},
                    f"bundle/{sub_name}/",
                    f"bundle/{sub_name}/g.txt",
                    "bundle (2)/",
                    "bundle (2)/f10.txt",
                    "bundle (2)/f11.txt",
                }
                assert set(zf.namelist()) == expected_names
                assert zf.read("bundle/f03.txt") == contents["f03.txt"]
                assert zf.read(f"bundle/{sub_name}/g.txt") == contents["g.txt"]
                assert zf.read("bundle (2)/f11.txt") == contents["f11.txt"]
        finally:
            archive.remove_zip(path)

    # Vorher: zwei Metadaten-Abfragen je Datei; jetzt eine (Join) plus Ordner.
    file_count = 8
    assert measured >= file_count, measured
    assert measured <= file_count + 8, f"{measured} Queries für {file_count} Dateien"


def test_share_zip_download_revoke_und_archiv_token(monkeypatch):
    _, fake_store_blob, fake_get_object = _zip_helpers()
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post(
            "/api/folders", headers=headers, json={"name": "opt-share"}
        ).json()["id"]
        sub = client.post(
            "/api/folders", headers=headers, json={"parent_id": folder, "name": "sub"}
        ).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=a.txt",
            headers=headers,
            content=b"eins",
        )
        client.put(
            f"/api/uploads/simple?parent_id={sub}&name=b.txt",
            headers=headers,
            content=b"zwei",
        )

        token = client.post("/api/shares", headers=headers, json={"entry_id": folder}).json()[
            "token"
        ]
        response = client.get(f"/api/shares/{token}/content")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert set(zf.namelist()) == {
                "opt-share/",
                "opt-share/a.txt",
                "opt-share/sub/",
                "opt-share/sub/b.txt",
            }
            assert zf.read("opt-share/a.txt") == b"eins"
            assert zf.read("opt-share/sub/b.txt") == b"zwei"
        assert client.get(f"/api/shares/{token}").json()["downloads"] == 1

        # Archiv-Token (Altpfad) liefert dieselbe Struktur.
        created = client.post(f"/api/entries/{folder}/archive-token", headers=headers)
        assert created.status_code == 200, created.text
        archive_response = client.get(created.json()["url"])
        assert archive_response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as zf:
            assert set(zf.namelist()) == {
                "opt-share/",
                "opt-share/a.txt",
                "opt-share/sub/",
                "opt-share/sub/b.txt",
            }
            assert zf.read("opt-share/a.txt") == b"eins"

        # Widerruf: Link ist danach endgültig tot.
        assert client.delete(f"/api/shares/{token}", headers=headers).status_code == 204
        assert client.get(f"/api/shares/{token}").status_code == 404
        assert client.get(f"/api/shares/{token}/content").status_code == 404


def test_archive_job_endwerte_und_zip_inhalt(monkeypatch):
    _, fake_store_blob, fake_get_object = _zip_helpers()
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", fake_get_object)

    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {_login(client)}"}
        folder = client.post(
            "/api/folders", headers=headers, json={"name": "opt-job"}
        ).json()["id"]
        expected: dict[str, bytes] = {}
        for index in range(12):
            payload = f"job-{index:02d}".encode()
            name = f"j{index:02d}.txt"
            client.put(
                f"/api/uploads/simple?parent_id={folder}&name={name}",
                headers=headers,
                content=payload,
            )
            expected[name] = payload

        created = client.post(
            "/api/archive/jobs", headers=headers, json={"entry_ids": [folder]}
        )
        assert created.status_code == 201, created.text
        data = created.json()
        assert data["total_files"] == 12
        assert data["total_bytes"] == sum(len(payload) for payload in expected.values())

        deadline = time.time() + 10
        status = {}
        while time.time() < deadline:
            status = client.get(f"/api/archive/jobs/{data['token']}").json()
            if status.get("status") in ("ready", "failed"):
                break
            time.sleep(0.05)
        assert status["status"] == "ready", status
        # Endwerte des gedrosselten Fortschritts bleiben exakt.
        assert status["files_done"] == status["total_files"] == 12
        assert status["bytes_done"] == status["total_bytes"] == data["total_bytes"]

        response = client.get(data["url"])
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert set(zf.namelist()) == {"opt-job/", *(f"opt-job/{name}" for name in expected)}
            for name, payload in expected.items():
                assert zf.read(f"opt-job/{name}") == payload
