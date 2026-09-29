"""Virtuelles Dateisystem für pyftpdlib, angebunden an den IFS-Kern.

pyftpdlib arbeitet synchron und ruft os-ähnliche Methoden auf. Diese Klasse
übersetzt virtuelle FTP-Pfade (z. B. "/projekte/2026") in Namespace-Einträge
und streamt Dateiinhalte aus bzw. nach S3.
"""

from __future__ import annotations

import os
import posixpath
import time
from datetime import UTC

from pyftpdlib.exceptions import FilesystemError
from pyftpdlib.filesystems import AbstractedFS

from ..config import get_settings
from ..core import authz, content, namespace
from ..db import session_scope
from ..models import Entry, EntryType, User

_MAX_BUFFER = 1024 * 1024


def _norm(path: str) -> str:
    """Tolerante Normalisierung eines virtuellen FS-Pfades ('..' klammert am Root)."""
    parts: list[str] = []
    for segment in str(path).replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/" + "/".join(parts)


class _BlobRef:
    __slots__ = ("mime", "mtime", "size", "storage_key")

    def __init__(self, storage_key: str, size: int, mime: str | None, mtime: float) -> None:
        self.storage_key = storage_key
        self.size = size
        self.mime = mime
        self.mtime = mtime


class _S3Reader:
    """Datei-ähnliches Leseobjekt; nutzt S3-Range-Requests (unterstützt REST/Seek)."""

    def __init__(self, blob: _BlobRef, name: str) -> None:
        self._blob = blob
        self.name = name
        self._pos = 0
        self._body = None
        self._closed = False

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._blob.size + offset
        self._reset()
        return self._pos

    def _reset(self) -> None:
        if self._body is not None:
            try:
                self._body.close()
            except Exception:
                pass
            self._body = None

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if self._pos >= self._blob.size:
            return b""
        if self._body is None:
            end = None
            if size is not None and size >= 0:
                end = min(self._pos + size - 1, self._blob.size - 1)
            response = content.get_object(self._blob.storage_key, self._pos, end)
            self._body = response["Body"]
        data = self._body.read(size if size is not None and size >= 0 else _MAX_BUFFER)
        if not data:
            self._reset()
        else:
            self._pos += len(data)
        return data

    def close(self) -> None:
        self._reset()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class _UploadWriter:
    """Datei-ähnliches Schreibobjekt; spoolt lokal.

    Die Finalisierung (Blob + Version) erfolgt erst nach erfolgreichem Transfer
    über `IFSFilesystem.finalize_received()`.
    """

    def __init__(self, path: str, name: str, limit: int | None = None) -> None:
        self.name = name
        self._path = path
        self._handle = open(path, "wb")
        self._closed = False
        # Harte Obergrenze (IFS_MAX_UPLOAD_SIZE), damit FTP-Uploads den Spool
        # nicht unbegrenzt füllen können. None = unbegrenzt.
        self._limit = limit if limit and limit > 0 else None

    def write(self, data: bytes) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        written = self._handle.write(data)
        if (
            self._limit is not None
            and os.fstat(self._handle.fileno()).st_size > self._limit
        ):
            raise FilesystemError("Maximale Dateigröße überschritten")
        return written

    def flush(self) -> None:
        self._handle.flush()

    def tell(self) -> int:
        return self._handle.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def close(self) -> None:
        if not self._closed:
            self._handle.flush()
            self._handle.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class IFSFilesystem(AbstractedFS):
    def __init__(self, root, cmd_channel):
        super().__init__(root, cmd_channel)
        self._pending: dict[str, tuple[str, str, str]] = {}  # pfad -> (parent, name, spool)

    # --- Hilfsfunktionen -------------------------------------------------

    @property
    def _username(self) -> str:
        return getattr(self.cmd_channel, "username", "") or ""

    def _user(self, db) -> User:
        from sqlalchemy import select

        user = db.execute(select(User).where(User.username == self._username)).scalars().first()
        if user is None or not user.is_active:
            raise FilesystemError("Unbekannter oder deaktivierter Benutzer")
        return user

    def _resolve(self, db, path: str) -> Entry | None:
        """Löst einen FTP-Pfad relativ zur Wurzel des eingeloggten Benutzers auf."""
        root = namespace.ensure_root(db, self._user(db).id)
        return namespace.resolve_path(db, path, root)

    def _lookup(self, path: str) -> Entry | None:
        with session_scope() as db:
            return self._resolve(db, _norm(path))

    def _visible(self, path: str) -> Entry | None:
        """Eintrag nur zurückgeben, wenn der Benutzer ihn lesen/schreiben darf."""
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, _norm(path))
            if entry is None:
                return None
            if not (authz.can(db, user, "read", entry) or authz.can(db, user, "write", entry)):
                return None
            return entry

    def _resolve_authorized(self, path: str) -> Entry | None:
        """Auflösung mit Lese-/Schreibprüfung (Defense-in-Depth zum Command-Gate)."""
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, _norm(path))
            if entry is not None and not (
                authz.can(db, user, "read", entry) or authz.can(db, user, "write", entry)
            ):
                raise FilesystemError("Kein Zugriff")
            return entry

    # --- Pfadkonvertierung ----------------------------------------------

    def ftp2fs(self, ftppath):
        return _norm(self.ftpnorm(ftppath))

    def fs2ftp(self, fspath):
        return _norm(fspath)

    def validpath(self, path):
        return True

    def realpath(self, path):
        return _norm(path)

    def chdir(self, path):
        path = _norm(path)
        entry = self._resolve_authorized(path)
        if entry is None or entry.type != EntryType.folder:
            raise FilesystemError("Verzeichnis nicht gefunden")
        self.cwd = path

    # --- Verzeichnisinhalte ---------------------------------------------

    def listdir(self, path):
        path = _norm(path)
        with session_scope() as db:
            entry = self._resolve(db, path)
            if entry is None or entry.type != EntryType.folder:
                raise FilesystemError("Verzeichnis nicht gefunden")
            user = self._user(db)
            if not (authz.can(db, user, "read", entry) or authz.can(db, user, "write", entry)):
                raise FilesystemError("Kein Zugriff")
            return [child.name for child in namespace.list_children(db, entry)]

    listdirinfo = listdir

    # --- Metadaten -------------------------------------------------------

    def _stat_for(self, entry: Entry) -> os.stat_result:
        mode = 0o40755 if entry.type == EntryType.folder else 0o100644
        mtime = entry.updated_at.timestamp() if entry.updated_at else time.time()
        return os.stat_result((mode, 0, 0, 1, 0, 0, entry.size, mtime, mtime, mtime))

    def stat(self, path):
        entry = self._resolve_authorized(_norm(path))
        if entry is None:
            raise FilesystemError("Datei oder Verzeichnis nicht gefunden")
        return self._stat_for(entry)

    lstat = stat

    def isdir(self, path):
        entry = self._visible(_norm(path))
        return entry is not None and entry.type == EntryType.folder

    def isfile(self, path):
        entry = self._visible(_norm(path))
        return entry is not None and entry.type == EntryType.file

    def exists(self, path):
        return self._visible(_norm(path)) is not None

    lexists = exists

    def getsize(self, path):
        entry = self._resolve_authorized(_norm(path))
        if entry is None:
            raise FilesystemError("Datei nicht gefunden")
        return entry.size

    def getmtime(self, path):
        entry = self._resolve_authorized(_norm(path))
        if entry is None:
            raise FilesystemError("Datei nicht gefunden")
        return entry.updated_at.timestamp() if entry.updated_at else time.time()

    # --- Mutationen ------------------------------------------------------

    def mkdir(self, path):
        path = _norm(path)
        parent_path = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)
        with session_scope() as db:
            user = self._user(db)
            parent = self._resolve(db, parent_path)
            if parent is None:
                raise FilesystemError("Zielverzeichnis nicht gefunden")
            authz.authorize(db, user, "write", parent)
            namespace.create_folder(db, parent, name, user.id)

    def rmdir(self, path):
        path = _norm(path)
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, path)
            if entry is None or entry.type != EntryType.folder:
                raise FilesystemError("Verzeichnis nicht gefunden")
            if namespace.list_children(db, entry):
                raise FilesystemError("Verzeichnis nicht leer")
            authz.authorize(db, user, "delete", entry)
            namespace.soft_delete(db, entry, user.id)

    def remove(self, path):
        path = _norm(path)
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, path)
            if entry is None or entry.type != EntryType.file:
                raise FilesystemError("Datei nicht gefunden")
            authz.authorize(db, user, "delete", entry)
            namespace.soft_delete(db, entry, user.id)

    def rename(self, src, dst):
        src = _norm(src)
        dst = _norm(dst)
        dst_parent_path = posixpath.dirname(dst) or "/"
        dst_name = posixpath.basename(dst)
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, src)
            if entry is None:
                raise FilesystemError("Quelle nicht gefunden")
            target = self._resolve(db, dst_parent_path)
            if target is None:
                raise FilesystemError("Zielverzeichnis nicht gefunden")
            authz.authorize(db, user, "read", entry)
            authz.authorize(db, user, "write", entry)
            authz.authorize(db, user, "write", target)
            if entry.parent_id != target.id:
                namespace.move(db, entry, target)
            if entry.name != dst_name:
                namespace.rename(db, entry, dst_name)

    def utime(self, path, timeval):
        from datetime import datetime

        path = _norm(path)
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, path)
            if entry is None:
                raise FilesystemError("Datei nicht gefunden")
            authz.authorize(db, user, "write", entry)
            entry.updated_at = datetime.fromtimestamp(timeval, tz=UTC)

    def chmod(self, path, mode):
        # Rechte werden ausschließlich über den IFS-Kern verwaltet.
        path = _norm(path)
        with session_scope() as db:
            user = self._user(db)
            entry = self._resolve(db, path)
            if entry is None:
                raise FilesystemError("Datei nicht gefunden")
            authz.authorize(db, user, "write", entry)

    def mkstemp(self, suffix="", prefix="", dir=None, mode="wb"):
        raise FilesystemError("STOU wird nicht unterstützt")

    # --- Dateizugriff ----------------------------------------------------

    def open(self, filename, mode):
        path = _norm(filename)
        is_write = "w" in mode or "a" in mode or ("r" in mode and "+" in mode)
        if is_write:
            return self._open_writer(path, mode)
        return self._open_reader(path)

    def _open_reader(self, path: str):
        with session_scope() as db:
            entry = self._resolve(db, path)
            if entry is None or entry.type != EntryType.file or entry.current_version_id is None:
                raise FilesystemError("Datei nicht gefunden")
            user = self._user(db)
            authz.authorize(db, user, "read", entry)
            from ..models import Blob, Version

            version = db.get(Version, entry.current_version_id)
            blob = db.get(Blob, version.blob_id) if version else None
            if blob is None:
                raise FilesystemError("Blob fehlt")
            ref = _BlobRef(
                storage_key=blob.storage_key,
                size=blob.size,
                mime=entry.mime,
                mtime=entry.updated_at.timestamp() if entry.updated_at else time.time(),
            )
        return _S3Reader(ref, path)

    def _open_writer(self, path: str, mode: str):
        parent_path = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)
        if not name:
            raise FilesystemError("Ungültiger Dateiname")

        with session_scope() as db:
            user = self._user(db)
            parent = self._resolve(db, parent_path)
            if parent is None or parent.type != EntryType.folder:
                raise FilesystemError("Zielverzeichnis nicht gefunden")
            authz.authorize(db, user, "write", parent)

        writer = _UploadWriter(
            content.new_spool_path(), path, limit=get_settings().max_upload_size
        )

        # APPE / REST: bestehenden Inhalt vorladen
        if mode.startswith(("a", "r+")):
            try:
                existing = self._lookup(path)
                if existing is not None and existing.type == EntryType.file:
                    with session_scope() as db:
                        user = self._user(db)
                        entry = self._resolve(db, path)
                        if entry is not None:
                            # Auch das Vorladen ist ein Lesezugriff: Ein
                            # write-only-Nutzer darf fremden Inhalt nicht in den
                            # Spool kopieren (Defense-in-Depth zu F2).
                            authz.authorize(db, user, "read", entry)
                        from ..models import Blob, Version

                        version = (
                            db.get(Version, entry.current_version_id)
                            if entry is not None and entry.current_version_id
                            else None
                        )
                        blob = db.get(Blob, version.blob_id) if version else None
                        if blob is not None:
                            self._preload(blob.storage_key, writer._path)
                    if mode.startswith("a"):
                        writer._handle.close()
                        writer._handle = open(writer._path, "ab")
                elif mode.startswith("r+"):
                    writer.close()
                    raise FilesystemError("REST auf nicht vorhandene Datei")
            except Exception:
                # Kein verwaister Spool, wenn das Vorladen abgelehnt oder
                # abgebrochen wird.
                writer.close()
                content.remove_spool(writer._path)
                raise

        self._pending[path] = (parent_path, name, writer._path)
        return writer

    @staticmethod
    def _preload(storage_key: str, spool_path: str) -> None:
        response = content.get_object(storage_key)
        with open(spool_path, "wb") as handle:
            handle.writelines(content.stream_out(response["Body"]))

    # --- Finalisierung / Aufräumen --------------------------------------

    def finalize_received(self, filename: str) -> None:
        path = _norm(filename)
        info = self._pending.pop(path, None)
        if info is None:
            return
        parent_path, name, spool = info
        try:
            with session_scope() as db:
                user = self._user(db)
                parent = self._resolve(db, parent_path)
                if parent is None:
                    raise FilesystemError("Zielverzeichnis nicht gefunden")
                authz.authorize(db, user, "write", parent)
                # Defense-in-Depth: Größe und absehbare Konflikte vor dem S3-Upload
                # prüfen, damit kein verwaistes Objekt entsteht.
                size = os.path.getsize(spool)
                limit = get_settings().max_upload_size
                if limit and size > limit:
                    raise FilesystemError("Maximale Dateigröße überschritten")
                namespace.ensure_writable(db, parent, name, user.id, size)
                blob = content.store_blob(db, spool, None)
                namespace.apply_write(db, parent, name, user.id, blob, None)
        finally:
            content.remove_spool(spool)

    def cleanup(self) -> None:
        for _, _, spool in list(self._pending.values()):
            content.remove_spool(spool)
        self._pending.clear()
