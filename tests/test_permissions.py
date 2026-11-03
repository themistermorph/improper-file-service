"""Berechtigungsmodell: ACL-Upsert, System-Grants und Schema-Idempotenz."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from ifs import main
from ifs.core import authz, namespace, permissions, roles
from ifs.db import get_engine, session_scope
from ifs.models import Group, PrincipalType, RoleScope

from .conftest import make_user
from .helpers import login

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "sql"
    / "0011_permissions_schema.sql"
)


def test_acl_unique_index_und_systemtabelle_im_schema():
    acl_indexes = {
        (index["name"], tuple(index["column_names"]), index["unique"])
        for index in inspect(get_engine()).get_indexes("acl")
    }
    assert ("uq_acl_entry_principal", ("entry_id", "principal_type", "principal_id"), 1) in acl_indexes

    sys_indexes = {
        (index["name"], tuple(index["column_names"]), index["unique"])
        for index in inspect(get_engine()).get_indexes("system_permissions")
    }
    assert (
        "uq_system_permissions_principal",
        ("principal_type", "principal_id"),
        1,
    ) in sys_indexes


def test_migration_ist_additiv_und_idempotent():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS system_permissions" in text
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_permissions_principal" in text
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_acl_entry_principal" in text
    # Duplikate werden vor dem Index per Bit-OR zusammengeführt.
    assert "bit_or(perms)" in text
    # Keine Rollen werden in der Schema-Migration gelöscht (erst 0013).
    assert not re.search(r"DELETE\s+FROM\s+roles", text, re.IGNORECASE)


def test_acl_upsert_vereinigt_und_ersetzt(db):
    owner = make_user(db, "owner-upsert")
    reader = make_user(db, "reader-upsert")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)

    first = permissions.upsert_acl(
        db,
        entry_id=folder.id,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        perms=authz.READ,
    )
    merged = permissions.upsert_acl(
        db,
        entry_id=folder.id,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        perms=authz.WRITE,
    )
    assert merged.id == first.id
    assert merged.perms == authz.READ | authz.WRITE
    assert len(permissions.list_entry_acls(db, folder.id)) == 1

    replaced = permissions.upsert_acl(
        db,
        entry_id=folder.id,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        perms=authz.DELETE,
        merge=False,
    )
    assert replaced.perms == authz.DELETE


def test_acl_lehnt_systemrechte_ab(db):
    owner = make_user(db, "owner-aclbad")
    root = namespace.ensure_root(db, owner.id)
    with pytest.raises(ValueError):
        permissions.upsert_acl(
            db,
            entry_id=root.id,
            principal_type=PrincipalType.user,
            principal_id=owner.id,
            perms=authz.AUDIT,
        )


def test_system_grant_upsert_und_entfernen(db):
    group = Group(name="audit-team")
    db.add(group)
    db.flush()

    first = permissions.merge_system_permissions(
        db,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        perms=authz.AUDIT,
    )
    assert first is not None and first.permissions == authz.AUDIT
    second = permissions.merge_system_permissions(
        db,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        perms=authz.MONITOR,
    )
    assert second is not None and second.permissions == authz.AUDIT | authz.MONITOR

    exact = permissions.set_system_permissions(
        db,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        perms=authz.MONITOR,
    )
    assert exact is not None and exact.permissions == authz.MONITOR

    assert (
        permissions.set_system_permissions(
            db,
            principal_type=PrincipalType.group,
            principal_id=group.id,
            perms=0,
        )
        is None
    )
    assert permissions.get_system_permission(db, PrincipalType.group, group.id) is None


def test_system_grant_lehnt_admin_und_eintragsrechte_ab(db):
    user = make_user(db, "grant-bad")
    for bad in (authz.ADMIN, authz.READ, authz.ADMIN | authz.AUDIT):
        with pytest.raises(ValueError):
            permissions.set_system_permissions(
                db,
                principal_type=PrincipalType.user,
                principal_id=user.id,
                perms=bad,
            )


# --- Backfill aus dem Rollenmodell -----------------------------------------


def test_backfill_migriert_ressourcen_und_systemrollen(db):
    from ifs.core import roles
    from ifs.models import Role, RoleAssignment, RoleScope

    roles.ensure_builtin_roles(db)
    owner = make_user(db, "backfill-owner")
    reader = make_user(db, "backfill-reader")
    sharer = make_user(db, "backfill-sharer")
    auditor = make_user(db, "backfill-auditor")
    operator = make_user(db, "backfill-operator")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "shared", owner.id)

    viewer_role = roles.create_role(
        db, name="viewer", scope=RoleScope.resource, permissions=authz.READ
    )
    sharer_role = roles.create_role(
        db,
        name="sharer",
        scope=RoleScope.resource,
        permissions=authz.READ | authz.SHARE,
    )
    auditor_role = roles.create_role(
        db, name="auditor", scope=RoleScope.system, permissions=0
    )
    roles.assign_role(
        db,
        role=viewer_role,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        entry_id=folder.id,
    )
    roles.assign_role(
        db,
        role=sharer_role,
        principal_type=PrincipalType.user,
        principal_id=sharer.id,
        entry_id=folder.id,
    )
    roles.assign_role(
        db,
        role=auditor_role,
        principal_type=PrincipalType.user,
        principal_id=auditor.id,
    )
    admin_role = roles.create_role(
        db, name="ops-admin", scope=RoleScope.system, permissions=authz.ADMIN
    )
    roles.assign_role(
        db,
        role=admin_role,
        principal_type=PrincipalType.user,
        principal_id=operator.id,
    )

    stats = permissions.backfill_roles(db)
    assert stats["acl_grants"] >= 2
    assert stats["system_grants"] == 1
    assert stats["admin_assignments"] == 1

    acls = {
        acl.principal_id: acl.perms
        for acl in permissions.list_entry_acls(db, folder.id)
    }
    assert acls[reader.id] == authz.READ
    # Alter sharer hatte read+share; publish kommt additiv hinzu.
    assert acls[sharer.id] == authz.READ | authz.SHARE | authz.PUBLISH

    grant = permissions.get_system_permission(db, PrincipalType.user, auditor.id)
    assert grant is not None and grant.permissions == authz.AUDIT
    # Eintragsrechte entstehen aus System-Grants nicht.
    assert authz.effective_perms(db, auditor, folder) == 0

    admin_assignments = db.execute(
        select(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            Role.name == "admin",
            RoleAssignment.principal_type == PrincipalType.user,
            RoleAssignment.principal_id == operator.id,
        )
    ).scalars().all()
    assert len(admin_assignments) == 1
    assert authz.is_system_admin(db, operator)

    # idempotent: ein zweiter Lauf ändert nichts mehr.
    assert permissions.backfill_roles(db) == {
        "acl_grants": 0,
        "system_grants": 0,
        "admin_assignments": 0,
        "publish_from_share": 0,
    }


def test_backfill_publish_fuer_bestehende_share_acls_und_is_admin(db):
    from ifs.models import Role, RoleAssignment

    user = make_user(db, "bootstrap-admin", is_admin=True)
    owner = make_user(db, "share-owner")
    reader = make_user(db, "share-reader")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "alt", owner.id)
    permissions.upsert_acl(
        db,
        entry_id=folder.id,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        perms=authz.READ | authz.SHARE,
    )

    stats = permissions.backfill_roles(db)
    # Bestehende ACL erhält publish, ohne dass share verloren geht.
    acl = permissions.list_entry_acls(db, folder.id)[0]
    assert acl.perms == authz.READ | authz.SHARE | authz.PUBLISH
    assert stats["publish_from_share"] == 1

    assignment = db.execute(
        select(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            Role.name == "admin",
            RoleAssignment.principal_type == PrincipalType.user,
            RoleAssignment.principal_id == user.id,
            RoleAssignment.entry_id.is_(None),
        )
    ).scalars().first()
    assert assignment is not None
    # Flag und Adminrolle ergeben denselben effektiven Adminstatus.
    assert authz.is_system_admin(db, user)


def test_cleanup_laesst_nur_adminrolle_uebrig(db):
    from ifs.core import roles
    from ifs.models import Role, RoleAssignment

    roles.ensure_builtin_roles(db)
    user = make_user(db, "cleanup-admin", is_admin=True)
    roles.create_role(
        db, name="legacy-viewer", scope=RoleScope.resource, permissions=authz.READ
    )
    permissions.backfill_roles(db)

    result = permissions.cleanup_roles(db)
    assert result["roles_removed"] >= 1

    names = set(db.execute(select(Role.name)).scalars().all())
    assert names == {"admin"}
    assignments = db.execute(select(RoleAssignment)).scalars().all()
    assert all(a.entry_id is None for a in assignments)
    assert authz.is_system_admin(db, user)

    # Idempotent.
    assert permissions.cleanup_roles(db)["roles_removed"] == 0


# --- API: /api/me und EntryOut.permissions ---------------------------------


def test_me_liefert_effektive_systemrechte():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        me = client.get("/api/me", headers=admin).json()
        assert me["is_admin"] is True
        assert me["permissions"] == []

        user_id = client.post(
            "/api/users", headers=admin, json={"username": "me-audit", "password": "geheim123"}
        ).json()["id"]
        with session_scope() as db:
            permissions.merge_system_permissions(
                db,
                principal_type=PrincipalType.user,
                principal_id=UUID(user_id),
                perms=authz.AUDIT | authz.MONITOR,
            )
        headers = {"Authorization": f"Bearer {login(client, 'me-audit', 'geheim123')}"}
        me = client.get("/api/me", headers=headers).json()
        assert me["is_admin"] is False
        assert me["permissions"] == ["audit", "monitor"]

        # Normale Nutzer haben weder Admin noch Systemrechte.
        client.post(
            "/api/users", headers=admin, json={"username": "me-normal", "password": "geheim123"}
        )
        normal = {"Authorization": f"Bearer {login(client, 'me-normal', 'geheim123')}"}
        me = client.get("/api/me", headers=normal).json()
        assert me["is_admin"] is False
        assert me["permissions"] == []


def test_adminrolle_ohne_flag_ist_in_me_effektiv_admin():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        user_id = client.post(
            "/api/users", headers=admin, json={"username": "role-admin", "password": "geheim123"}
        ).json()["id"]
        with session_scope() as db:
            roles.ensure_builtin_roles(db)
            admin_role = roles.find_by_name(db, "admin")
            roles.assign_role(
                db,
                role=admin_role,
                principal_type=PrincipalType.user,
                principal_id=UUID(user_id),
            )
        headers = {"Authorization": f"Bearer {login(client, 'role-admin', 'geheim123')}"}
        me = client.get("/api/me", headers=headers).json()
        assert me["is_admin"] is True
        # Gleiche Bedingung wie die API: Admin-Endpunkte funktionieren.
        assert client.get("/api/users", headers=headers).status_code == 200


def test_entryout_liefert_effektive_eintragsrechte():
    with TestClient(main.app) as client:
        admin = {"Authorization": f"Bearer {login(client)}"}
        folder = client.post("/api/folders", headers=admin, json={"name": "caps"}).json()
        assert set(folder["permissions"]) == {
            "read",
            "write",
            "delete",
            "share",
            "publish",
            "admin",
        }

        sam_id = client.post(
            "/api/users", headers=admin, json={"username": "caps-sam", "password": "geheim123"}
        ).json()["id"]
        granted = client.post(
            f"/api/entries/{folder['id']}/acl",
            headers=admin,
            json={
                "principal_type": "user",
                "principal_id": sam_id,
                "perms": ["read", "share", "publish"],
            },
        )
        assert granted.status_code == 201, granted.text

        sam = {"Authorization": f"Bearer {login(client, 'caps-sam', 'geheim123')}"}
        entry = client.get(f"/api/entries/{folder['id']}", headers=sam).json()
        assert set(entry["permissions"]) == {"read", "share", "publish"}
        # Kein Schreib-/Loesch-/Adminrecht nur wegen share/publish.
        assert "write" not in entry["permissions"]
        assert "delete" not in entry["permissions"]
        assert "admin" not in entry["permissions"]


