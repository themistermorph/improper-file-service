"""Rollen nach der Umstellung: nur die Adminrolle bleibt.

Fachliche Rechte laufen über ACLs/system_permissions; die alten
Rollen-Endpunkte liefern 410 mit einem Hinweis auf die neuen Endpunkte.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ifs import main
from ifs.core import authz, namespace, roles
from ifs.errors import Conflict
from ifs.models import ACL, PrincipalType, Role, RoleScope

from .conftest import make_user
from .helpers import login


def test_builtin_roles_created(db):
    roles.ensure_builtin_roles(db)
    assert {role.name for role in roles.list_roles(db)} == {"admin"}
    admin = roles.find_by_name(db, "admin")
    assert admin.permissions == authz.ALL
    assert admin.scope == RoleScope.system
    assert admin.is_builtin is True


def test_builtin_role_protection(db):
    roles.ensure_builtin_roles(db)
    admin = roles.find_by_name(db, "admin")
    with pytest.raises(Conflict):
        roles.update_role(db, admin, permissions=authz.READ)
    with pytest.raises(Conflict):
        roles.delete_role(db, admin)


def test_admin_role_wird_auf_neue_bits_aktualisiert(db):
    db.add(
        Role(
            name="admin",
            scope=RoleScope.system,
            permissions=authz.READ | authz.WRITE,
            description="alt",
            is_builtin=True,
        )
    )
    db.flush()
    roles.ensure_builtin_roles(db)
    assert roles.find_by_name(db, "admin").permissions == authz.ALL


def test_custom_system_role_admin_bleibt_lesbar(db):
    """Übergangs-/Rollback-Pfad: eine ADMIN-Systemrolle wirkt weiterhin."""
    user = make_user(db, "ops")
    role = roles.create_role(
        db, name="ops-admin", scope=RoleScope.system, permissions=authz.ADMIN
    )
    roles.assign_role(db, role=role, principal_type=PrincipalType.user, principal_id=user.id)

    assert authz.is_system_admin(db, user)
    root = namespace.ensure_root(db, make_user(db, "owner2").id)
    # Systemrollen gewähren keinen Dateizugriff auf fremde Wurzelbäume.
    assert authz.effective_perms(db, user, root) == 0


def test_gruppen_acl_statt_ressourcenrolle(db):
    from ifs.models import Group

    owner = make_user(db, "owner")
    member = make_user(db, "member")
    group = Group(name="team")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "share", owner.id)
    db.add(
        ACL(
            entry_id=folder.id,
            principal_type=PrincipalType.group,
            principal_id=group.id,
            perms=authz.READ | authz.WRITE,
            inherited=True,
        )
    )
    db.flush()

    assert authz.can(db, member, "read", folder)
    assert authz.can(db, member, "write", folder)
    assert not authz.can(db, member, "delete", folder)


# --- API ------------------------------------------------------------------


def test_rollen_api_liefert_nur_admin_und_lehnt_mutationen_ab():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}
        listing = client.get("/api/roles", headers=admin).json()
        assert [role["name"] for role in listing] == ["admin"]

        assert client.post(
            "/api/roles",
            headers=admin,
            json={"name": "reviewer", "scope": "resource", "permissions": ["read"]},
        ).status_code == 410
        role_id = listing[0]["id"]
        assert client.patch(
            f"/api/roles/{role_id}", headers=admin, json={"permissions": ["read"]}
        ).status_code == 410
        assert client.delete(f"/api/roles/{role_id}", headers=admin).status_code == 410


def test_entry_rollen_endpunkte_sind_entfernt():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}
        folder = client.post(
            "/api/folders", headers=admin, json={"name": "entry-roles"}
        ).json()["id"]
        assert client.get(f"/api/entries/{folder}/roles", headers=admin).status_code == 410
        assert client.post(
            f"/api/entries/{folder}/roles",
            headers=admin,
            json={
                "role_id": "00000000-0000-0000-0000-000000000001",
                "principal_type": "user",
                "principal_id": "00000000-0000-0000-0000-000000000002",
            },
        ).status_code == 410
        assert client.delete(
            f"/api/entries/{folder}/roles/00000000-0000-0000-0000-000000000001",
            headers=admin,
        ).status_code == 410


def test_admin_flag_gewaehrt_admin_endpunkte():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client, 'admin', 'admin')}"}
        user_id = client.post(
            "/api/users", headers=admin, json={"username": "vera", "password": "geheim123"}
        ).json()["id"]

        vera = {"Authorization": f"Bearer {login(client, 'vera', 'geheim123')}"}
        assert client.get("/api/users", headers=vera).status_code == 403

        assert client.patch(
            f"/api/users/{user_id}", headers=admin, json={"is_admin": True}
        ).status_code == 200
        vera = {"Authorization": f"Bearer {login(client, 'vera', 'geheim123')}"}
        assert client.get("/api/users", headers=vera).status_code == 200

        assert client.patch(
            f"/api/users/{user_id}", headers=admin, json={"is_admin": False}
        ).status_code == 200
        vera = {"Authorization": f"Bearer {login(client, 'vera', 'geheim123')}"}
        assert client.get("/api/users", headers=vera).status_code == 403
