"""Tests für die Kontoverwaltung (Kern und API)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import accounts
from ifs.errors import BadRequest, Conflict

from .conftest import make_user


def test_create_user_and_duplicate(db):
    user = accounts.create_user(db, username="alice", password="geheim123")
    assert user.username == "alice"
    assert user.token_version == 0
    with pytest.raises(Conflict):
        accounts.create_user(db, username="alice", password="geheim123")


def test_password_policy(db):
    with pytest.raises(BadRequest):
        accounts.create_user(db, username="bob", password="kurz")


def test_change_password_bumps_token_version(db):
    user = make_user(db, "carol")
    before = user.token_version
    accounts.change_password(db, user, "secret", "neuespasswort")
    assert user.token_version == before + 1
    with pytest.raises(BadRequest):
        accounts.change_password(db, user, "falsch", "nochneuer1")


def test_last_admin_protection(db):
    admin = make_user(db, "admin1", is_admin=True)
    with pytest.raises(Conflict):
        accounts.deactivate_user(db, admin)

    make_user(db, "admin2", is_admin=True)
    accounts.deactivate_user(db, admin)
    assert admin.is_active is False


def test_delete_requires_ownership_transfer(db):
    from ifs.core import namespace

    owner = make_user(db, "owner")
    root = namespace.ensure_root(db, owner.id)
    namespace.create_folder(db, root, "docs", owner.id)

    with pytest.raises(Conflict):
        accounts.delete_user(db, owner)

    target = make_user(db, "target")
    accounts.delete_user(db, owner, transfer_to=target)
    # Inhalt wurde unter "<username>" in die Zielwurzel gemergt.
    target_root = namespace.get_root(db, target.id)
    assert target_root is not None
    moved = namespace.find_child(db, target_root.id, "owner")
    assert moved is not None
    assert namespace.find_child(db, moved.id, "docs") is not None
    assert namespace.get_root(db, owner.id) is None


def test_group_membership(db):
    from ifs.models import Group

    user = make_user(db, "dave")
    group = Group(name="team")
    db.add(group)
    db.flush()
    accounts.add_to_group(db, user, group)
    assert group in user.groups
    accounts.remove_from_group(db, user, group)
    assert group not in user.groups


# --- API ------------------------------------------------------------------


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_user_management_api():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}

        created = client.post(
            "/api/users",
            headers=admin,
            json={"username": "erika", "password": "geheim123", "email": "e@example.org"},
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]
        assert created.json()["is_active"] is True

        listing = client.get("/api/users?q=erik", headers=admin).json()
        assert listing["total"] == 1
        assert listing["items"][0]["username"] == "erika"

        detail = client.get(f"/api/users/{user_id}", headers=admin).json()
        assert detail["groups"] == []

        assert client.patch(
            f"/api/users/{user_id}",
            headers=admin,
            json={"display_name": "Erika M."},
        ).json()["display_name"] == "Erika M."

        # Deaktivieren → Login scheitert
        assert client.post(f"/api/users/{user_id}/deactivate", headers=admin).status_code == 204
        assert client.post(
            "/api/auth/login", json={"username": "erika", "password": "geheim123"}
        ).status_code == 401

        assert client.post(f"/api/users/{user_id}/activate", headers=admin).status_code == 204

        # Gruppe zuweisen
        group_id = client.post(
            "/api/groups", headers=admin, json={"name": "team"}
        ).json()["id"]
        assert client.post(
            f"/api/users/{user_id}/groups/{group_id}", headers=admin
        ).status_code == 204
        assert client.get(f"/api/users/{user_id}/groups", headers=admin).json()[0]["name"] == "team"
        assert client.delete(
            f"/api/users/{user_id}/groups/{group_id}", headers=admin
        ).status_code == 204

        # Nicht-Admin darf die Benutzerliste nicht sehen
        erika_token = _login(client, "erika", "geheim123")
        assert client.get(
            "/api/users", headers={"Authorization": f"Bearer {erika_token}"}
        ).status_code == 403

        # Löschen (kein Besitz) ohne Übertragung
        assert client.delete(f"/api/users/{user_id}", headers=admin).status_code == 204
        assert client.get(f"/api/users/{user_id}", headers=admin).status_code == 404


def test_self_service_password_change():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        client.post(
            "/api/users",
            headers=admin,
            json={"username": "frank", "password": "start1234"},
        )
        token = _login(client, "frank", "start1234")
        auth = {"Authorization": f"Bearer {token}"}

        assert client.patch("/api/me", headers=auth, json={"display_name": "Frank"}).status_code == 200
        assert client.post(
            "/api/me/password",
            headers=auth,
            json={"current_password": "start1234", "new_password": "neuespass9"},
        ).status_code == 204

        # Alter Token ist durch die Token-Version ungültig geworden
        assert client.get("/api/me", headers=auth).status_code == 401
        # Neues Passwort funktioniert
        assert _login(client, "frank", "neuespass9")


def _new_user(client, admin, username, password="geheim123", is_admin=False):
    created = client.post(
        "/api/users",
        headers=admin,
        json={"username": username, "password": password, "is_admin": is_admin},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_bulk_user_active():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        first = _new_user(client, admin, "bulk-a")
        second = _new_user(client, admin, "bulk-b")

        response = client.post(
            "/api/users/bulk/active",
            headers=admin,
            json={"ids": [first, second], "active": False},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": 2, "failed": []}
        for name in ("bulk-a", "bulk-b"):
            assert client.post(
                "/api/auth/login", json={"username": name, "password": "geheim123"}
            ).status_code == 401

        again = client.post(
            "/api/users/bulk/active",
            headers=admin,
            json={"ids": [first, second], "active": True},
        )
        assert again.json()["ok"] == 2
        for name in ("bulk-a", "bulk-b"):
            assert client.post(
                "/api/auth/login", json={"username": name, "password": "geheim123"}
            ).status_code == 200


def test_bulk_user_active_protects_last_admin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        me = client.get("/api/users?q=admin", headers=admin).json()["items"][0]["id"]

        response = client.post(
            "/api/users/bulk/active", headers=admin, json={"ids": [me], "active": False}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": 0, "failed": [me]}
        assert client.get(f"/api/users/{me}", headers=admin).json()["is_active"] is True


def test_bulk_user_delete_with_transfer():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        owner_id = _new_user(client, admin, "bulk-owner")
        target_id = _new_user(client, admin, "bulk-target")

        root_id = client.get("/api/root", headers=admin).json()["id"]
        client.post(
            f"/api/entries/{root_id}/acl",
            headers=admin,
            json={"principal_type": "user", "principal_id": owner_id, "perms": ["read", "write"]},
        )
        owner = {"Authorization": f"Bearer {_login(client, 'bulk-owner', 'geheim123')}"}
        folder_id = client.post("/api/folders", headers=owner, json={"name": "owned"}).json()["id"]

        response = client.post(
            "/api/users/bulk/delete",
            headers=admin,
            json={"ids": [owner_id], "transfer_to": target_id},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": 1, "failed": []}
        assert client.get(f"/api/users/{owner_id}", headers=admin).status_code == 404

        target = {"Authorization": f"Bearer {_login(client, 'bulk-target', 'geheim123')}"}
        assert client.get(f"/api/entries/{folder_id}", headers=target).status_code == 200


def test_bulk_user_endpoints_require_admin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {_login(client)}"}
        _new_user(client, admin, "bulk-plain")
        plain = {"Authorization": f"Bearer {_login(client, 'bulk-plain', 'geheim123')}"}
        assert client.post(
            "/api/users/bulk/active", headers=plain, json={"ids": [], "active": True}
        ).status_code in (401, 403, 422)
        assert client.post(
            "/api/users/bulk/active",
            headers=plain,
            json={"ids": ["00000000-0000-0000-0000-000000000000"], "active": True},
        ).status_code == 403
