"""Tests für das Rollenmanagement (Kern und API)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import authz, namespace, roles
from ifs.errors import Conflict
from ifs.models import PrincipalType, RoleScope

from .conftest import make_user
from .helpers import login


def test_builtin_roles_created(db):
    roles.ensure_builtin_roles(db)
    names = {role.name for role in roles.list_roles(db)}
    assert {"admin", "viewer", "editor", "manager", "auditor", "user"} <= names
    assert roles.find_by_name(db, "manager").permissions == authz.ALL


def test_builtin_role_protection(db):
    roles.ensure_builtin_roles(db)
    viewer = roles.find_by_name(db, "viewer")
    with pytest.raises(Conflict):
        roles.update_role(db, viewer, permissions=authz.ALL)
    with pytest.raises(Conflict):
        roles.delete_role(db, viewer)


def test_resource_role_grants_read_only(db):
    roles.ensure_builtin_roles(db)
    owner = make_user(db, "owner")
    reader = make_user(db, "reader")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)
    child = namespace.create_folder(db, folder, "2026", owner.id)

    viewer = roles.find_by_name(db, "viewer")
    roles.assign_role(
        db,
        role=viewer,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        entry_id=folder.id,
    )

    assert authz.can(db, reader, "read", child)
    assert not authz.can(db, reader, "write", child)
    assert not authz.can(db, reader, "delete", child)


def test_custom_system_role_admin(db):
    user = make_user(db, "ops")
    role = roles.create_role(
        db, name="ops-admin", scope=RoleScope.system, permissions=authz.ADMIN
    )
    roles.assign_role(db, role=role, principal_type=PrincipalType.user, principal_id=user.id)

    assert authz.is_system_admin(db, user)
    root = namespace.ensure_root(db, make_user(db, "owner2").id)
    # Systemrollen gewähren keinen Dateizugriff auf fremde Wurzelbäume.
    assert authz.effective_perms(db, user, root) == 0


def test_group_role_assignment(db):
    from ifs.models import Group

    roles.ensure_builtin_roles(db)
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    group = Group(name="team")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "share", owner.id)
    editor = roles.find_by_name(db, "editor")
    roles.assign_role(
        db,
        role=editor,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        entry_id=folder.id,
    )

    assert authz.can(db, member, "delete", folder)


# --- API ------------------------------------------------------------------


def test_roles_api_flow():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}

        listing = client.get("/api/roles", headers=admin).json()
        names = {role["name"] for role in listing}
        assert "manager" in names and "admin" in names
        manager = next(role for role in listing if role["name"] == "manager")
        assert "admin" in manager["permission_names"]

        # Eingebaute Rolle löschen -> 409
        assert client.delete(f"/api/roles/{manager['id']}", headers=admin).status_code == 409

        # Eigene Rolle anlegen und ändern
        created = client.post(
            "/api/roles",
            headers=admin,
            json={"name": "reviewer", "scope": "resource", "permissions": ["read"]},
        )
        assert created.status_code == 201, created.text
        role_id = created.json()["id"]
        assert client.patch(
            f"/api/roles/{role_id}", headers=admin, json={"permissions": ["read", "write"]}
        ).json()["permissions"] == authz.READ | authz.WRITE

        # Benutzer + Ordner
        user_id = client.post(
            "/api/users",
            headers=admin,
            json={"username": "gina", "password": "geheim123"},
        ).json()["id"]
        folder_id = client.post(
            "/api/folders", headers=admin, json={"name": "shared"}
        ).json()["id"]

        # Rolle am Ordner zuweisen
        assigned = client.post(
            f"/api/entries/{folder_id}/roles",
            headers=admin,
            json={"role_id": role_id, "principal_type": "user", "principal_id": user_id},
        )
        assert assigned.status_code == 201, assigned.text

        gina = {"Authorization": f"Bearer {login(client, 'gina', 'geheim123')}"}
        assert client.get(f"/api/entries/{folder_id}", headers=gina).status_code == 200
        # Schreiben (Unterordner) ist mit "reviewer" (read,write) erlaubt:
        assert client.post(
            "/api/folders", headers=gina, json={"parent_id": folder_id, "name": "sub"}
        ).status_code == 201

        # Zuweisung entfernen -> kein Zugriff mehr
        assert client.delete(
            f"/api/entries/{folder_id}/roles/{assigned.json()['id']}", headers=admin
        ).status_code == 204
        assert client.get(f"/api/entries/{folder_id}", headers=gina).status_code == 403


def test_system_role_grants_admin_endpoints():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}
        user_id = client.post(
            "/api/users",
            headers=admin,
            json={"username": "vera", "password": "geheim123"},
        ).json()["id"]
        system_roles = client.get("/api/roles?scope=system", headers=admin).json()
        admin_role = next(role for role in system_roles if role["name"] == "admin")

        vera = {"Authorization": f"Bearer {login(client, 'vera', 'geheim123')}"}
        assert client.get("/api/users", headers=vera).status_code == 403

        assigned = client.post(
            f"/api/users/{user_id}/roles/{admin_role['id']}", headers=admin
        )
        assert assigned.status_code == 201
        # Jetzt gilt die systemweite admin-Rolle auch für Admin-Endpunkte
        assert client.get("/api/users", headers=vera).status_code == 200

        assert client.delete(
            f"/api/users/{user_id}/roles/{assigned.json()['id']}", headers=admin
        ).status_code == 204
        assert client.get("/api/users", headers=vera).status_code == 403
