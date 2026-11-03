"""Berechtigungsmodell: ACL-Upsert, System-Grants und Schema-Idempotenz."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect

from ifs.core import authz, namespace, permissions
from ifs.db import get_engine
from ifs.models import Group, PrincipalType

from .conftest import make_user

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
