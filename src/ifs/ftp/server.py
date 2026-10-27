"""FTPS-Server: TLS-geschützt, Authentifizierung und Rechte über den IFS-Kern."""

from __future__ import annotations

import logging
import posixpath
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.exceptions import AuthenticationFailed
from pyftpdlib.handlers import TLS_DTPHandler, TLS_FTPHandler
from pyftpdlib.servers import ThreadedFTPServer
from sqlalchemy import select

try:  # pyftpdlib >= 2.0
    from pyftpdlib.handlers.ftp.producers import FileProducer
except ImportError:  # pragma: no cover - pyftpdlib 1.x
    from pyftpdlib.handlers import FileProducer

from ..config import get_settings
from ..core import audit, authz, namespace, quota, ratelimit
from ..db import read_scope, session_scope
from ..models import EntryType, User
from ..security import verify_password_safe
from ..utils import utcnow
from . import context as ftp_context
from .fs import IFSFilesystem, _norm

logger = logging.getLogger("ifs.ftp")

# Wird nur von run_server gesetzt. None = synchrone Finalisierung.
_finalize_pool: ThreadPoolExecutor | None = None

# Datenkanal-Durchsatz: pyftpdlib liest/schreibt standardmäßig in 64-KiB-Blöcken.
# Größere Puffer senken die Zahl der recv/send-Syscalls und TLS-Records je
# Transfer. 16 KiB ist die Untergrenze (darunter wird der Durchsatz wieder
# durch Syscall-Overhead begrenzt), Standard 256 KiB.
_DEFAULT_TRANSFER_BUFFER = 256 * 1024
_MIN_TRANSFER_BUFFER = 16 * 1024

# Kommandos, die den Namespace lesen/ändern: Vor der Ausführung muss die
# asynchrone Finalisierung abgeschlossen sein (Read-after-Write). Reine
# Verbindungs-/Transfer-Setup-Kommandos (TYPE, PASV, PORT, NOOP, …)
# bleiben absichtlich außen vor, damit der nächste Upload parallel laufen kann.
_DRAIN_COMMANDS = frozenset(
    {
        "LIST",
        "NLST",
        "MLSD",
        "MLST",
        "SIZE",
        "MDTM",
        "RETR",
        "DELE",
        "RNFR",
        "RNTO",
        "MKD",
        "RMD",
        "APPE",
        "REST",
        "STAT",
        "SITE",
        "QUIT",
    }
)

_PERM_ACTIONS = {
    "e": "read",
    "l": "read",
    "r": "read",
    "d": "delete",
    "f": "write",
    "m": "write",
    "w": "write",
    "a": "write",
    "T": "write",
    "M": "write",
}


def _nearest_entry(db, user, path: str):
    root = namespace.ensure_root(db, user.id)
    current = _norm(path)
    while True:
        entry = namespace.resolve_path(db, current, root)
        if entry is not None:
            return entry
        if current == "/":
            return root
        current = posixpath.dirname(current) or "/"


def _write_target(db, user, fs_path: str) -> ftp_context.WriteTarget | None:
    """Löst Parent und Ziel eines STOR/APPE in einer Session auf und autorisiert beide.

    Damit bleibt die bisherige Semantik erhalten (``has_perm`` prüfte das Ziel,
    ``_open_writer`` den Parent); die zweite Session entfällt. Bei aktiver Quota
    wird bewusst der klassische Pfad genutzt, damit die Vorabprüfung vor dem
    Spool-Anlegen vollständig bleibt.
    """
    if quota.limit_bytes() > 0:
        return None
    parent_path = posixpath.dirname(fs_path) or "/"
    name = posixpath.basename(fs_path)
    if not name:
        return None
    root = namespace.ensure_root(db, user.id)
    parent = namespace.resolve_path(db, parent_path, root)
    if parent is None or parent.type != EntryType.folder:
        return None
    if not authz.can(db, user, "write", parent):
        return None
    existing = namespace.find_child(db, parent.id, name)
    if existing is not None and not authz.can(db, user, "write", existing):
        return None
    return ftp_context.WriteTarget(
        path=fs_path,
        user_id=user.id,
        parent_id=parent.id,
        parent_path=parent_path,
        name=name,
        existing_id=existing.id if existing is not None else None,
        existing_is_folder=existing is not None and existing.type == EntryType.folder,
    )


class IFSAuthorizer(DummyAuthorizer):
    """Bindet FTP-Logins und Berechtigungen an den IFS-Kern."""

    @staticmethod
    def _audit_login(action, result, ip, *, actor_id=None, details=None) -> None:
        with session_scope() as db:
            audit.record(
                db,
                action,
                actor_id=actor_id,
                protocol="ftps",
                ip=ip,
                result=result,
                details=details,
            )

    def validate_authentication(self, username, password, handler):
        ip = getattr(handler, "remote_ip", None)
        account_key, ip_key = ratelimit.keys(ip, username)
        retry = ratelimit.retry_after(account_key, ip_key)
        if retry:
            self._audit_login(
                "auth.login.failed", "locked", ip, details={"username": username}
            )
            raise AuthenticationFailed(
                f"Zu viele Fehlversuche – bitte in {retry}s erneut versuchen."
            )

        with session_scope() as db:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalars().first()
            password_hash = user.password_hash if user is not None else None
            active = bool(user and user.is_active)
            user_id = user.id if user is not None else None

        # Auch bei unbekanntem Nutzer wird ein Hash geprüft (kein Timing-Leak).
        valid = verify_password_safe(password_hash, password)
        if not active or not valid:
            ratelimit.record_failure(account_key, ip_key)
            self._audit_login(
                "auth.login.failed",
                "denied",
                ip,
                actor_id=user_id,
                details={"username": username},
            )
            raise AuthenticationFailed("Benutzername oder Passwort falsch")

        ratelimit.record_success(account_key, ip_key)
        with session_scope() as db:
            db_user = db.get(User, user_id) if user_id is not None else None
            if db_user is not None:
                db_user.last_login_at = utcnow()
            audit.record(db, "auth.login", actor_id=user_id, protocol="ftps", ip=ip)
        # Marker für den Logout-Eintrag beim Verbindungsende.
        handler._ifs_logged_in = True

    def has_user(self, username):
        # Immer True: verhindert Benutzer-Enumeration; die Autorisierung erfolgt
        # ohnehin über validate_authentication und has_perm.
        return True

    def get_home_dir(self, username):
        return "/"

    def get_msg_login(self, username):
        return "IFS FTPS Login erfolgreich."

    def get_msg_quit(self, username):
        return "Auf Wiedersehen."

    def get_perms(self, username):
        return "elradfmwMT"

    def has_perm(self, username, perm, path=None):
        action = _PERM_ACTIONS.get(perm)
        if action is None:
            return False
        if path is None:
            return True
        with read_scope() as db:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalars().first()
            if user is None or not user.is_active:
                return False
            cmd = getattr(ftp_context.ctx, "cmd", None)
            fs_path = getattr(ftp_context.ctx, "fs_path", None)
            if cmd in ("STOR", "APPE") and perm == "w" and fs_path:
                target = _write_target(db, user, fs_path)
                if target is not None:
                    ftp_context.ctx.target = target
                    return True
                # Kein Cache (z. B. Quota aktiv, Parent fehlt): klassischer Pfad.
                ftp_context.ctx.target = None
            entry = _nearest_entry(db, user, path)
            return authz.can(db, user, action, entry)


class IFSDTPHandler(TLS_DTPHandler):
    """FTPS-Datenkanal mit größeren Puffern.

    Die Puffergröße wird in :func:`build_handler` aus
    ``IFS_FTP_TRANSFER_BUFFER_BYTES`` übernommen.
    """

    ac_in_buffer_size = _DEFAULT_TRANSFER_BUFFER
    ac_out_buffer_size = _DEFAULT_TRANSFER_BUFFER


class IFSFTPHandler(TLS_FTPHandler):
    abstracted_fs = IFSFilesystem
    tls_control_required = True
    tls_data_required = True
    # Von run_server gesetzt; None = synchrone Finalisierung (Tests/Fallback).
    finalize_pool: ThreadPoolExecutor | None = None
    finalize_slots: threading.BoundedSemaphore | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ifs_finalizer_lock = threading.Lock()
        self._ifs_finalizers: set[Future] = set()
        self._ifs_path_futures: dict[str, Future] = {}

    def on_file_received(self, file):
        """226 ist bereits gesendet; Finalisierung darf den nächsten Transfer überlappen."""
        info = self.fs.take_pending(file)
        if info is None:
            return
        pool = self.finalize_pool
        if pool is None or self.finalize_slots is None:
            try:
                self.fs.finalize_taken(*info)
            except Exception:
                logger.exception("Finalisieren des FTP-Uploads fehlgeschlagen: %s", file)
            return

        path = info[0]
        with self._ifs_finalizer_lock:
            previous = self._ifs_path_futures.get(path)
        if previous is not None:
            # Gleicher Zielpfad: Reihenfolge wahren, bevor der nächste Stand schreibt.
            try:
                previous.result()
            except Exception:
                logger.exception("Vorherige Finalisierung fehlgeschlagen: %s", path)

        self.finalize_slots.acquire()
        try:
            future = pool.submit(self.fs.finalize_taken, *info)
        except Exception:
            self.finalize_slots.release()
            raise
        with self._ifs_finalizer_lock:
            self._ifs_finalizers.add(future)
            self._ifs_path_futures[path] = future
        future.add_done_callback(lambda fut, p=path: self._ifs_finalizer_done(fut, p))

    def _ifs_finalizer_done(self, future: Future, path: str) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Finalisieren des FTP-Uploads fehlgeschlagen: %s", path)
        with self._ifs_finalizer_lock:
            self._ifs_finalizers.discard(future)
            if self._ifs_path_futures.get(path) is future:
                self._ifs_path_futures.pop(path, None)
        slots = self.finalize_slots
        if slots is not None:
            try:
                slots.release()
            except ValueError:
                pass

    def _ifs_wait_finalizers(self) -> None:
        """Vor lesenden/nicht-STOR-Kommandos: Read-after-Write sicherstellen."""
        if self.finalize_pool is None:
            return
        lock = getattr(self, "_ifs_finalizer_lock", None)
        if lock is None:
            return
        with lock:
            futures = list(self._ifs_finalizers)
            self._ifs_finalizers.clear()
            self._ifs_path_futures.clear()
        for future in futures:
            try:
                future.result()
            except Exception:
                logger.exception("Finalisieren des FTP-Uploads fehlgeschlagen")

    def pre_process_command(self, line, cmd, arg):
        ftp_context.clear()
        upper = cmd.upper()
        if upper in ("STOR", "APPE"):
            ftp_context.ctx.cmd = upper
            try:
                ftp_context.ctx.fs_path = self.fs.ftp2fs(arg)
            except Exception:
                ftp_context.ctx.fs_path = None
        try:
            if self.finalize_pool is not None and upper in _DRAIN_COMMANDS:
                self._ifs_wait_finalizers()
            return super().pre_process_command(line, cmd, arg)
        finally:
            ftp_context.clear()

    def on_incomplete_file_received(self, file):
        # Abgebrochene Uploads (ABOR/Disconnect) hinterlassen sonst einen
        # verwaisten Spool; Fehler dürfen die Verbindung nicht sprengen.
        try:
            self.fs.discard_pending(file)
        except Exception:
            logger.exception("Verwerfen des abgebrochenen FTP-Uploads fehlgeschlagen: %s", file)

    def close(self):
        fs = getattr(self, "fs", None)
        logged_in = getattr(self, "_ifs_logged_in", False)
        username = getattr(self, "username", "") or ""
        ip = getattr(self, "remote_ip", None)
        try:
            super().close()
        finally:
            try:
                self._ifs_wait_finalizers()
            except Exception:
                logger.exception("Warten auf FTP-Finalisierungen fehlgeschlagen")
            if logged_in and username:
                with session_scope() as db:
                    user = db.execute(
                        select(User).where(User.username == username)
                    ).scalars().first()
                    audit.record(
                        db,
                        "auth.logout",
                        actor_id=user.id if user is not None else None,
                        protocol="ftps",
                        ip=ip,
                    )
            if fs is not None:
                try:
                    fs.cleanup()
                except Exception:
                    logger.exception("Aufräumen der FTP-Session fehlgeschlagen")


def build_handler() -> type[IFSFTPHandler]:
    settings = get_settings()
    if not settings.ftp_certfile or not settings.ftp_keyfile:
        raise RuntimeError(
            "FTPS benötigt ein Zertifikat. IFS_FTP_CERTFILE und IFS_FTP_KEYFILE setzen "
            "(z. B. per `openssl req -x509 -newkey rsa:2048 -nodes -keyout ftps.key "
            "-out ftps.crt -days 365 -subj \"/CN=localhost\"`)."
        )
    IFSFTPHandler.authorizer = IFSAuthorizer()
    IFSFTPHandler.certfile = settings.ftp_certfile
    IFSFTPHandler.keyfile = settings.ftp_keyfile
    IFSFTPHandler.banner = settings.ftp_banner
    IFSFTPHandler.passive_ports = settings.passive_port_range
    IFSFTPHandler.masquerade_address = settings.ftp_masquerade_address
    # Durchsatz: größere Blöcke im Datenkanal und beim Datei-Producer.
    buffer_bytes = max(
        _MIN_TRANSFER_BUFFER, int(settings.ftp_transfer_buffer_bytes or 0)
    )
    IFSDTPHandler.ac_in_buffer_size = buffer_bytes
    IFSDTPHandler.ac_out_buffer_size = buffer_bytes
    IFSFTPHandler.dtp_handler = IFSDTPHandler
    FileProducer.buffer_size = buffer_bytes
    # pyftpdlib baut den TLS-Kontext genau einmal je Handler-Klasse und teilt ihn
    # über alle Verbindungen (``TLS_FTPHandler.get_ssl_context``). Nach einer
    # Neukonfiguration muss der zwischengespeicherte Kontext verworfen werden,
    # damit wieder die konfigurierten Zertifikate gelten.
    IFSFTPHandler.ssl_context = None
    return IFSFTPHandler


def run_server() -> None:
    global _finalize_pool
    settings = get_settings()
    if not settings.ftp_enabled:
        logger.info("FTPS ist deaktiviert (IFS_FTP_ENABLED=false)")
        return
    handler = build_handler()
    workers = max(0, int(settings.ftp_finalize_workers or 0))
    if workers > 0:
        _finalize_pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="ifs-ftp-finalize"
        )
        handler.finalize_pool = _finalize_pool
        handler.finalize_slots = threading.BoundedSemaphore(workers * 2)
        logger.info("FTPS: %s Finalisierungs-Worker aktiv", workers)
    else:
        handler.finalize_pool = None
        handler.finalize_slots = None
    server = ThreadedFTPServer((settings.ftp_host, settings.ftp_port), handler)
    logger.info(
        "FTPS gestartet auf %s:%s (PASV %s, TLS erzwungen)",
        settings.ftp_host,
        settings.ftp_port,
        settings.ftp_passive_ports,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.close_all()
    finally:
        if _finalize_pool is not None:
            _finalize_pool.shutdown(wait=True)
            _finalize_pool = None
        handler.finalize_pool = None
        handler.finalize_slots = None
