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
    assert authz.API == 256
    assert authz.ALL_RESOURCE == 63
    assert authz.ALL_SYSTEM == 464  # admin|audit|monitor|api
    assert authz.ALL == 511


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
    authz.validate_system_grant_perms(authz.AUDIT | authz.MONITOR | authz.API)
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


def test_systemrechte_und_eintragsrechte_sind_getrennt(db):
    from ifs.core import permissions
    from ifs.models import Group, SystemPermission

    owner = make_user(db, "owner-trennung")
    auditor = make_user(db, "auditor-trennung")
    member = make_user(db, "member-trennung")
    group = Group(name="audit-trennung")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)

    # Eintrags-ACL mit allen Eintragsrechten -> keine Systemrechte.
    permissions.upsert_acl(
        db,
        entry_id=folder.id,
        principal_type=PrincipalType.user,
        principal_id=auditor.id,
        perms=authz.ALL_RESOURCE,
    )
    assert authz.effective_perms(db, auditor, folder) == authz.ALL_RESOURCE
    assert authz.system_perms(db, auditor) == 0

    # System-Grant (auch über Gruppe) -> keine Eintragsrechte.
    db.add(
        SystemPermission(
            principal_type=PrincipalType.user,
            principal_id=auditor.id,
            permissions=authz.AUDIT,
        )
    )
    db.add(
        SystemPermission(
            principal_type=PrincipalType.group,
            principal_id=group.id,
            permissions=authz.MONITOR,
        )
    )
    db.flush()

    assert authz.system_perms(db, auditor) == authz.AUDIT
    assert authz.has_system_perm(db, auditor, authz.AUDIT)
    assert not authz.has_system_perm(db, auditor, authz.MONITOR)
    assert authz.system_perms(db, member) == authz.MONITOR
    assert authz.effective_perms(db, member, folder) == 0
    assert not authz.can(db, member, "read", folder)

    # Eigentümer: nur Eintragsrechte, keine Systemrechte.
    assert authz.system_perms(db, owner) == 0


def test_systemadmin_hat_admin_aber_kein_dateirecht(db):
    admin = make_user(db, "sysadmin-bits", is_admin=True)
    owner = make_user(db, "owner-bits")
    root = namespace.ensure_root(db, owner.id)

    assert authz.system_perms(db, admin) & authz.ADMIN
    assert not (authz.system_perms(db, admin) & (authz.AUDIT | authz.MONITOR))
    assert authz.effective_perms(db, admin, root) == 0


def test_require_system_perm_und_cache_invalidierung(db):
    from ifs.core import permissions
    from ifs.errors import PermissionDenied
    from ifs.models import PrincipalType

    user = make_user(db, "grant-cache")
    with pytest.raises(PermissionDenied):
        authz.require_system_perm(db, user, authz.AUDIT)

    permissions.merge_system_permissions(
        db,
        principal_type=PrincipalType.user,
        principal_id=user.id,
        perms=authz.AUDIT,
    )
    # Flush invalidiert den Memo-Cache; das neue Recht ist sofort sichtbar.
    authz.require_system_perm(db, user, authz.AUDIT)
    assert not authz.has_system_perm(db, user, authz.MONITOR)

