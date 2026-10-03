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
from typing import ClassVar

from pyftpdlib.exceptions import FilesystemError
from pyftpdlib.filesystems import AbstractedFS

from ..config import get_settings
from ..core import authz, content, namespace
from ..db import session_scope
from ..errors import IFSError
from ..models import Entry, EntryType, User
from ..utils import as_utc

_MAX_BUFFER = 1024 * 1024


def _as_fs_error(exc: IFSError) -> FilesystemError:
    """Übersetzt einen Domänenfehler in den pyftpdlib-Fehlertyp (Antwort „550“)."""
    return FilesystemError(str(exc))


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
        # size < 0 bzw. None bedeutet „bis EOF“; size == 0 liefert sofort b"".
        remaining = size if size is not None and size >= 0 else -1
        chunks: list[bytes] = []
        # Ein einzelner S3-Range deckt nur einen Teil des Objekts ab (pyftpdlib
        # liest in 64-KiB-Schritten). Ist der Body erschöpft, obwohl der Blob
        # noch Bytes meldet, wird ab `self._pos` ein neuer Range geöffnet.
        while self._pos < self._blob.size and remaining != 0:
            fresh = self._body is None
            if fresh:
                end = None
                if remaining >= 0:
                    end = min(self._pos + remaining - 1, self._blob.size - 1)
                response = content.get_object(self._blob.storage_key, self._pos, end)
                self._body = response["Body"]
            chunk = self._body.read(remaining if remaining >= 0 else _MAX_BUFFER)
            if not chunk:
                self._reset()
                # Ein soeben geöffneter Body ohne Daten heißt: Das Objekt ist
                # kürzer als im Blob vermerkt. Abbrechen statt erneut öffnen
                # (sonst droht eine Endlosschleife an derselben Position).
                if fresh:
                    break
                continue
            chunks.append(chunk)
            self._pos += len(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        return b"".join(chunks)

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
    # stat-Ergebnisse der gerade formatierten Auflistung (LIST/MLSD/MLST).
    # Wird ausschließlich innerhalb von ``format_list``/``format_mlsx`` gesetzt
    # und danach wieder zurückgesetzt; als Klassenattribut bleibt das Attribut
    # auch für per ``__new__`` erzeugte Instanzen (Tests) definiert.
    _stat_cache: ClassVar[dict[str, os.stat_result]] = {}

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

    def _listing_stats(self, basedir: str, listing) -> dict[str, os.stat_result]:
        """Lädt die stat-Daten einer Auflistung gebündelt in einer DB-Session.

        pyftpdlib ruft beim Formatieren von LIST/MLSD/MLST ``stat``/``lstat`` je
        Eintrag auf; ohne Bündelung entstünde pro Eintrag eine eigene Session
        samt Pfadauflösung und Rechteprüfung (N+1). ACLs und Ressourcenrollen
        vererben sich vom Ordner auf die Kinder, daher genügt für sie die
        Prüfung des Basisverzeichnisses. Besitz wird dagegen **nicht** vererbt:
        Kinder, die der Nutzer weder besitzt noch einzeln lesen/schreiben darf,
        bleiben (wie bei der Einzelauflösung) ungecacht und damit unsichtbar.
        Namen ohne exakten Treffer bleiben ebenfalls ungecacht und laufen
        anschließend durch die reguläre (autoritätsgeprüfte) Auflösung.
        """
        base = _norm(basedir)
        names = [str(name) for name in listing]
        if not names:
            return {}
        with session_scope() as db:
            try:
                user = self._user(db)
                parent = self._resolve(db, base)
            except FilesystemError:
                return {}
            if parent is None or parent.type != EntryType.folder:
                return {}
            if not (authz.can(db, user, "read", parent) or authz.can(db, user, "write", parent)):
                return {}
            visible = []
            for child in namespace.list_children(db, parent):
                # Besitz gilt nur je Eintrag; nur wer das Kind selbst lesen oder
                # schreiben darf, bekommt es aus dem Cache (sonst Fallback).
                if child.owner_id == user.id or authz.can(
                    db, user, "read", child
                ) or authz.can(db, user, "write", child):
                    visible.append(child)
        by_name = {child.name: child for child in visible}
        stats: dict[str, os.stat_result] = {}
        for name in names:
            child = by_name.get(name)
            if child is not None:
                stats[_norm(posixpath.join(base, name))] = self._stat_for(child)
        return stats

    def _with_listing_stats(self, basedir: str, listing):
        """Installiert den stat-Cache für die Dauer einer Auflistung.

        Die Namen werden einmal materialisiert, damit Cache und Formatierung
        exakt dieselbe Liste sehen (auch falls ein Aufrufer ein Iterable übergibt).
        """
        names = list(listing)
        cache = self._listing_stats(basedir, names)
        previous = self._stat_cache
        self._stat_cache = cache
        return previous, names

    def format_list(self, basedir, listing, ignore_err=True):
        """LIST: stat-Daten aller Einträge gebündelt statt je Eintrag."""
        previous, names = self._with_listing_stats(basedir, listing)
        try:
            yield from super().format_list(basedir, names, ignore_err)
        finally:
            self._stat_cache = previous

    def format_mlsx(self, basedir, listing, perms, facts, ignore_err=True):
        """MLSD/MLST: stat-Daten aller Einträge gebündelt statt je Eintrag."""
        previous, names = self._with_listing_stats(basedir, listing)
        try:
            yield from super().format_mlsx(basedir, names, perms, facts, ignore_err)
        finally:
            self._stat_cache = previous

    # --- Metadaten -------------------------------------------------------

    def _stat_for(self, entry: Entry) -> os.stat_result:
        mode = 0o40755 if entry.type == EntryType.folder else 0o100644
        mtime = as_utc(entry.updated_at).timestamp() if entry.updated_at else time.time()
        return os.stat_result((mode, 0, 0, 1, 0, 0, entry.size, mtime, mtime, mtime))

    def stat(self, path):
        # Innerhalb einer Auflistung sind die Werte bereits gebündelt geladen
        # (siehe ``_listing_stats``); außerhalb ist der Cache leer.
        cached = self._stat_cache.get(_norm(path))
        if cached is not None:
            return cached
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
        return as_utc(entry.updated_at).timestamp() if entry.updated_at else time.time()

    # --- Mutationen ------------------------------------------------------

    def mkdir(self, path):
        path = _norm(path)
        parent_path = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)
        try:
            with session_scope() as db:
                user = self._user(db)
                parent = self._resolve(db, parent_path)
                if parent is None:
                    raise FilesystemError("Zielverzeichnis nicht gefunden")
                authz.authorize(db, user, "write", parent)
                namespace.create_folder(db, parent, name, user.id)
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

    def rmdir(self, path):
        path = _norm(path)
        try:
            with session_scope() as db:
                user = self._user(db)
                entry = self._resolve(db, path)
                if entry is None or entry.type != EntryType.folder:
                    raise FilesystemError("Verzeichnis nicht gefunden")
                if namespace.list_children(db, entry):
                    raise FilesystemError("Verzeichnis nicht leer")
                authz.authorize(db, user, "delete", entry)
                namespace.soft_delete(db, entry, user.id)
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

    def remove(self, path):
        path = _norm(path)
        try:
            with session_scope() as db:
                user = self._user(db)
                entry = self._resolve(db, path)
                if entry is None or entry.type != EntryType.file:
                    raise FilesystemError("Datei nicht gefunden")
                authz.authorize(db, user, "delete", entry)
                namespace.soft_delete(db, entry, user.id)
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

    def rename(self, src, dst):
        src = _norm(src)
        dst = _norm(dst)
        dst_parent_path = posixpath.dirname(dst) or "/"
        dst_name = posixpath.basename(dst)
        try:
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
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

    def utime(self, path, timeval):
        from datetime import datetime

        path = _norm(path)
        try:
            with session_scope() as db:
                user = self._user(db)
                entry = self._resolve(db, path)
                if entry is None:
                    raise FilesystemError("Datei nicht gefunden")
                authz.authorize(db, user, "write", entry)
                entry.updated_at = datetime.fromtimestamp(timeval, tz=UTC)
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

    def chmod(self, path, mode):
        # Rechte werden ausschließlich über den IFS-Kern verwaltet.
        path = _norm(path)
        try:
            with session_scope() as db:
                user = self._user(db)
                entry = self._resolve(db, path)
                if entry is None:
                    raise FilesystemError("Datei nicht gefunden")
                authz.authorize(db, user, "write", entry)
        except IFSError as exc:
            raise _as_fs_error(exc) from exc

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
                mtime=as_utc(entry.updated_at).timestamp() if entry.updated_at else time.time(),
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
            # Absehbare Fehler (Namenskonflikt mit einem Ordner, ungültiger Name)
            # bereits vor dem Spool-Anlegen melden. Quota- und Größenlimitfehler
            # können erst beim Finalisieren auffallen, weil pyftpdlib die
            # 226-Antwort vor `on_file_received` sendet – das ist nachträglich
            # nicht mehr korrigierbar.
            try:
                namespace.ensure_writable(db, parent, name, user.id, 0)
            except IFSError as exc:
                raise _as_fs_error(exc) from exc

        writer = _UploadWriter(
            content.new_spool_path(), path, limit=get_settings().max_upload_size
        )

        # APPE / REST: bestehenden Inhalt vorladen (eine Session statt zwei)
        if mode.startswith(("a", "r+")):
            try:
                from ..models import Blob, Version

                with session_scope() as db:
                    user = self._user(db)
                    entry = self._resolve(db, path)
                    if entry is not None and entry.type == EntryType.file:
                        # Auch das Vorladen ist ein Lesezugriff: Ein
                        # write-only-Nutzer darf fremden Inhalt nicht in den
                        # Spool kopieren (Defense-in-Depth zu F2).
                        authz.authorize(db, user, "read", entry)
                        version = (
                            db.get(Version, entry.current_version_id)
                            if entry.current_version_id
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

        old = self._pending.pop(path, None)
        if old is not None:
            # Ein alter, nicht finalisierter Upload auf denselben Pfad darf keinen
            # verwaisten Spool hinterlassen.
            content.remove_spool(old[2])
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
        except IFSError as exc:
            raise _as_fs_error(exc) from exc
        finally:
            content.remove_spool(spool)

    def discard_pending(self, path: str) -> None:
        """Verwirft einen unvollständigen Upload und löscht dessen Spool."""
        info = self._pending.pop(_norm(path), None)
        if info is not None:
            content.remove_spool(info[2])

    def cleanup(self) -> None:
        for _, _, spool in list(self._pending.values()):
            content.remove_spool(spool)
        self._pending.clear()
