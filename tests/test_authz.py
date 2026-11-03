"""Tests für das zentrale Autorisierungsmodul."""

from __future__ import annotations

import pytest

from ifs.core import authz, namespace
from ifs.models import ACL, PrincipalType

from .conftest import make_user


def test_berechtigungsbits_sind_stabil_und_getrennt():
    # Bestehende Werte bleiben unverändert (Kompatibilität mit ACL/DB).
    assert (authz.READ, authz.WRITE, authz.DELETE, authz.SHARE, authz.ADMIN) == (
        1,
        2,
        4,
        8,
        16,
    )
    assert authz.PUBLISH == 32
    assert authz.AUDIT == 64
    assert authz.MONITOR == 128
    assert authz.ALL_RESOURCE == 63
    assert authz.ALL_SYSTEM == 208
    assert authz.ALL == 255


def test_rechte_namen_sind_namensraumgetrennt():
    assert authz.perms_from_names(["read", "publish"]) == authz.READ | authz.PUBLISH
    assert authz.perms_from_names(["audit", "monitor"], namespace="system") == (
        authz.AUDIT | authz.MONITOR
    )
    # Systembit in einer ACL und Eintragsbit in einem System-Grant sind illegal.
    with pytest.raises(ValueError):
        authz.perms_from_names(["audit"], namespace="entry")
    with pytest.raises(ValueError):
        authz.perms_from_names(["read"], namespace="system")
    with pytest.raises(ValueError):
        authz.perms_from_names(["unbekannt"])

    assert authz.names_from_perms(authz.AUDIT | authz.READ) == ["read", "audit"]
    assert authz.names_from_perms(authz.READ | authz.AUDIT, namespace="entry") == ["read"]
    assert authz.names_from_perms(authz.READ | authz.AUDIT, namespace="system") == ["audit"]


def test_acl_und_system_grant_validierung():
    authz.validate_entry_perms(authz.ALL_RESOURCE)
    authz.validate_system_grant_perms(authz.AUDIT | authz.MONITOR)
    with pytest.raises(ValueError):
        authz.validate_entry_perms(authz.AUDIT)
    with pytest.raises(ValueError):
        authz.validate_system_grant_perms(authz.ADMIN)
    with pytest.raises(ValueError):
        authz.validate_system_grant_perms(authz.READ)



def test_owner_has_all_permissions(db):
    owner = make_user(db, "owner")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)

    assert authz.effective_perms(db, owner, folder) == authz.ALL_RESOURCE
    assert authz.can(db, owner, "read", folder)
    assert authz.can(db, owner, "write", folder)


def test_other_user_has_no_permissions(db):
    owner = make_user(db, "owner")
    other = make_user(db, "other")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)

    assert authz.effective_perms(db, other, folder) == 0
    assert not authz.can(db, other, "read", folder)


def test_group_grant_is_inherited(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    from ifs.models import Group

    group = Group(name="team")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)
    child = namespace.create_folder(db, folder, "2026", owner.id)
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

    assert authz.can(db, member, "read", child)
    assert authz.can(db, member, "write", child)
    assert not authz.can(db, member, "delete", child)


def test_admin_does_not_bypass_acl(db):
    """Systemadmins haben keinen Dateizugriff auf fremde Wurzelbäume."""
    admin = make_user(db, "root", is_admin=True)
    owner = make_user(db, "owner")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "private", owner.id)

    assert not authz.can(db, admin, "delete", folder)
    assert not authz.can(db, admin, "read", folder)
    # Systemadmin bleibt Systemadmin (Benutzer/Rollen/Audit).
    assert authz.is_system_admin(db, admin)
