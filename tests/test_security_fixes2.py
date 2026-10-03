"""Regressionstests für die Findings F1–F4 aus dem dritten Sicherheits-Review."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from ifs import main
from ifs.core import authz, content, namespace
from ifs.core import quota as quota_core
from ifs.db import session_scope
from ifs.models import ACL, Entry

from .conftest import make_user
from .helpers import fake_store_blob, fixed_get_object, login

_fake_get_object = fixed_get_object(b"payload")


def _make_user(client, admin, username="bob", password="geheim123"):
    user_id = client.post(
        "/api/users", headers=admin, json={"username": username, "password": password}
    ).json()["id"]
    headers = {"Authorization": f"Bearer {login(client, username, password)}"}
    return user_id, headers


# --- F1: Share-Upload umgeht das `write`-Recht ---------------------------------


def test_f1_sharer_cannot_create_drop_link(monkeypatch):
    """`allow_upload` erfordert das `write`-Recht am Eintrag."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id, bob = _make_user(client, admin)

        folder = client.post(
            "/api/folders", headers=admin, json={"name": "dropzone"}
        ).json()["id"]
        sharer_id = next(
            role["id"]
            for role in client.get("/api/roles?scope=resource", headers=admin).json()
            if role["name"] == "sharer"
        )
        assigned = client.post(
            f"/api/entries/{folder}/roles",
            headers=admin,
            json={"role_id": sharer_id, "principal_type": "user", "principal_id": bob_id},
        )
        assert assigned.status_code == 201, assigned.text

        # Kontrolle: Die Rolle `sharer` umfasst kein `write`.
        denied_folder = client.post(
            "/api/folders", headers=bob, json={"parent_id": folder, "name": "x"}
        )
        assert denied_folder.status_code == 403

        # Drop-Link mit Upload ist ohne `write` verboten ...
        denied = client.post(
            "/api/shares", headers=bob, json={"entry_id": folder, "allow_upload": True}
        )
        assert denied.status_code == 403, denied.text

        # ... eine reine Lese-/Download-Freigabe bleibt erlaubt.
        created = client.post("/api/shares", headers=bob, json={"entry_id": folder})
        assert created.status_code == 201, created.text
        assert created.json()["allow_upload"] is False
        assert created.json()["overwrite"] is False
        token = created.json()["token"]

        # Auch per PATCH darf Bob den Upload nicht scharfschalten.
        denied_patch = client.patch(
            f"/api/shares/{token}", headers=bob, json={"allow_upload": True}
        )
        assert denied_patch.status_code == 403

        # Abschalten (hier: bereits aus) bleibt ohne `write` möglich.
        allowed_patch = client.patch(
            f"/api/shares/{token}", headers=bob, json={"allow_upload": False}
        )
        assert allowed_patch.status_code == 200, allowed_patch.text


def test_f1_upload_blocked_after_write_revoked(monkeypatch):
    """Nach Entzug des `write`-Rechts ist der anonyme Upload sofort gesperrt."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id, bob = _make_user(client, admin)

        folder = client.post(
            "/api/folders", headers=admin, json={"name": "drop"}
        ).json()["id"]
        granted = client.post(
            f"/api/entries/{folder}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "write", "share"],
            },
        )
        assert granted.status_code == 201, granted.text

        created = client.post(
            "/api/shares", headers=bob, json={"entry_id": folder, "allow_upload": True}
        )
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        assert client.post(
            f"/api/shares/{token}/upload?name=a.txt", content=b"a"
        ).status_code == 201

        # Schreibrecht direkt in der DB entziehen.
        with session_scope() as db:
            db.execute(
                delete(ACL).where(
                    ACL.entry_id == UUID(folder), ACL.principal_id == UUID(bob_id)
                )
            )
            db.flush()

        blocked = client.post(f"/api/shares/{token}/upload?name=b.txt", content=b"b")
        assert blocked.status_code == 403, blocked.text


def test_f1_overwrite_is_share_property(monkeypatch):
    """Teil B: Anonymes Überschreiben nur bei `overwrite=true` an der Freigabe."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "keep"}).json()["id"]
        client.put(
            f"/api/uploads/simple?parent_id={folder}&name=keep.txt",
            headers=admin,
            content=b"alt",
        )
        token = client.post(
            "/api/shares", headers=admin, json={"entry_id": folder, "allow_upload": True}
        ).json()["token"]

        # Der Request-Parameter allein darf nicht überschreiben.
        attempted = client.post(
            f"/api/shares/{token}/upload?name=keep.txt&overwrite=true", content=b"neu"
        )
        assert attempted.status_code == 201, attempted.text
        assert attempted.json()["name"] == "keep (2).txt"

        # Erst die Freigabe-Eigenschaft erlaubt das Ersetzen.
        patched = client.patch(
            f"/api/shares/{token}", headers=admin, json={"overwrite": True}
        )
        assert patched.status_code == 200, patched.text
        forced = client.post(
            f"/api/shares/{token}/upload?name=keep.txt", content=b"neu2"
        )
        assert forced.status_code == 201, forced.text
        assert forced.json()["name"] == "keep.txt"
        assert forced.json()["size"] == len(b"neu2")

        names = [
            entry["name"]
            for entry in client.get(
                f"/api/entries?parent_id={folder}", headers=admin
            ).json()
        ]
        assert sorted(names) == ["keep (2).txt", "keep.txt"]


# --- F2: Verschieben umgeht das `read`-Recht -----------------------------------


def test_f2_move_requires_read_on_source(monkeypatch):
    """Verschieben verlangt zusätzlich `read` auf der Quelle."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id, bob = _make_user(client, admin)

        private = client.post(
            "/api/folders", headers=admin, json={"name": "private"}
        ).json()["id"]
        exfil = client.post("/api/folders", headers=admin, json={"name": "exfil"}).json()["id"]
        client.post(
            f"/api/entries/{private}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["write"]},
        )
        client.post(
            f"/api/entries/{exfil}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "write"],
            },
        )
        secret = client.put(
            f"/api/uploads/simple?parent_id={private}&name=secret.txt",
            headers=admin,
            content=b"secret",
        ).json()

        # Kontrolle: write-only bedeutet kein Lesezugriff.
        assert client.get(f"/api/entries/{secret['id']}", headers=bob).status_code == 403

        moved = client.post(
            f"/api/entries/{secret['id']}/move", headers=bob, json={"parent_id": exfil}
        )
        assert moved.status_code == 403, moved.text
        assert (
            client.get(f"/api/entries/{secret['id']}", headers=admin).json()["parent_id"]
            == private
        )

        bulk = client.post(
            "/api/bulk/move", headers=bob, json={"ids": [secret["id"]], "parent_id": exfil}
        )
        assert bulk.json() == {"ok": 0, "failed": [secret["id"]]}

        # Mit `read` auf der Quelle ist der Move weiterhin erlaubt.
        client.post(
            f"/api/entries/{private}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "write"],
            },
        )
        ok = client.post(
            f"/api/entries/{secret['id']}/move", headers=bob, json={"parent_id": exfil}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["parent_id"] == exfil


def test_f2_restore_requires_read_on_source(monkeypatch):
    """Gleiche Klasse wie F2: Restore in einen lesbaren Ordner verlangt `read`."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        bob_id, bob = _make_user(client, admin)

        private = client.post("/api/folders", headers=admin, json={"name": "rest-priv"}).json()[
            "id"
        ]
        exfil = client.post("/api/folders", headers=admin, json={"name": "rest-exfil"}).json()[
            "id"
        ]
        # Bob darf löschen (delete), aber nicht lesen; /rest-exfil darf er lesen.
        assert client.post(
            f"/api/entries/{private}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": bob_id, "perms": ["delete"]},
        ).status_code == 201
        assert client.post(
            f"/api/entries/{exfil}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "write"],
            },
        ).status_code == 201

        secret = client.put(
            f"/api/uploads/simple?parent_id={private}&name=secret.txt",
            headers=admin,
            content=b"secret",
        ).json()
        # Kontrolle: kein Lesezugriff auf den Inhalt.
        assert client.get(f"/api/entries/{secret['id']}/content", headers=bob).status_code == 403

        # Admin entsorgt die Datei; Bob sieht sie über sein delete-Recht im Papierkorb.
        assert client.delete(f"/api/entries/{secret['id']}", headers=admin).status_code == 204
        assert any(
            item["id"] == secret["id"] for item in client.get("/api/trash", headers=bob).json()
        )

        # Restore in den lesbaren Ordner ist ohne `read` auf der Quelle verboten.
        denied = client.post(
            f"/api/trash/{secret['id']}/restore", headers=bob, json={"parent_id": exfil}
        )
        assert denied.status_code == 403, denied.text

        # Erst mit `read` (und delete) auf der Quelle ist der Restore erlaubt.
        assert client.post(
            f"/api/entries/{private}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": bob_id,
                "perms": ["read", "delete"],
            },
        ).status_code == 201
        restored = client.post(
            f"/api/trash/{secret['id']}/restore", headers=bob, json={"parent_id": exfil}
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["parent_id"] == exfil


# --- F3: Quota-Bypass über Papierkorb-Restore ----------------------------------


def test_f3_restore_respects_quota(monkeypatch):
    """Wiederherstellen prüft die Quota des betroffenen Besitzers erneut."""
    monkeypatch.setattr(content, "store_blob", fake_store_blob)
    monkeypatch.setattr(content, "get_object", _fake_get_object)
    monkeypatch.setattr(
        quota_core, "get_settings", lambda: SimpleNamespace(default_quota_bytes=100)
    )

    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        first = client.post("/api/folders", headers=admin, json={"name": "first"}).json()["id"]
        uploaded = client.put(
            f"/api/uploads/simple?parent_id={first}&name=a.bin",
            headers=admin,
            content=b"x" * 60,
        )
        assert uploaded.status_code == 201, uploaded.text
        assert client.delete(f"/api/entries/{first}", headers=admin).status_code == 204

        second = client.post("/api/folders", headers=admin, json={"name": "second"}).json()["id"]
        assert client.put(
            f"/api/uploads/simple?parent_id={second}&name=b.bin",
            headers=admin,
            content=b"y" * 60,
        ).status_code == 201

        # Restore würde die Quota überschreiten (60 + 60 > 100).
        over = client.post(f"/api/trash/{first}/restore", headers=admin, json={})
        assert over.status_code == 413, over.text
        assert any(
            item["id"] == first
            for item in client.get("/api/trash", headers=admin).json()
        )

        # Nach Freigabe von Platz klappt die Wiederherstellung.
        assert client.delete(f"/api/entries/{second}", headers=admin).status_code == 204
        ok = client.post(f"/api/trash/{first}/restore", headers=admin, json={})
        assert ok.status_code == 200, ok.text


# --- F4: Zyklus-Guard ----------------------------------------------------------


def test_f4_cycle_guard_terminates(db):
    """Auch bei einem bereits vorhandenen Zyklus terminieren die Pfadfunktionen."""
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    root = namespace.ensure_root(db, owner.id)
    a = namespace.create_folder(db, root, "a", owner.id)
    b = namespace.create_folder(db, root, "b", owner.id)

    # Zyklus direkt in der DB erzeugen (entspricht dem Ergebnis der früheren Race).
    db.execute(update(Entry).where(Entry.id == a.id).values(parent_id=b.id))
    db.execute(update(Entry).where(Entry.id == b.id).values(parent_id=a.id))
    db.flush()
    db.expire_all()

    entry = db.get(Entry, a.id)
    assert namespace.path_of(db, entry).startswith("/")
    assert authz.can(db, other, "read", entry) is False
    assert namespace._is_descendant(db.get(Entry, b.id), UUID(int=0)) is False


# --- H3/H4: optionale Härtung aus dem dritten Review ---------------------------


def test_h3_version_hides_environment():
    with TestClient(main.app) as client:
        response = client.get("/version")
        assert response.status_code == 200
        data = response.json()
        assert "environment" not in data
        assert data["version"]


def test_h4_permission_lists_are_bounded():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "acls"}).json()["id"]

        too_many = client.post(
            f"/api/entries/{folder}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": str(UUID(int=1)),
                "perms": ["read"] * 6,
            },
        )
        assert too_many.status_code == 422

        role = client.post(
            "/api/roles",
            headers=admin,
            json={"name": "toomany", "permissions": ["read"] * 6},
        )
        assert role.status_code == 422

        # Fünf (alle bekannten) Rechte bleiben erlaubt.
        ok = client.post(
            "/api/roles",
            headers=admin,
            json={
                "name": "allfive",
                "permissions": ["read", "write", "delete", "share", "admin"],
            },
        )
        assert ok.status_code == 201, ok.text
