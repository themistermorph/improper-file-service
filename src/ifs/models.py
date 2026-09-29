"""SQLAlchemy-Modelle des IFS-Kerns.

Leitlinie (siehe ARCHITEKTUR.md, P2/P3):
- Der Namespace (Ordner/Dateien/Rechte) liegt in PostgreSQL.
- S3 enthält ausschließlich unveränderliche, content-addressed Blobs.
- `entries.current_version_id` ist eine *weiche* Referenz ohne FK, um einen
  zirkulären Fremdschlüssel (entries <-> versions) zu vermeiden.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils import utcnow, uuid7


def _enum(enum_cls: type[enum.Enum], length: int = 32) -> SAEnum:
    return SAEnum(enum_cls, native_enum=False, validate_strings=True, length=length)


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid7)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class EntryType(str, enum.Enum):
    folder = "folder"
    file = "file"


class PrincipalType(str, enum.Enum):
    user = "user"
    group = "group"


class RoleScope(str, enum.Enum):
    system = "system"
    resource = "resource"


class BlobStatus(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    deleted = "deleted"


class UploadStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    aborted = "aborted"


class CredentialType(str, enum.Enum):
    token = "token"
    ftp = "ftp"


class Permission(str, enum.Enum):
    read = "read"
    write = "write"
    delete = "delete"
    share = "share"
    admin = "admin"


group_members = Table(
    "group_members",
    Base.metadata,
    Column("group_id", Uuid(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    groups: Mapped[list[Group]] = relationship(
        secondary=group_members, back_populates="members", lazy="selectin"
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    members: Mapped[list[User]] = relationship(
        secondary=group_members, back_populates="groups", lazy="selectin"
    )


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[CredentialType] = mapped_column(_enum(CredentialType))
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[UUID] = _uuid_pk()
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    type: Mapped[EntryType] = mapped_column(_enum(EntryType), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # weiche Referenz auf die aktuelle Version (kein FK, siehe Modul-Docstring)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trashed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    original_parent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    parent: Mapped[Entry | None] = relationship(
        "Entry", remote_side="Entry.id", back_populates="children"
    )
    children: Mapped[list[Entry]] = relationship(
        "Entry", back_populates="parent", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_entries_parent_name", "parent_id", "name"),
        # Eindeutiger Name je Parent (case-insensitive, nur aktive Einträge).
        Index(
            "uq_entries_parent_lower_name",
            "parent_id",
            func.lower(name),
            unique=True,
            postgresql_where=text("trashed_at IS NULL"),
            sqlite_where=text("trashed_at IS NULL"),
        ),
        # Genau eine aktive Wurzel je Besitzer (per-user root).
        Index(
            "uq_entries_root_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("parent_id IS NULL AND trashed_at IS NULL"),
            sqlite_where=text("parent_id IS NULL AND trashed_at IS NULL"),
        ),
    )


class Blob(Base):
    __tablename__ = "blobs"

    id: Mapped[UUID] = _uuid_pk()
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[BlobStatus] = mapped_column(_enum(BlobStatus), default=BlobStatus.ready)
    created_at: Mapped[datetime] = _created_at()


class Version(Base):
    __tablename__ = "versions"

    id: Mapped[UUID] = _uuid_pk()
    entry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), index=True
    )
    blob_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("blobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class ACL(Base):
    __tablename__ = "acl"

    id: Mapped[UUID] = _uuid_pk()
    entry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), index=True
    )
    principal_type: Mapped[PrincipalType] = mapped_column(_enum(PrincipalType), nullable=False)
    principal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    perms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inherited: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permissions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scope: Mapped[RoleScope] = mapped_column(
        _enum(RoleScope), default=RoleScope.resource, nullable=False
    )
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class RoleAssignment(Base):
    __tablename__ = "role_assignments"

    id: Mapped[UUID] = _uuid_pk()
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    principal_type: Mapped[PrincipalType] = mapped_column(_enum(PrincipalType), nullable=False)
    principal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    entry_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = _created_at()

    role: Mapped[Role] = relationship(lazy="joined")


class Share(Base):
    __tablename__ = "shares"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    allow_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Anonymes Überschreiben nur, wenn die Freigabe es explizit erlaubt (Default aus).
    overwrite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[UUID] = _uuid_pk()
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    parent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_expected: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256_expected: Mapped[str | None] = mapped_column(String(64), nullable=True)
    offset: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    spool_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[UploadStatus] = mapped_column(_enum(UploadStatus), default=UploadStatus.pending)
    protocol: Mapped[str] = mapped_column(String(16), default="http")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = _uuid_pk()
    ts: Mapped[datetime] = _created_at()
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_entry: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    protocol: Mapped[str] = mapped_column(String(16), default="http")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="ok")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[UUID] = _uuid_pk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
