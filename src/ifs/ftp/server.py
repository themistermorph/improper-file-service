"""FTPS-Server: TLS-geschützt, Authentifizierung und Rechte über den IFS-Kern."""

from __future__ import annotations

import logging
import posixpath

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.exceptions import AuthenticationFailed
from pyftpdlib.handlers import TLS_FTPHandler
from pyftpdlib.servers import ThreadedFTPServer
from sqlalchemy import select

from ..config import get_settings
from ..core import authz, namespace, ratelimit
from ..db import session_scope
from ..models import User
from ..security import verify_password_safe
from .fs import IFSFilesystem, _norm

logger = logging.getLogger("ifs.ftp")

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


class IFSAuthorizer(DummyAuthorizer):
    """Bindet FTP-Logins und Berechtigungen an den IFS-Kern."""

    def validate_authentication(self, username, password, handler):
        ip = getattr(handler, "remote_ip", None)
        account_key, ip_key = ratelimit.keys(ip, username)
        retry = ratelimit.retry_after(account_key, ip_key)
        if retry:
            raise AuthenticationFailed(
                f"Zu viele Fehlversuche – bitte in {retry}s erneut versuchen."
            )

        with session_scope() as db:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalars().first()
            password_hash = user.password_hash if user is not None else None
            active = bool(user and user.is_active)

        # Auch bei unbekanntem Nutzer wird ein Hash geprüft (kein Timing-Leak).
        valid = verify_password_safe(password_hash, password)
        if not active or not valid:
            ratelimit.record_failure(account_key, ip_key)
            raise AuthenticationFailed("Benutzername oder Passwort falsch")

        ratelimit.record_success(account_key, ip_key)

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
        with session_scope() as db:
            user = db.execute(
                select(User).where(User.username == username)
            ).scalars().first()
            if user is None or not user.is_active:
                return False
            entry = _nearest_entry(db, user, path)
            return authz.can(db, user, action, entry)


class IFSFTPHandler(TLS_FTPHandler):
    abstracted_fs = IFSFilesystem
    tls_control_required = True
    tls_data_required = True

    def on_file_received(self, file):
        try:
            self.fs.finalize_received(file)
        except Exception:
            logger.exception("Finalisieren des FTP-Uploads fehlgeschlagen: %s", file)
            super().on_file_received(file)

    def on_incomplete_file_received(self, file):
        # Abgebrochene Uploads (ABOR/Disconnect) hinterlassen sonst einen
        # verwaisten Spool; Fehler dürfen die Verbindung nicht sprengen.
        try:
            self.fs.discard_pending(file)
        except Exception:
            logger.exception("Verwerfen des abgebrochenen FTP-Uploads fehlgeschlagen: %s", file)

    def close(self):
        fs = getattr(self, "fs", None)
        try:
            super().close()
        finally:
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
    return IFSFTPHandler


def run_server() -> None:
    settings = get_settings()
    if not settings.ftp_enabled:
        logger.info("FTPS ist deaktiviert (IFS_FTP_ENABLED=false)")
        return
    handler = build_handler()
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
