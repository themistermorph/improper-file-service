"""Kommandozeile zum Starten der Rollen und Initialisieren der Umgebung."""

from __future__ import annotations

import argparse
import logging
import re
import sys

from .config import get_settings


class _TokenRedactionFilter(logging.Filter):
    """Entfernt Tokens aus Log-Pfaden (z. B. /api/shares/<token>, /api/preview/<token>)."""

    # Auch mehrsegmentige Pfade wie /api/archive/jobs/<token> erfassen.
    _PATTERN = re.compile(
        r"(/(?:api/)?(?:shares|preview|archive|download)/(?:jobs/)?)[A-Za-z0-9._\-]+"
    )
    _PASSWORD = re.compile(r"(password=)[^&\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            message = args[2]
            # Vorab-Check: Ohne diese Wörter können die Muster nicht greifen;
            # spart zwei Regex-Scans pro Access-Log-Zeile.
            if (
                "shares" in message
                or "preview" in message
                or "archive" in message
                or "/download/" in message
                or "password=" in message
            ):
                new_args = list(args)
                redacted = self._PATTERN.sub(r"\1<redacted>", message)
                new_args[2] = self._PASSWORD.sub(r"\1<redacted>", redacted)
                record.args = tuple(new_args)
        return True


def _configure_logging(role: str | None = None) -> None:
    settings = get_settings()
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
    )
    # Access-Logs: Tokens in URLs unkenntlich machen.
    logging.getLogger("uvicorn.access").addFilter(_TokenRedactionFilter())

    if not role:
        return
    from logging.handlers import RotatingFileHandler

    from .core import logs

    root = logging.getLogger()
    # Bei wiederholter Konfiguration (z. B. Tests) keine Doppel-Handler.
    for handler in list(root.handlers):
        if getattr(handler, "_ifs_file_handler", False):
            root.removeHandler(handler)
            handler.close()
    try:
        logs.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            logs.log_path(role),
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
    except OSError:
        # Lokale Entwicklung ohne /var/log/ifs: nur Konsole.
        return
    handler.setFormatter(logging.Formatter(log_format))
    handler._ifs_file_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def cmd_init_db(_: argparse.Namespace) -> int:
    from .core import content
    from .db import create_all

    create_all()
    print("Schema angelegt.")
    try:
        content.ensure_bucket()
        print("S3-Bucket geprüft/angelegt.")
    except Exception as exc:
        print(f"Warnung: Bucket konnte nicht geprüft werden: {exc}")
    return 0


def cmd_seed_admin(args: argparse.Namespace) -> int:
    from .main import seed_initial_data

    seed_initial_data(username=args.username, password=args.password)
    print("Initialdaten geprüft.")
    return 0


def cmd_run_api(args: argparse.Namespace) -> int:
    import uvicorn

    # Unsere Logging-Konfiguration (inkl. Datei-Handler) beibehalten; Uvicorn
    # würde sonst die Root-Handler überschreiben.
    uvicorn.run(
        "ifs.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        log_config=None,
    )
    return 0


def cmd_run_ftp(_: argparse.Namespace) -> int:
    from .ftp.server import run_server

    run_server()
    return 0


def cmd_run_worker(_: argparse.Namespace) -> int:
    from .worker import run_worker

    run_worker()
    return 0


def cmd_cleanup_missing_blobs(args: argparse.Namespace) -> int:
    """Entfernt Datei-Einträge, deren Blob im Objektspeicher fehlt."""
    from sqlalchemy import select

    from .core import content
    from .db import session_scope
    from .models import Blob, Entry, EntryType, Version

    removed_files = 0
    removed_folders = 0
    with session_scope() as db:
        files = db.execute(
            select(Entry).where(Entry.type == EntryType.file)
        ).scalars().all()
        for entry in files:
            version = (
                db.get(Version, entry.current_version_id)
                if entry.current_version_id
                else None
            )
            blob = db.get(Blob, version.blob_id) if version else None
            if blob is None or not content.object_exists(blob.storage_key):
                db.delete(entry)
                removed_files += 1
        db.flush()

        if args.remove_empty_folders:
            changed = True
            while changed:
                changed = False
                folders = db.execute(
                    select(Entry).where(
                        Entry.type == EntryType.folder,
                        Entry.parent_id.isnot(None),
                        Entry.trashed_at.is_(None),
                    )
                ).scalars().all()
                for folder in folders:
                    child = db.execute(
                        select(Entry.id).where(Entry.parent_id == folder.id).limit(1)
                    ).first()
                    if child is None:
                        db.delete(folder)
                        removed_folders += 1
                        changed = True
                db.flush()

    print(
        f"Bereinigung abgeschlossen: {removed_files} Dateien ohne Blob entfernt"
        + (f", {removed_folders} leere Ordner entfernt" if args.remove_empty_folders else "")
        + "."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ifs", description="IFS – webbasiertes Dateisystem")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Datenbankschema anlegen und Bucket prüfen").set_defaults(
        func=cmd_init_db
    )
    seed = sub.add_parser("seed-admin", help="Initialen Admin/Benutzer anlegen")
    seed.add_argument("--username", default=None)
    seed.add_argument("--password", default=None)
    seed.set_defaults(func=cmd_seed_admin)

    api = sub.add_parser("run-api", help="HTTP-API und Web-UI starten")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--log-level", default="info")
    api.set_defaults(func=cmd_run_api, log_role="api")

    sub.add_parser("run-ftp", help="FTPS-Gateway starten").set_defaults(
        func=cmd_run_ftp, log_role="ftp"
    )
    sub.add_parser("run-worker", help="Hintergrund-Worker starten").set_defaults(
        func=cmd_run_worker, log_role="worker"
    )
    cleanup = sub.add_parser(
        "cleanup-missing-blobs",
        help="Datei-Einträge ohne vorhandenen Blob entfernen (nach S3-Umstellung)",
    )
    cleanup.add_argument(
        "--remove-empty-folders",
        action="store_true",
        help="zusätzlich leere, nicht gelöschte Ordner entfernen",
    )
    cleanup.set_defaults(func=cmd_cleanup_missing_blobs)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "log_role", None))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
