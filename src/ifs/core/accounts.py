"""Kontoverwaltung: Benutzer anlegen/bearbeiten, Passwörter, Deaktivieren, Gruppen.

Protokollunabhängiger Kern – von HTTP-API und (später) CLI nutzbar.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, lazyload

from ..errors import BadRequest, Conflict, NotFound
from ..models import (
    ACL,
    Credential,
    Entry,
    Group,
    PrincipalType,
    PublishedEntry,
    RoleAssignment,
    Share,
    User,
)
from ..security import hash_password, verify_password
from . import namespace

MIN_PASSWORD_LENGTH = 8


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise BadRequest(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein")


def get_user(db: Session, user_id: UUID) -> User:
    # Gruppen erst bei Zugriff laden: Die meisten Aufrufer brauchen sie nicht,
    # das spart pro Aufruf eine zusätzliche Abfrage (Daten bleiben unverändert).
    user = db.get(User, user_id, options=[lazyload(User.groups)])
    if user is None:
        raise NotFound("Benutzer nicht gefunden")
    return user


def find_by_username(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username == username)).scalars().first()


def list_users(
    db: Session,
    *,
    q: str | None = None,
    active: bool | None = None,
    admin: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[User], int]:
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.username).like(like),
                func.lower(func.coalesce(User.email, "")).like(like),
                func.lower(func.coalesce(User.display_name, "")).like(like),
            )
        )
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    if admin is not None:
        stmt = stmt.where(User.is_admin.is_(admin))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    # Die Listendarstellung enthält keine Gruppen: bewusst nicht mitladen.
    rows = (
        db.execute(
            stmt.order_by(User.username).limit(limit).offset(offset)
            .options(lazyload(User.groups))
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
    display_name: str | None = None,
    is_admin: bool = False,
) -> User:
    username = (username or "").strip()
    if not username:
        raise BadRequest("Benutzername darf nicht leer sein")
    if find_by_username(db, username) is not None:
        raise Conflict("Benutzername existiert bereits")
    validate_password(password)
    user = User(
        username=username,
        display_name=display_name,
        email=email,
        password_hash=hash_password(password),
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    namespace.ensure_root(db, user.id)
    return user


def _active_admin_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(User)
            .where(User.is_admin.is_(True), User.is_active.is_(True))
        ).scalar_one()
    )


def _ensure_not_last_active_admin(db: Session, user: User) -> None:
    if user.is_admin and user.is_active and _active_admin_count(db) <= 1:
        raise Conflict("Der letzte aktive Administrator kann nicht entfernt werden")


def update_user(
    db: Session,
    user: User,
    *,
    email: str | None = None,
    display_name: str | None = None,
    is_admin: bool | None = None,
) -> User:
    if email is not None:
        user.email = email or None
    if display_name is not None:
        user.display_name = display_name or None
    if is_admin is not None and is_admin != user.is_admin:
        if not is_admin:
            _ensure_not_last_active_admin(db, user)
        user.is_admin = is_admin
        user.token_version += 1
    db.flush()
    return user


def set_password(db: Session, user: User, password: str) -> None:
    validate_password(password)
    user.password_hash = hash_password(password)
    user.token_version += 1
    db.flush()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise BadRequest("Aktuelles Passwort ist falsch")
    validate_password(new_password)
    if current_password == new_password:
        raise BadRequest("Neues Passwort muss sich vom alten unterscheiden")
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    db.flush()


def principal_exists(db: Session, principal_type: PrincipalType, principal_id: UUID) -> bool:
    # Reine Existenzprüfung: Mitglieder bzw. Gruppen nicht mitladen.
    if principal_type == PrincipalType.group:
        return db.get(Group, principal_id, options=[lazyload(Group.members)]) is not None
    return db.get(User, principal_id, options=[lazyload(User.groups)]) is not None


def _revoke_created_shares(db: Session, user: User) -> None:
    # Sammel-DELETE statt Zeile für Zeile (eine Abfrage statt SELECT + N DELETE).
    db.execute(delete(Share).where(Share.created_by == user.id))


def _revoke_created_publications(db: Session, user: User) -> None:
    # Deaktivierte/gelöschte Konten dürfen keine öffentlichen Dateien mehr zeigen.
    db.execute(delete(PublishedEntry).where(PublishedEntry.published_by == user.id))


def deactivate_user(db: Session, user: User) -> None:
    _ensure_not_last_active_admin(db, user)
    user.is_active = False
    user.token_version += 1
    # Freigaben und Veröffentlichungen eines deaktivierten Kontos dürfen nicht
    # weiterleben.
    _revoke_created_shares(db, user)
    _revoke_created_publications(db, user)
    db.flush()


def activate_user(db: Session, user: User) -> None:
    user.is_active = True
    db.flush()


def _clear_group_memberships(db: Session, user: User) -> None:
    user.groups.clear()
    db.flush()


def _delete_credentials(db: Session, user: User) -> None:
    db.execute(delete(Credential).where(Credential.user_id == user.id))


def _delete_user_acls(db: Session, user: User) -> None:
    db.execute(
        delete(ACL).where(
            ACL.principal_type == PrincipalType.user, ACL.principal_id == user.id
        )
    )


def _delete_user_role_assignments(db: Session, user: User) -> None:
    """Entfernt verwaiste Rollenzuweisungen des Benutzers (principal_id ohne FK)."""
    db.execute(
        delete(RoleAssignment).where(
            RoleAssignment.principal_type == PrincipalType.user,
            RoleAssignment.principal_id == user.id,
        )
    )


def delete_user(db: Session, user: User, transfer_to: User | None = None) -> None:
    _ensure_not_last_active_admin(db, user)

    root = namespace.get_root(db, user.id)
    children = namespace.list_children(db, root) if root is not None else []
    # Einträge des Benutzers (außer der Wurzel selbst) – z. B. auch in fremden,
    # freigegebenen Ordnern oder im eigenen Baum.
    owned = db.execute(
        select(Entry).where(
            Entry.owner_id == user.id,
            *([Entry.id != root.id] if root is not None else []),
        )
    ).scalars().all()

    if (children or owned) and transfer_to is None:
        raise Conflict(
            "Benutzer besitzt noch Einträge – Zielbenutzer über 'transfer_to' angeben"
        )
    if transfer_to is not None:
        if transfer_to.id == user.id:
            raise BadRequest("Besitz kann nicht an denselben Benutzer übertragen werden")
        if not transfer_to.is_active:
            raise BadRequest("Zielbenutzer ist nicht aktiv")

        # Besitz aller Einträge auf den Zielbenutzer umschreiben.
        for entry in owned:
            entry.owner_id = transfer_to.id
        db.flush()

        # Inhalt der Wurzel unter '<username>' in die Zielwurzel mergen.
        if root is not None and children:
            target_root = namespace.ensure_root(db, transfer_to.id)
            folder_name = namespace.unique_child_name(db, target_root, user.username)
            folder = namespace.create_folder(db, target_root, folder_name, transfer_to.id)
            for child in children:
                namespace.move(db, child, folder)
        db.flush()

    _delete_user_acls(db, user)
    _delete_user_role_assignments(db, user)
    _delete_credentials(db, user)
    _clear_group_memberships(db, user)
    _revoke_created_shares(db, user)
    _revoke_created_publications(db, user)
    if root is not None:
        # Wurzel (samt evtl. noch enthaltenem Papierkorb) entfernen.
        db.delete(root)
    db.delete(user)
    db.flush()


def add_to_group(db: Session, user: User, group: Group) -> None:
    if group not in user.groups:
        user.groups.append(group)
        db.flush()


def remove_from_group(db: Session, user: User, group: Group) -> None:
    if group in user.groups:
        user.groups.remove(group)
        db.flush()
