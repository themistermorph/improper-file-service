"""Tests für das zentrale Autorisierungsmodul."""

from __future__ import annotations

from ifs.core import authz, namespace
from ifs.models import ACL, PrincipalType

from .conftest import make_user


def test_owner_has_all_permissions(db):
    owner = make_user(db, "owner")
    root = namespace.ensure_root(db, owner.id)
    folder = namespace.create_folder(db, root, "docs", owner.id)

    assert authz.effective_perms(db, owner, folder) == authz.ALL
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
