"""Pydantic-Schemas der REST-API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import EntryType, PrincipalType, RoleScope


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None = None
    email: str | None = None
    is_admin: bool
    is_active: bool = True
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=1024)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    is_admin: bool = False


class UserUpdate(BaseModel):
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    is_admin: bool | None = None


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=1024)


class ProfileUpdate(BaseModel):
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class UserDetailOut(UserOut):
    groups: list[GroupOut] = []


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MemberAdd(BaseModel):
    user_id: UUID


class EntryOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    type: EntryType
    path: str
    size: int
    mime: str | None = None
    sha256: str | None = None
    created_at: datetime
    updated_at: datetime


class FolderCreate(BaseModel):
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)


class EntryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MoveRequest(BaseModel):
    parent_id: UUID


class CopyRequest(BaseModel):
    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)


class UploadCreate(BaseModel):
    parent_id: UUID
    name: str = Field(min_length=1, max_length=255)
    # Negative Größen würden eine Session erzeugen, die nie abschließbar ist.
    size: int | None = Field(default=None, ge=0)
    # SHA-256 als 64 Hex-Zeichen (Body-/CPU-Schutz vor der Prüfung).
    sha256: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    mime: str | None = Field(default=None, max_length=255)


class UploadOut(BaseModel):
    id: UUID
    offset: int
    status: str


class ShareCreate(BaseModel):
    entry_id: UUID
    expires_at: datetime | None = None
    password: str | None = Field(default=None, max_length=1024)
    # Negatives Limit würde die Freigabe sofort dauerhaft auf 410 setzen.
    max_downloads: int | None = Field(default=None, ge=0)
    allow_upload: bool = False
    # Überschreiben nur bei ausdrücklich erlaubter Freigabe (Default aus).
    overwrite: bool = False


class ShareOut(BaseModel):
    token: str
    entry_id: UUID
    expires_at: datetime | None = None
    max_downloads: int | None = None
    downloads: int
    has_password: bool
    allow_upload: bool
    overwrite: bool


class ShareDetailOut(ShareOut):
    entry_name: str | None = None
    entry_path: str | None = None
    entry_type: str | None = None
    created_by: UUID | None = None
    created_by_username: str | None = None
    created_at: datetime | None = None


class ShareUpdate(BaseModel):
    # Nicht gesetzte Felder bleiben unverändert; explizit null/leer löscht.
    password: str | None = Field(default=None, max_length=1024)
    expires_at: datetime | None = None
    max_downloads: int | None = Field(default=None, ge=0)
    allow_upload: bool | None = None
    overwrite: bool | None = None


class PublishedOut(BaseModel):
    """Veröffentlichung in der eigenen Verwaltungsansicht (mit Pfad)."""

    entry_id: UUID
    name: str
    # Öffentlicher Anzeigename; leer = `name`.
    public_name: str | None = None
    type: EntryType
    size: int
    mime: str | None = None
    path: str | None = None
    published_by: UUID | None = None
    published_by_username: str | None = None
    published_at: datetime


class PublishedPublicOut(BaseModel):
    """Öffentliche Galerie: bewusst ohne interne Pfade/Besitzer-IDs."""

    entry_id: UUID
    # Effektiver Anzeigename (`public_name` oder interner Name).
    name: str
    type: EntryType
    size: int
    mime: str | None = None
    published_by_username: str | None = None
    published_at: datetime


class PublishedInput(BaseModel):
    """Beim Veröffentlichen oder nachträglichen Ändern des Anzeigenamens."""

    public_name: str | None = Field(default=None, max_length=255)


class AclCreate(BaseModel):
    principal_type: PrincipalType
    principal_id: UUID
    # Höchstens so viele Einträge wie es Rechte gibt (Body-/CPU-Schutz).
    perms: list[str] = Field(max_length=5)


class AclOut(BaseModel):
    id: UUID
    entry_id: UUID
    principal_type: PrincipalType
    principal_id: UUID
    perms: int
    inherited: bool


class TrashEntryOut(BaseModel):
    id: UUID
    name: str
    path: str
    type: EntryType
    size: int
    files: int
    trashed_at: datetime
    trashed_by: UUID | None = None
    days_left: int


class RestoreRequest(BaseModel):
    parent_id: UUID | None = None
    new_name: str | None = Field(default=None, min_length=1, max_length=255)


class TrashCount(BaseModel):
    count: int


class VersionOut(BaseModel):
    id: UUID
    seq: int
    size: int
    mime: str | None = None
    sha256: str
    created_by: UUID | None = None
    created_at: datetime
    is_current: bool = False


class RoleOut(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    permissions: int
    permission_names: list[str] = []
    scope: RoleScope
    is_builtin: bool


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    # Höchstens so viele Einträge wie es Rechte gibt (Body-/CPU-Schutz).
    permissions: list[str] = Field(default_factory=list, max_length=5)
    scope: RoleScope = RoleScope.resource


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = Field(default=None, max_length=5)


class RoleAssignmentOut(BaseModel):
    id: UUID
    role_id: UUID
    role_name: str | None = None
    scope: RoleScope | None = None
    principal_type: PrincipalType
    principal_id: UUID
    entry_id: UUID | None = None
    created_at: datetime | None = None


class EntryRoleAssign(BaseModel):
    role_id: UUID
    principal_type: PrincipalType = PrincipalType.user
    principal_id: UUID


class RoleAssignmentCreate(BaseModel):
    principal_type: PrincipalType = PrincipalType.user
    principal_id: UUID
    entry_id: UUID | None = None


class PreviewTokenOut(BaseModel):
    token: str
    url: str
    expires_in: int
    name: str
    mime: str | None = None
    size: int


class ArchiveTokenOut(BaseModel):
    token: str
    url: str
    expires_in: int
    name: str


class ArchiveBulkCreate(BaseModel):
    entry_ids: list[UUID] = Field(min_length=1, max_length=1000)


class ArchiveJobOut(BaseModel):
    token: str
    name: str
    url: str
    status_url: str
    expires_in: int
    total_files: int
    total_bytes: int


class ArchiveJobStatus(BaseModel):
    token: str
    name: str
    status: str
    files_done: int
    total_files: int
    bytes_done: int
    total_bytes: int
    error: str | None = None


class BulkIds(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=1000)


class BulkMove(BulkIds):
    parent_id: UUID


class BulkUserActive(BulkIds):
    active: bool


class BulkUserDelete(BulkIds):
    transfer_to: UUID | None = None


class BulkTokens(BaseModel):
    # Tokens sind max. 64 Zeichen (DB-Spalte); Einzelwerte begrenzen Body-/CPU-Last.
    tokens: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=1000
    )


class BulkResult(BaseModel):
    ok: int
    failed: list[UUID] = []


class BulkTokenResult(BaseModel):
    ok: int
    failed: list[str] = []


class BulkPurgeResult(BulkResult):
    files: int = 0
