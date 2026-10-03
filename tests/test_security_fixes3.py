"""Regressionstests für die Findings S1–S4 aus dem vierten Sicherheits-Review."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import authz, content, namespace
from ifs.errors import PermissionDenied
from ifs.ftp.fs import IFSFilesystem
from ifs.models import ACL, PrincipalType

from .conftest import make_blob, make_user
from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"payload")


def _make_user(client, admin, username="bob", password="geheim123"):
    user_id = client.post(
        "/api/users", headers=admin, json={"username": username, "password": password}
    ).json()["id"]
    headers = {"Authorization": f"Bearer {login(client, username, password)}"}
    return user_id, headers


def _grant(client, owner_headers, entry_id, principal_id, perms):
    response = client.post(
        f"/api/entries/{entry_id}/acl",
        headers=owner_headers,
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "perms": perms,
        },
    )
    assert response.status_code == 201, response.text
    return response


# --- S1: `share` ohne `read` darf keinen Link erzeugen -------------------------


def test_s1_share_requires_read(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "alice-s1")
        bob_id, bob = _make_user(client, admin, "bob-s1")

        folder = client.post("/api/folders", headers=alice, json={"name": "team"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=secret.txt",
            headers=alice,
            content=b"GEHEIM",
        )

        # Nur `share`: Bob darf weder lesen noch einen Link anlegen.
        _grant(client, alice, folder, bob_id, ["share"])
        assert client.get(f"/api/entries/{folder}", headers=bob).status_code == 403
        denied = client.post("/api/shares", headers=bob, json={"entry_id": folder})
        assert denied.status_code == 403, denied.text

        # Mit `read` + `share` bleibt das Teilen möglich.
        _grant(client, alice, folder, bob_id, ["read", "share"])
        created = client.post("/api/shares", headers=bob, json={"entry_id": folder})
        assert created.status_code == 201, created.text
        assert (
            client.get(f"/api/shares/{created.json()['token']}/content").status_code == 200
        )


# --- S2: Admin kann die Per-User-Isolation nicht per API aushebeln -------------


def test_s2_admin_cannot_self_assign_resource_role():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        admin_id = client.get("/api/me", headers=admin).json()["id"]
        _alice_id, alice = _make_user(client, admin, "alice-s2")

        alice_folder = client.post(
            "/api/folders", headers=alice, json={"name": "private"}
        ).json()["id"]
        alice_root = client.get("/api/root", headers=alice).json()["id"]
        manager = next(
            role["id"]
            for role in client.get("/api/roles?scope=resource", headers=admin).json()
            if role["name"] == "manager"
        )

        denied = client.post(
            f"/api/roles/{manager}/assignments",
            headers=admin,
            json={"principal_type": "user", "principal_id": admin_id, "entry_id": alice_root},
        )
        assert denied.status_code == 403, denied.text
        # Isolation bleibt bestehen.
        assert client.get(f"/api/entries/{alice_folder}", headers=admin).status_code == 403

        # Auf eigenen Einträgen darf der Admin weiterhin Ressourcenrollen vergeben.
        own_folder = client.post("/api/folders", headers=admin, json={"name": "own"}).json()["id"]
        allowed = client.post(
            f"/api/roles/{manager}/assignments",
            headers=admin,
            json={"principal_type": "user", "principal_id": admin_id, "entry_id": own_folder},
        )
        assert allowed.status_code == 201, allowed.text


def test_s2_admin_can_only_revoke_foreign_share(monkeypatch):
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        _alice_id, alice = _make_user(client, admin, "alice-s2b")

        folder = client.post("/api/folders", headers=alice, json={"name": "team"}).json()["id"]
        entry = client.put(
            f"/api/uploads/simple?parent_id={folder}&name=secret.txt",
            headers=alice,
            content=b"GEHEIM",
        ).json()
        token = client.post(
            "/api/shares",
            headers=alice,
            json={"entry_id": entry["id"], "password": "pw"},
        ).json()["token"]

        # Admin darf fremde Freigaben weder ändern noch Passwörter entfernen.
        patched = client.patch(f"/api/shares/{token}", headers=admin, json={"password": ""})
        assert patched.status_code == 403, patched.text
        assert client.get(f"/api/shares/{token}/content").status_code == 401

        # Widerrufen (Missbrauchsschutz) bleibt möglich.
        assert client.delete(f"/api/shares/{token}", headers=admin).status_code == 204
        assert client.get(f"/api/shares/{token}").status_code == 404


# --- S3: FTP APPE/REST lädt fremden Inhalt nur mit `read` ----------------------


def test_s3_ftp_append_requires_read(monkeypatch):
    from ifs.db import session_scope
    from ifs.models import Entry

    with session_scope() as db:
        owner = make_user(db, "owner-s3")
        writer_user = make_user(db, "writer-s3")
        root = namespace.ensure_root(db, owner.id)
        docs = namespace.create_folder(db, root, "docs", owner.id)
        file_entry = namespace.apply_write(
            db, docs, "file.txt", owner.id, make_blob(db, b"secret")
        )
        docs_id, file_id = docs.id, file_entry.id
        db.add(
            ACL(
                entry_id=docs.id,
                principal_type=PrincipalType.user,
                principal_id=writer_user.id,
                perms=authz.WRITE,
                inherited=False,
            )
        )
        db.flush()

    calls = {"count": 0}

    def _get_object(key, start=None, end=None):
        calls["count"] += 1
        return {"Body": io.BytesIO(b"secret"), "ContentLength": 6}

    monkeypatch.setattr(content, "get_object", _get_object)

    fs = IFSFilesystem.__new__(IFSFilesystem)
    fs.cmd_channel = SimpleNamespace(username="writer-s3")
    fs._pending = {}

    # Fremder Baum (z. B. via ACL freigegeben) wird hier gezielt aufgelöst.
    def fake_resolve(db, path):
        mapping = {"/docs": docs_id, "/docs/file.txt": file_id}
        entry_id = mapping.get(path)
        return db.get(Entry, entry_id) if entry_id else None

    fs._resolve = fake_resolve

    # write-only: Vorladen wird abgelehnt, der Blob wird nicht gelesen.
    with pytest.raises(PermissionDenied):
        fs._open_writer("/docs/file.txt", "a")
    assert calls["count"] == 0

    # Mit read+write bleibt APPE funktionsfähig.
    with session_scope() as db:
        db.add(
            ACL(
                entry_id=docs_id,
                principal_type=PrincipalType.user,
                principal_id=writer_user.id,
                perms=authz.READ | authz.WRITE,
                inherited=False,
            )
        )
        db.flush()

    writer = fs._open_writer("/docs/file.txt", "a")
    try:
        assert calls["count"] == 1
    finally:
        writer.close()
        fs.cleanup()


# --- S4: Längen-/Formatgrenzen -------------------------------------------------


def test_s4_bulk_tokens_and_sha256_are_bounded():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        root = client.get("/api/root", headers=admin).json()["id"]

        too_long = client.post(
            "/api/shares/bulk/revoke", headers=admin, json={"tokens": ["x" * 65]}
        )
        assert too_long.status_code == 422

        bad_sha = client.post(
            "/api/uploads",
            headers=admin,
            json={"parent_id": root, "name": "x.bin", "sha256": "not-a-sha"},
        )
        assert bad_sha.status_code == 422

        ok = client.post(
            "/api/uploads",
            headers=admin,
            json={"parent_id": root, "name": "y.bin", "sha256": "0" * 64},
        )
        assert ok.status_code == 201, ok.text
