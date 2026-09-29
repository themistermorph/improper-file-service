"""Benutzerverwaltung (Administrator) und Selbstbedienung (eigenes Konto)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..core import accounts, audit
from ..errors import NotFound
from ..models import Group, User
from .deps import client_ip, get_current_user, get_db, require_admin
from .schemas import (
    BulkResult,
    BulkUserActive,
    BulkUserDelete,
    GroupOut,
    PasswordChange,
    PasswordReset,
    ProfileUpdate,
    UserCreate,
    UserDetailOut,
    UserListOut,
    UserOut,
    UserUpdate,
)

router = APIRouter(tags=["users"])


def _group_or_404(db: Session, group_id: UUID) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise NotFound("Gruppe nicht gefunden")
    return group


# --- Verwaltung (nur Admin) ----------------------------------------------


@router.get("/users", response_model=UserListOut)
def list_users(
    q: str | None = None,
    active: bool | None = None,
    admin: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserListOut:
    bounded_limit = min(max(limit, 1), 200)
    users, total = accounts.list_users(
        db,
        q=q,
        active=active,
        admin=admin,
        limit=bounded_limit,
        offset=max(offset, 0),
    )
    return UserListOut(items=list(users), total=total, limit=bounded_limit, offset=max(offset, 0))


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    user = accounts.create_user(
        db,
        username=payload.username,
        password=payload.password,
        email=payload.email,
        display_name=payload.display_name,
        is_admin=payload.is_admin,
    )
    audit.record(
        db, "user.create", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"username": user.username, "admin": user.is_admin},
    )
    return user


@router.post("/users/bulk/active", response_model=BulkResult)
def bulk_user_active(
    payload: BulkUserActive,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> BulkResult:
    """Aktiviert oder deaktiviert mehrere Benutzer (letzter Admin bleibt geschützt)."""
    ok = 0
    failed: list[UUID] = []
    for user_id in payload.ids:
        try:
            with db.begin_nested():
                user = accounts.get_user(db, user_id)
                if payload.active:
                    accounts.activate_user(db, user)
                else:
                    accounts.deactivate_user(db, user)
            ok += 1
        except Exception:
            failed.append(user_id)
    audit.record(
        db, "user.bulk_active", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"active": payload.active, "ok": ok, "failed": len(failed)},
    )
    return BulkResult(ok=ok, failed=failed)


@router.post("/users/bulk/delete", response_model=BulkResult)
def bulk_user_delete(
    payload: BulkUserDelete,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> BulkResult:
    """Löscht mehrere Benutzer; vorhandener Besitz wird optional übertragen."""
    target = accounts.get_user(db, payload.transfer_to) if payload.transfer_to else None
    ok = 0
    failed: list[UUID] = []
    for user_id in payload.ids:
        try:
            with db.begin_nested():
                user = accounts.get_user(db, user_id)
                accounts.delete_user(db, user, transfer_to=target)
            ok += 1
        except Exception:
            failed.append(user_id)
    audit.record(
        db, "user.bulk_delete", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={
            "ok": ok,
            "failed": len(failed),
            "transfer_to": str(payload.transfer_to) if payload.transfer_to else None,
        },
    )
    return BulkResult(ok=ok, failed=failed)


@router.get("/users/{user_id}", response_model=UserDetailOut)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    return accounts.get_user(db, user_id)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    user = accounts.get_user(db, user_id)
    accounts.update_user(
        db,
        user,
        email=payload.email,
        display_name=payload.display_name,
        is_admin=payload.is_admin,
    )
    audit.record(
        db, "user.update", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username},
    )
    return user


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: UUID,
    payload: PasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    accounts.set_password(db, user, payload.password)
    audit.record(
        db, "user.password_reset", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
def activate_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    accounts.activate_user(db, user)
    audit.record(
        db, "user.activate", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    accounts.deactivate_user(db, user)
    audit.record(
        db, "user.deactivate", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: UUID,
    request: Request,
    transfer_to: UUID | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    target = accounts.get_user(db, transfer_to) if transfer_to else None
    username = user.username
    accounts.delete_user(db, user, transfer_to=target)
    audit.record(
        db, "user.delete", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": username, "transfer_to": str(transfer_to) if transfer_to else None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/{user_id}/groups", response_model=list[GroupOut])
def list_user_groups(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[Group]:
    user = accounts.get_user(db, user_id)
    return list(user.groups)


@router.post("/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def add_user_group(
    user_id: UUID,
    group_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    group = _group_or_404(db, group_id)
    accounts.add_to_group(db, user, group)
    audit.record(
        db, "user.group_add", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username, "group": group.name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/users/{user_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user_group(
    user_id: UUID,
    group_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    user = accounts.get_user(db, user_id)
    group = _group_or_404(db, group_id)
    accounts.remove_from_group(db, user, group)
    audit.record(
        db, "user.group_remove", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user": user.username, "group": group.name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Selbstbedienung -------------------------------------------------------


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    accounts.update_user(db, user, email=payload.email, display_name=payload.display_name)
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    accounts.change_password(db, user, payload.current_password, payload.new_password)
    audit.record(
        db, "user.password_change", actor_id=user.id, protocol="http", ip=client_ip(request)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/quota")
def my_quota(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    from ..core import quota

    return {
        "limit_bytes": quota.limit_bytes(),
        "usage_bytes": quota.usage_bytes(db, user.id),
    }
