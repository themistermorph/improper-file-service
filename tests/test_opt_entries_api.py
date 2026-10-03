"""Performance-Regressionstests der Listen-/Download-Endpunkte.

Ein SQL-Zähler (``before_cursor_execute``) belegt, dass die Query-Anzahl der
Listen nicht linear mit der Zahl der Kinder wächst (Pfad-/Eltern-Aufstiege
werden memoisiert) und dass Reihenfolge, Pfade und Auswahl unverändert bleiben.
"""

from __future__ import annotations

import io
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from ifs import main
from ifs.api.entries import resolve_blob
from ifs.core import content, namespace
from ifs.db import get_engine, get_session_factory
from ifs.models import Blob, BlobStatus

from .conftest import make_blob, make_user


@pytest.fixture
def query_count():
    """Kontextmanager, der alle SQL-Statements auf dem Test-Engine zählt."""

    @contextmanager
    def _counter():
        engine = get_engine()
        statements: list[str] = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            yield statements
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

    return _counter


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> dict:
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_folder(
    client: TestClient, headers: dict, name: str, parent_id: str | None = None
) -> str:
    body: dict = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    response = client.post("/api/folders", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _fake_store_blob(db, local_path, mime=None):
    import hashlib

    with open(local_path, "rb") as handle:
        data = handle.read()
    sha = hashlib.sha256(data).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}", sha256=sha, size=len(data), status=BlobStatus.ready
    )
    db.add(blob)
    db.flush()
    return blob


def _fake_get_object(key, start=None, end=None):
    payload = b"hello opt"
    if start is not None or end is not None:
        payload = payload[start or 0 : (end + 1 if end is not None else None)]
    return {"Body": io.BytesIO(payload), "ContentLength": len(payload)}


def test_get_entries_query_anzahl_nicht_linear_mit_kinderzahl(monkeypatch, query_count):
    """21 zusätzliche Kinder dürfen keine 21 zusätzlichen Queries auslösen."""
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = _login(client)

        # Gemeinsame tiefe Vorfahrenkette beider Vergleichsordner.
        level: str | None = None
        for depth in range(4):
            level = _create_folder(client, headers, f"level-{depth}", level)

        small = _create_folder(client, headers, "small", level)
        big = _create_folder(client, headers, "big", level)
        for index in range(3):
            _create_folder(client, headers, f"s-{index:02d}", small)
        for index in range(24):
            _create_folder(client, headers, f"b-{index:02d}", big)

        with query_count() as small_calls:
            response = client.get(f"/api/entries?parent_id={small}", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 3

        with query_count() as big_calls:
            response = client.get(f"/api/entries?parent_id={big}", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 24

        assert len(big_calls) <= len(small_calls) + 2, (
            f"{len(big_calls)} Queries für 24 Kinder vs. {len(small_calls)} für 3 "
            "- Query-Anzahl wächst offenbar linear pro Kind"
        )


def test_list_entries_reihenfolge_und_pfade(monkeypatch):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        headers = _login(client)
        first = _create_folder(client, headers, "OrdnerA")
        second = _create_folder(client, headers, "OrdnerB", first)
        _create_folder(client, headers, "Zulu", second)
        _create_folder(client, headers, "alpha", second)
        upload = client.put(
            f"/api/uploads/simple?parent_id={second}&name=beta.txt",
            headers=headers,
            content=b"hello opt",
        )
        assert upload.status_code == 201, upload.text

        listing = client.get(f"/api/entries?parent_id={second}", headers=headers).json()
        # Ordner zuerst, danach Dateien; innerhalb case-insensitiv nach Name.
        assert [item["name"] for item in listing] == ["alpha", "Zulu", "beta.txt"]
        assert [item["path"] for item in listing] == [
            "/OrdnerA/OrdnerB/alpha",
            "/OrdnerA/OrdnerB/Zulu",
            "/OrdnerA/OrdnerB/beta.txt",
        ]

        root_listing = client.get("/api/entries", headers=headers).json()
        assert [item["path"] for item in root_listing] == ["/OrdnerA"]


def test_list_folders_pfade_und_query_anzahl(monkeypatch, query_count):
    monkeypatch.setattr(content, "store_blob", _fake_store_blob)

    with TestClient(main.app) as client:
        headers = _login(client)
        parent = _create_folder(client, headers, "Ebene")
        few = _create_folder(client, headers, "few", parent)
        for index in range(3):
            _create_folder(client, headers, f"f-{index}", few)

        client.get("/api/folders", headers=headers)
        with query_count() as few_calls:
            client.get("/api/folders", headers=headers)

        many = _create_folder(client, headers, "many", parent)
        for index in range(40):
            _create_folder(client, headers, f"m-{index:02d}", many)

        with query_count() as many_calls:
            listing = client.get("/api/folders", headers=headers).json()

        by_name = {item["name"]: item["path"] for item in listing}
        assert by_name["f-0"] == "/Ebene/few/f-0"
        assert by_name["m-39"] == "/Ebene/many/m-39"
        # Pfade werden memoisiert statt zweimal je Ordner (Sortierung + Ausgabe)
        # neu aufgestiegen; 37 zusätzliche Ordner kosten keine Queries.
        assert len(many_calls) <= len(few_calls) + 2, (
            f"{len(many_calls)} Queries für 44 Ordner vs. {len(few_calls)} für 4"
        )


def test_shared_oberste_einstiegspunkte_und_reihenfolge():
    with TestClient(main.app) as client:
        admin = _login(client)
        bob_id = client.post(
            "/api/users", headers=admin, json={"username": "bob-opt", "password": "geheim123"}
        ).json()["id"]
        client.post(
            "/api/users", headers=admin, json={"username": "alice-opt", "password": "geheim123"}
        )
        alice = _login(client, "alice-opt", "geheim123")
        bob = _login(client, "bob-opt", "geheim123")

        team = _create_folder(client, alice, "team")
        nested = _create_folder(client, alice, "nested", team)
        solo = _create_folder(client, alice, "solo")
        for entry_id in (team, nested, solo):
            response = client.post(
                f"/api/entries/{entry_id}/acl",
                headers=alice,
                json={"principal_type": "user", "principal_id": bob_id, "perms": ["read"]},
            )
            assert response.status_code == 201, response.text

        # Eigener Eintrag mit ACL auf sich selbst: darf nicht auftauchen.
        mine = _create_folder(client, bob, "mine")
        response = client.post(
            f"/api/entries/{mine}/acl",
            headers=bob,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["read"]},
        )
        assert response.status_code == 201, response.text

        shared = client.get("/api/shared", headers=bob).json()
        # Nur oberste Einstiegspunkte (nested ist über team erreichbar),
        # sortiert nach Pfad; der eigene Eintrag fehlt.
        assert [item["name"] for item in shared] == ["solo", "team"]
        assert [item["path"] for item in shared] == ["/solo", "/team"]


def test_trash_pfade_und_reihenfolge():
    with TestClient(main.app) as client:
        headers = _login(client)
        parent = _create_folder(client, headers, "TrashBase")
        first = _create_folder(client, headers, "first", parent)
        second = _create_folder(client, headers, "second", parent)

        assert client.delete(f"/api/entries/{first}", headers=headers).status_code == 204
        assert client.delete(f"/api/entries/{second}", headers=headers).status_code == 204

        listing = client.get("/api/trash", headers=headers).json()
        # Neueste Löschung zuerst; Pfade trotz gelöschter Einträge korrekt.
        assert [item["name"] for item in listing] == ["second", "first"]
        assert [item["path"] for item in listing] == [
            "/TrashBase/second",
            "/TrashBase/first",
        ]
        assert client.get("/api/trash/count", headers=headers).json()["count"] == 2


def test_resolve_blob_nur_eine_query(db, query_count):
    """Version + Blob werden in genau einer Abfrage geladen."""
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    blob = make_blob(db, b"hello opt")
    entry = namespace.apply_write(db, root, "a.txt", user.id, blob)
    entry_id, blob_id = entry.id, blob.id
    db.commit()

    # Frische Session: Erst dann muss der alte Pfad Version und Blob wirklich
    # zweimal aus der Datenbank laden (Identity-Map-Treffer zählen nicht).
    with get_session_factory()() as fresh:
        fresh_entry = namespace.get_entry(fresh, entry_id)
        with query_count() as calls:
            resolved = resolve_blob(fresh, fresh_entry)

        assert resolved.id == blob_id
        assert len(calls) == 1, f"resolve_blob löst {len(calls)} Queries aus (erwartet: 1)"
