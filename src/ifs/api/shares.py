"""Freigaben und externe Links."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core import archive, audit, authz, content, namespace, ratelimit
from ..errors import PermissionDenied
from ..models import Blob, Entry, EntryType, Share, User, Version
from ..security import generate_token, hash_password, verify_password
from ..utils import content_disposition, safe_mime, utcnow
from .deps import client_ip, get_current_user, get_db
from .schemas import BulkTokenResult, BulkTokens, ShareCreate, ShareDetailOut, ShareOut, ShareUpdate

router = APIRouter(tags=["shares"])

# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
_IN_CHUNK_SIZE = 500


def _to_out(share: Share) -> ShareOut:
    return ShareOut(
        token=share.token,
        entry_id=share.entry_id,
        expires_at=share.expires_at,
        max_downloads=share.max_downloads,
        downloads=share.downloads,
        has_password=share.password_hash is not None,
        allow_upload=share.allow_upload,
        overwrite=share.overwrite,
    )


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _load_share(db: Session, token: str) -> Share:
    share = db.get(Share, token)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Freigabe nicht gefunden")
    return share


def _check_expiry(share: Share) -> None:
    expires_at = _as_aware(share.expires_at)
    if expires_at is not None and expires_at < utcnow():
        raise HTTPException(status.HTTP_410_GONE, "Freigabe ist abgelaufen")


def _load_valid(db: Session, token: str) -> Share:
    share = _load_share(db, token)
    _check_expiry(share)
    if share.max_downloads is not None and share.downloads >= share.max_downloads:
        raise HTTPException(status.HTTP_410_GONE, "Download-Limit erreicht")
    return share


def _check_share_password(share: Share, request: Request, supplied: str | None) -> None:
    """Prüft das Freigabe-Passwort inkl. Brute-Force-Schutz (Rate-Limit)."""
    if share.password_hash is None:
        return
    account_key, ip_key = ratelimit.keys(client_ip(request), f"share:{share.token}")
    retry = ratelimit.retry_after(account_key, ip_key)
    if retry:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Fehlversuche – bitte später erneut versuchen.",
            headers={"Retry-After": str(retry)},
        )
    if not verify_password(share.password_hash, supplied or ""):
        ratelimit.record_failure(account_key, ip_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Passwort erforderlich oder falsch")
    ratelimit.record_success(account_key, ip_key)


def _available_name(db: Session, parent: Entry, name: str) -> str:
    """Weicht bei Namenskonflikten auf 'name (2).ext' aus – verhindert Überschreiben."""
    if namespace.find_child(db, parent.id, name) is None:
        return name
    base, dot, ext = name.partition(".")
    suffix = f".{ext}" if dot else ""
    index = 2
    while True:
        candidate = f"{base} ({index}){suffix}"
        if namespace.find_child(db, parent.id, candidate) is None:
            return candidate
        index += 1


@router.post("/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
def create_share(
    payload: ShareCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ShareOut:
    entry = namespace.get_entry(db, payload.entry_id)
    # `share` allein reicht nicht: Wer den Inhalt nicht lesen darf, darf ihn auch
    # nicht über einen öffentlichen Link zugänglich machen (sonst wäre `share`
    # faktisch ein `read`-Recht).
    authz.authorize(db, user, "read", entry)
    authz.authorize(db, user, "share", entry)
    # `allow_upload` erlaubt anonymes Schreiben in den Ordner. Das setzt voraus,
    # dass der Ersteller dort selbst schreiben darf – sonst ließe sich das
    # fehlende `write`-Recht über einen Drop-Link umgehen.
    if payload.allow_upload:
        authz.authorize(db, user, "write", entry)
    share = Share(
        token=generate_token(24),
        entry_id=entry.id,
        created_by=user.id,
        password_hash=hash_password(payload.password) if payload.password else None,
        expires_at=payload.expires_at,
        max_downloads=payload.max_downloads,
        allow_upload=payload.allow_upload,
        overwrite=payload.overwrite,
    )
    db.add(share)
    db.flush()
    audit.record(
        db, "share.create", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request), details={"entry_id": str(entry.id)},
    )
    return _to_out(share)


def _detail(
    share: Share,
    entry: Entry | None,
    creator: User | None,
    entry_path: str | None,
) -> ShareDetailOut:
    return ShareDetailOut(
        token=share.token,
        entry_id=share.entry_id,
        entry_name=entry.name if entry else None,
        entry_path=entry_path if entry else None,
        entry_type=entry.type.value if entry else None,
        created_by=share.created_by,
        created_by_username=creator.username if creator else None,
        expires_at=share.expires_at,
        max_downloads=share.max_downloads,
        downloads=share.downloads,
        has_password=share.password_hash is not None,
        allow_upload=share.allow_upload,
        overwrite=share.overwrite,
        created_at=share.created_at,
    )


def _fetch_entries(db: Session, entry_ids: list[UUID]) -> list[Entry]:
    """Lädt Einträge zu den IDs (gechunkt wegen IN-Parameterlimits)."""
    entries: list[Entry] = []
    for start in range(0, len(entry_ids), _IN_CHUNK_SIZE):
        chunk = entry_ids[start : start + _IN_CHUNK_SIZE]
        entries.extend(
            db.execute(select(Entry).where(Entry.id.in_(chunk))).scalars().all()
        )
    return entries


def _path_map(db: Session, entries: list[Entry]) -> dict[UUID, str]:
    """Pfade aller Einträge mit höchstens einer Query je Baumebene.

    Entspricht ``namespace.path_of``: Die Kette endet an der Wurzel
    (``parent_id is None``), an einem fehlenden Parent oder bei einem Zyklus.
    """
    nodes: dict[UUID, Entry] = {entry.id: entry for entry in entries}
    frontier = {entry.parent_id for entry in entries if entry.parent_id is not None}
    while frontier:
        missing = [entry_id for entry_id in frontier if entry_id not in nodes]
        if not missing:
            break
        loaded = _fetch_entries(db, missing)
        if not loaded:
            break
        for node in loaded:
            nodes[node.id] = node
        frontier = {node.parent_id for node in loaded if node.parent_id is not None}
    paths: dict[UUID, str] = {}
    for entry in entries:
        parts: list[str] = []
        seen: set[UUID] = set()
        node: Entry | None = entry
        while node is not None and node.parent_id is not None:
            if node.id in seen:
                # Defense-in-Depth: Ein Zyklus darf nicht zur Endlosschleife führen.
                break
            seen.add(node.id)
            parts.append(node.name)
            node = nodes.get(node.parent_id)
        paths[entry.id] = "/" + "/".join(reversed(parts))
    return paths


def _details(db: Session, shares: list[Share]) -> list[ShareDetailOut]:
    """Baut mehrere Share-Details mit gebündelten Abfragen statt N+1-Lazy-Loads."""
    entries: dict[UUID, Entry] = {}
    creators: dict[UUID, User] = {}
    if shares:
        entry_ids = list({share.entry_id for share in shares})
        entries = {entry.id: entry for entry in _fetch_entries(db, entry_ids)}
        creator_ids = list(
            {share.created_by for share in shares if share.created_by is not None}
        )
        for start in range(0, len(creator_ids), _IN_CHUNK_SIZE):
            chunk = creator_ids[start : start + _IN_CHUNK_SIZE]
            for user in db.execute(select(User).where(User.id.in_(chunk))).scalars().all():
                creators[user.id] = user
    paths = _path_map(db, list(entries.values()))
    return [
        _detail(
            share,
            entries.get(share.entry_id),
            creators.get(share.created_by) if share.created_by is not None else None,
            paths.get(share.entry_id),
        )
        for share in shares
    ]


def _to_detail(db: Session, share: Share) -> ShareDetailOut:
    entry = db.get(Entry, share.entry_id)
    creator = db.get(User, share.created_by) if share.created_by else None
    return _detail(share, entry, creator, namespace.path_of(db, entry) if entry else None)


@router.get("/shares", response_model=list[ShareDetailOut])
def list_shares(
    entry_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ShareDetailOut]:
    stmt = select(Share)
    if not authz.is_system_admin(db, user):
        stmt = stmt.where(Share.created_by == user.id)
    if entry_id is not None:
        stmt = stmt.where(Share.entry_id == entry_id)
    shares = db.execute(stmt.order_by(Share.created_at.desc())).scalars().all()
    return _details(db, shares)


@router.patch("/shares/{token}", response_model=ShareDetailOut)
def update_share(
    token: str,
    payload: ShareUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ShareDetailOut:
    share = db.get(Share, token)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Freigabe nicht gefunden")
    entry = _authorize_manage(db, user, share)

    data = payload.model_dump(exclude_unset=True)
    # Upload/Überschreiben scharfschalten erfordert weiterhin `write` am Eintrag;
    # reines Abschalten bleibt auch ohne Schreibrecht erlaubt.
    upload_enabled = data.get("allow_upload") if "allow_upload" in data else share.allow_upload
    enable_upload = data.get("allow_upload") is True
    enable_overwrite = data.get("overwrite") is True and bool(upload_enabled)
    if (enable_upload or enable_overwrite) and (
        entry is None or not authz.can(db, user, "write", entry)
    ):
        raise PermissionDenied(
            "Zum Erlauben von Upload/Überschreiben fehlt das Schreibrecht am Eintrag"
        )
    if "password" in data:
        share.password_hash = hash_password(data["password"]) if data["password"] else None
    if "expires_at" in data:
        share.expires_at = data["expires_at"]
    if "max_downloads" in data:
        share.max_downloads = data["max_downloads"]
    if "allow_upload" in data:
        share.allow_upload = bool(data["allow_upload"])
    if "overwrite" in data:
        share.overwrite = bool(data["overwrite"])
    db.flush()
    audit.record(
        db, "share.update", actor_id=user.id, target_entry=entry.id if entry else None,
        protocol="http", ip=client_ip(request), details={"fields": sorted(data.keys())},
    )
    return _to_detail(db, share)


@router.get("/shares/{token}", response_model=ShareOut)
def get_share(
    token: str,
    request: Request,
    response: Response,
    x_share_password: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ShareOut:
    share = _load_valid(db, token)
    # Metadaten (u. a. entry_id) nur nach korrekter Passwortprüfung; schützt
    # zugleich vor unbegrenztem Abruf (Rate-Limit in _check_share_password).
    _check_share_password(share, request, x_share_password)
    response.headers["Cache-Control"] = "no-store"
    return _to_out(share)


def _serve_share(token: str, request: Request, supplied: str | None, db: Session) -> Response:
    share = _load_share(db, token)
    _check_expiry(share)
    _check_share_password(share, request, supplied)

    # Downloadzähler atomar erhöhen und Limit gleichzeitig durchsetzen.
    result = db.execute(
        update(Share)
        .where(Share.token == token)
        .where(or_(Share.max_downloads.is_(None), Share.downloads < Share.max_downloads))
        .values(downloads=Share.downloads + 1)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_410_GONE, "Download-Limit erreicht")

    entry = namespace.get_entry(db, share.entry_id)

    # Ordner-Freigabe: Teilbaum als ZIP streamen (Download-Zähler wurde bereits erhöht).
    if entry.type == EntryType.folder:
        audit.record(
            db, "share.download", actor_id=share.created_by, target_entry=entry.id,
            protocol="http", ip=client_ip(request), details={"folder": True},
        )
        path = archive.build_zip(db, [entry])
        size = os.path.getsize(path)
        headers = {
            "Content-Type": "application/zip",
            "Content-Length": str(size),
            "Content-Disposition": content_disposition(f"{entry.name}.zip"),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }

        def folder_iterator():
            try:
                with open(path, "rb") as handle:
                    yield from iter(lambda: handle.read(1024 * 1024), b"")
            finally:
                archive.remove_zip(path)

        return StreamingResponse(
            folder_iterator(), media_type="application/zip", headers=headers
        )

    if entry.current_version_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Freigabe enthält keinen Dateiinhalt")
    version = db.get(Version, entry.current_version_id)
    blob = db.get(Blob, version.blob_id) if version else None
    if blob is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Blob fehlt")

    audit.record(
        db, "share.download", actor_id=share.created_by, target_entry=entry.id,
        protocol="http", ip=client_ip(request),
    )

    response = content.get_object(blob.storage_key)
    headers = {
        "Content-Type": safe_mime(entry.mime),
        "Content-Length": str(blob.size),
        "Content-Disposition": content_disposition(entry.name),
        "ETag": f'"{blob.sha256}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(content.stream_out(response["Body"]), headers=headers)


@router.get("/shares/{token}/content")
def download_shared(
    token: str,
    request: Request,
    x_share_password: str | None = Header(default=None),
    password: str | None = None,  # veraltet: Passwort bitte per Header senden
    db: Session = Depends(get_db),
) -> Response:
    return _serve_share(
        token, request, x_share_password if x_share_password is not None else password, db
    )


@router.get("/shares/{token}/content/{filename}")
def download_shared_named(
    token: str,
    filename: str,
    request: Request,
    x_share_password: str | None = Header(default=None),
    password: str | None = None,  # veraltet: Passwort bitte per Header senden
    db: Session = Depends(get_db),
) -> Response:
    """Wie `/content`, aber mit Dateiname im Pfad – so speichert `curl -O` korrekt."""
    _ = filename  # nur für die Benennung durch den Client
    return _serve_share(
        token, request, x_share_password if x_share_password is not None else password, db
    )


@router.post("/shares/{token}/upload", status_code=status.HTTP_201_CREATED)
@router.put("/shares/{token}/upload", status_code=status.HTTP_201_CREATED)
async def upload_shared(
    token: str,
    request: Request,
    name: str = "",
    mime: str | None = None,
    x_share_password: str | None = Header(default=None),
    password: str | None = None,  # veraltet: Passwort bitte per Header senden
    db: Session = Depends(get_db),
) -> dict:
    """Öffentlicher Upload in einen geteilten Ordner (Body = Dateiinhalt).

    Überschrieben wird nur, wenn die Freigabe mit ``overwrite=true`` erstellt
    wurde; sonst weicht IFS bei Namenskonflikten aus.
    """
    share = _load_share(db, token)
    _check_expiry(share)
    if not share.allow_upload:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Upload ist für diese Freigabe nicht erlaubt")
    _check_share_password(
        share, request, x_share_password if x_share_password is not None else password
    )

    entry = namespace.get_entry(db, share.entry_id)
    if entry.type != EntryType.folder:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Upload ist nur in geteilte Ordner möglich"
        )
    creator = db.get(User, share.created_by) if share.created_by else None
    if creator is None or not creator.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Freigabe-Ersteller ist nicht mehr aktiv")
    # Das `write`-Recht wird bei jedem Upload erneut geprüft: Ein Drop-Link darf
    # nur so weit reichen, wie der Ersteller selbst schreiben dürfte. Wird ihm das
    # Recht später entzogen, ist der Upload sofort gesperrt.
    if not authz.can(db, creator, "write", entry):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Freigabe-Ersteller hat kein Schreibrecht (mehr)"
        )

    # Oeffentliche Drop-Links sind anonym erreichbar: hartes Groessenlimit und ein
    # Rate-Limit verhindern das Fluten des Objektspeichers ueber einen geleakten Link.
    ratelimit_key = f"{client_ip(request) or 'unknown'}|{token}"
    if not ratelimit.allow_share_upload(ratelimit_key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Uploads – bitte später erneut versuchen.",
            headers={"Retry-After": str(ratelimit.share_upload_retry_after(ratelimit_key))},
        )

    namespace.validate_name(name)

    limit = get_settings().effective_share_upload_limit
    declared = request.headers.get("content-length")
    if limit and declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß")

    # Standardmäßig nichts überschreiben: bei Namenskonflikt automatisch umbenennen.
    # Überschreiben ist nur bei ausdrücklich so konfigurierter Freigabe erlaubt.
    target_name = name if share.overwrite else _available_name(db, entry, name)

    spool = content.new_spool_path()
    size = 0
    try:
        with open(spool, "wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if limit and size + len(chunk) > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß")
                handle.write(chunk)
                size += len(chunk)
        # Vorabprüfung, damit kein Blob nach S3 hochgeladen wird, wenn Konflikt
        # oder Quota einen anschließenden Rollback erzwingen würden.
        namespace.ensure_writable(db, entry, target_name, creator.id, size)
        blob = content.store_blob(db, spool, mime)
    finally:
        content.remove_spool(spool)

    if not share.overwrite:
        # Das Streamen des Bodys kann lange dauern: In der Zwischenzeit kann
        # parallel eine gleichnamige Datei entstanden sein (TOCTOU). Ohne
        # Überschreibrecht den finalen Namen deshalb erst unmittelbar vor dem
        # Schreiben erneut bestimmen und prüfen. Ein Restrisiko bleibt nur für
        # den kurzen Moment zwischen Prüfung und INSERT bestehen.
        target_name = _available_name(db, entry, name)
        namespace.ensure_writable(db, entry, target_name, creator.id, size)

    stored = namespace.apply_write(db, entry, target_name, creator.id, blob, mime)
    audit.record(
        db, "share.upload", actor_id=creator.id, target_entry=stored.id,
        protocol="http", ip=client_ip(request),
        details={"share_prefix": token[:8], "size": blob.size},
    )
    return {
        "id": str(stored.id),
        "name": stored.name,
        "size": stored.size,
        "sha256": stored.sha256,
    }


def _manageable(
    db: Session, user: User, share: Share, entry: Entry | None, admin_override: bool
) -> bool:
    """Verwaltungsrecht wie ``_authorize_manage``, aber mit geladenem Eintrag."""
    if share.created_by == user.id:
        return True
    if entry is not None and authz.can(db, user, "share", entry):
        return True
    return bool(admin_override and authz.is_system_admin(db, user))


def _authorize_manage(
    db: Session, user: User, share: Share, *, admin_override: bool = False
) -> Entry | None:
    """Erlaubt Verwaltung, auch wenn der Eintrag bereits gelöscht (im Papierkorb) ist.

    ``admin_override`` erlaubt Systemadmins nur das **Widerrufen** fremder
    Freigaben (Missbrauchsschutz). Ein PATCH könnte z. B. den Passwortschutz
    entfernen und damit die Per-User-Isolation aushebeln – dafür muss der
    Aufrufer Ersteller sein oder das `share`-Recht am Eintrag haben.
    """
    entry = db.get(Entry, share.entry_id)
    if not _manageable(db, user, share, entry, admin_override):
        raise PermissionDenied("Kein Recht, diese Freigabe zu verwalten")
    return entry


@router.delete("/shares/{token}", status_code=status.HTTP_204_NO_CONTENT)
def delete_share(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    share = db.get(Share, token)
    if share is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Freigabe nicht gefunden")
    _authorize_manage(db, user, share, admin_override=True)
    entry_id = share.entry_id
    db.delete(share)
    audit.record(
        db, "share.revoke", actor_id=user.id, target_entry=entry_id,
        protocol="http", ip=client_ip(request), details={"token_prefix": token[:8]},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _load_shares(db: Session, tokens: list[str]) -> dict[str, Share]:
    """Lädt die zu den Tokens vorhandenen Freigaben in einer Abfrage."""
    unique_tokens = list(dict.fromkeys(tokens))
    shares: dict[str, Share] = {}
    for start in range(0, len(unique_tokens), _IN_CHUNK_SIZE):
        chunk = unique_tokens[start : start + _IN_CHUNK_SIZE]
        for share in db.execute(select(Share).where(Share.token.in_(chunk))).scalars().all():
            shares[share.token] = share
    return shares


@router.post("/shares/bulk/revoke", response_model=BulkTokenResult)
def bulk_revoke_shares(
    payload: BulkTokens,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkTokenResult:
    """Widerruft mehrere Freigaben; nicht verwaltbare Tokens landen in `failed`."""
    # Freigaben und Einträge gebündelt laden (statt je Token). Fehlende Tokens
    # sowie Duplikate (ein Token wird nur einmal widerrufen) landen in `failed` –
    # exakt wie beim früheren Einzel-Lookup.
    shares = _load_shares(db, payload.tokens)
    entry_ids = list({share.entry_id for share in shares.values()})
    entries = {entry.id: entry for entry in _fetch_entries(db, entry_ids)}

    ok = 0
    failed: list[str] = []
    handled: set[str] = set()
    for token in payload.tokens:
        share = shares.get(token)
        if share is None or token in handled:
            failed.append(token)
            continue
        if not _manageable(db, user, share, entries.get(share.entry_id), True):
            failed.append(token)
            continue
        try:
            with db.begin_nested():
                db.delete(share)
            handled.add(token)
            ok += 1
        except Exception:
            failed.append(token)
    audit.record(
        db, "share.bulk_revoke", actor_id=user.id, protocol="http", ip=client_ip(request),
        details={"ok": ok, "failed": len(failed)},
    )
    return BulkTokenResult(ok=ok, failed=failed)
