"""Optimierungstests für das FTP-Dateisystem.

Prüft, dass LIST/MLSD die stat-Daten einer Auflistung gebündelt laden
(keine N+1-Queries) und dass die gebündelte Auflösung die Rechteprüfung
nicht aufweicht (vererbte Rechte, Root-Operationen, Ablehnungen).
"""

from __future__ import annotations

import stat
from types import SimpleNamespace

import pytest
from pyftpdlib.exceptions import FilesystemError
from sqlalchemy import event

from ifs.core import authz, namespace
from ifs.db import get_engine, session_scope
from ifs.errors import PermissionDenied
from ifs.ftp.fs import IFSFilesystem
from ifs.models import ACL, Entry, PrincipalType, User

from .conftest import make_blob, make_user


def _fs_for(username: str) -> IFSFilesystem:
    """IFSFilesystem ohne asyncore-Initialisierung (Muster aus test_review5_ftp)."""
    fs = IFSFilesystem.__new__(IFSFilesystem)
    fs.cmd_channel = SimpleNamespace(
        username=username,
        use_gmt_times=False,
        encoding="utf-8",
        unicode_errors="strict",
    )
    fs._pending = {}
    return fs


def _make_tree(username: str, folder: str, count: int) -> None:
    """Legt einen Ordner mit je ``count`` Unterordnern und Dateien an."""
    from sqlalchemy import select

    with session_scope() as db:
        user = db.execute(select(User).where(User.username == username)).scalars().first()
        if user is None:
            user = make_user(db, username)
        root = namespace.ensure_root(db, user.id)
        target = namespace.create_folder(db, root, folder, user.id)
        for index in range(count):
            namespace.create_folder(db, target, f"dir{index:02d}", user.id)
            namespace.apply_write(
                db,
                target,
                f"file{index:02d}.txt",
                user.id,
                make_blob(db, f"{folder}-{index}".encode()),
            )


def _count_statements(fn):
    """Führt ``fn`` aus und zählt dabei die SQL-Statements der Test-Engine."""
    engine = get_engine()
    counter = {"count": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        counter["count"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["count"], result


# --- (a) Query-Zähler: LIST skaliert nicht linear mit der Eintragszahl --------


def test_list_query_count_is_constant_in_entries():
    _make_tree("opt-list", "small", 3)
    _make_tree("opt-list", "large", 12)
    fs = _fs_for("opt-list")

    def list_workflow(folder: str):
        # Entspricht ftp_LIST: isdir() -> listdir() -> format_list().
        assert fs.isdir(f"/{folder}")
        listing = fs.listdir(f"/{folder}")
        data = b"".join(fs.format_list(f"/{folder}", listing))
        return listing, data

    small_count, (small_listing, small_data) = _count_statements(
        lambda: list_workflow("small")
    )
    large_count, (large_listing, large_data) = _count_statements(
        lambda: list_workflow("large")
    )

    assert len(small_listing) == 6  # 3 Ordner + 3 Dateien
    assert len(large_listing) == 24  # 12 Ordner + 12 Dateien
    assert b"file00.txt" in small_data and b"dir00" in small_data
    assert b"file00.txt" in large_data and b"dir11" in large_data
    # Die Zahl der Statements darf nicht mit der Eintragszahl wachsen.
    assert large_count == small_count
    # Großzügige Obergrenze als zweite Absicherung (aktuell 20 Statements).
    assert large_count < 40


def test_mlsd_and_mlst_use_bundled_stats():
    _make_tree("opt-mlsd", "docs", 4)
    fs = _fs_for("opt-mlsd")
    perms = "elradfmwMT"
    facts = ["type", "perm", "size", "modify"]

    listing = fs.listdir("/docs")
    count, data = _count_statements(
        lambda: b"".join(fs.format_mlsx("/docs", listing, perms, facts))
    )
    assert b"file00.txt" in data and b"type=file;" in data
    # Auch MLSD bleibt konstant gegenüber der Eintragszahl.
    assert count < 40

    # MLST einer einzelnen Datei (kein Treffer aus listdir) nutzt den Fallback
    # mit vollständiger Auflösung und liefert dieselben Fakten.
    single = b"".join(fs.format_mlsx("/docs", ["file01.txt"], perms, facts, ignore_err=False))
    assert b"file01.txt" in single


def test_stat_cache_is_scoped_to_listing():
    _make_tree("opt-cache", "docs", 2)
    fs = _fs_for("opt-cache")

    generator = fs.format_list("/docs", fs.listdir("/docs"))
    next(generator)
    assert fs._stat_cache  # innerhalb der Auflistung gefüllt
    generator.close()
    assert fs._stat_cache == {}  # danach wieder zurückgesetzt

    # Außerhalb der Auflistung läuft die reguläre (autoritätsgeprüfte) Auflösung.
    assert fs.stat("/docs/file00.txt").st_size == len(b"docs-0")


# --- (b) Verhalten: vererbte Rechte, Root-Operationen, Ablehnungen ------------


def _tree_with_acl(viewer_username: str, perms: int) -> dict:
    """Fremder Baum mit ACL für den Viewer; liefert Pfad -> Entry-ID."""
    with session_scope() as db:
        owner = make_user(db, "opt-acl-owner")
        viewer = make_user(db, viewer_username)
        root = namespace.ensure_root(db, owner.id)
        team = namespace.create_folder(db, root, "team", owner.id)
        file_entry = namespace.apply_write(
            db, team, "report.txt", owner.id, make_blob(db, b"inhalt")
        )
        db.add(
            ACL(
                entry_id=team.id,
                principal_type=PrincipalType.user,
                principal_id=viewer.id,
                perms=perms,
                inherited=True,
            )
        )
        db.flush()
        mapping = {"/team": team.id, "/team/report.txt": file_entry.id}
    return mapping


def _fs_with_mapping(username: str, mapping: dict) -> IFSFilesystem:
    """IFSFilesystem, dessen Auflösung auf einen fremden Baum zeigt (wie F2-Test)."""

    def _resolve(db, path):
        entry_id = mapping.get(path)
        return db.get(Entry, entry_id) if entry_id else None

    fs = _fs_for(username)
    fs._resolve = _resolve
    return fs


def test_inherited_folder_rights_apply_to_children():
    mapping = _tree_with_acl("opt-viewer", authz.READ)
    fs = _fs_with_mapping("opt-viewer", mapping)

    # Ordner-Leserecht und vererbte Rechte auf der Datei darin.
    assert fs.isdir("/team") is True
    assert fs.listdir("/team") == ["report.txt"]
    assert fs.stat("/team/report.txt").st_size == len(b"inhalt")
    assert fs.getsize("/team/report.txt") == len(b"inhalt")

    # Auflistung formatiert die Kinder aus dem gebündelten Cache.
    data = b"".join(fs.format_list("/team", fs.listdir("/team")))
    assert b"report.txt" in data


def test_write_requires_write_permission_even_with_inherited_read():
    mapping = _tree_with_acl("opt-reader", authz.READ)
    fs = _fs_with_mapping("opt-reader", mapping)

    # Lesen der Datei bleibt erlaubt, Schreiben im Ordner nicht.
    assert fs.stat("/team/report.txt").st_size == len(b"inhalt")
    with pytest.raises(PermissionDenied):
        fs._open_writer("/team/neu.txt", "w")
    assert fs._pending == {}


def test_rejections_without_rights():
    # ACL für einen anderen Nutzer; der getestete Nutzer bleibt ohne Rechte.
    mapping = _tree_with_acl("opt-granted", authz.READ)
    with session_scope() as db:
        make_user(db, "opt-outsider")
    fs = _fs_with_mapping("opt-outsider", mapping)

    assert fs.isdir("/team") is False
    assert fs.exists("/team") is False
    with pytest.raises(FilesystemError):
        fs.listdir("/team")
    with pytest.raises(FilesystemError):
        fs.stat("/team/report.txt")
    with pytest.raises(FilesystemError):
        fs.chdir("/team")
    # Auch die gebündelte MLST-Formatierung verrät keine Metadaten.
    with pytest.raises(FilesystemError):
        list(
            fs.format_mlsx(
                "/team", ["report.txt"], "elradfmwMT", ["type", "size"], ignore_err=False
            )
        )


def test_root_operations_for_owner():
    with session_scope() as db:
        user = make_user(db, "opt-root")
        root = namespace.ensure_root(db, user.id)
        namespace.create_folder(db, root, "docs", user.id)

    fs = _fs_for("opt-root")
    assert fs.isdir("/") is True
    assert fs.listdir("/") == ["docs"]
    assert stat.S_ISDIR(fs.stat("/").st_mode)
    fs.chdir("/")
    assert fs.cwd == "/"
    # Formatieren der Wurzel bleibt mit der gebündelten Auflösung möglich.
    assert b"docs" in b"".join(fs.format_list("/", fs.listdir("/")))

    # Die Wurzel selbst darf nicht gelöscht oder verschoben werden.
    with pytest.raises(FilesystemError):
        fs.rename("/", "/neu")

    with session_scope() as db:
        empty_user = make_user(db, "opt-root-empty")
        namespace.ensure_root(db, empty_user.id)
    fs_empty = _fs_for("opt-root-empty")
    with pytest.raises(FilesystemError):
        fs_empty.rmdir("/")


def test_rmdir_non_empty_trashes_subtree():
    """``RMD`` verschiebt den Ordner samt Inhalt in den Papierkorb (wie HTTP)."""
    from sqlalchemy import select

    with session_scope() as db:
        user = make_user(db, "opt-rmdir")
        user_id = user.id
        root = namespace.ensure_root(db, user.id)
        docs = namespace.create_folder(db, root, "docs", user.id)
        sub = namespace.create_folder(db, docs, "sub", user.id)
        datei = namespace.apply_write(db, docs, "datei.txt", user.id, make_blob(db, b"inhalt"))
        tief = namespace.apply_write(db, sub, "tief.txt", user.id, make_blob(db, b"tief"))
        subtree = {docs.id, sub.id, datei.id, tief.id}

    fs = _fs_for("opt-rmdir")
    # Früher scheiterte das mit „Verzeichnis nicht leer“.
    fs.rmdir("/docs")

    with session_scope() as db:
        user = db.get(User, user_id)
        root = namespace.ensure_root(db, user.id)
        assert namespace.resolve_path(db, "/docs", root) is None
        trashed = set(
            db.execute(select(Entry.id).where(Entry.trashed_at.isnot(None))).scalars().all()
        )
        assert subtree <= trashed
        # Nur der oberste Knoten erscheint als Papierkorb-Eintrag.
        assert [entry.name for entry in namespace.list_trash(db, user)] == ["docs"]
