"""Performance-Regressionstests für die Namespace-Kernpfade (N+1 -> gebündelt).

Gemessen werden SQL-Statements über ``sqlalchemy.event.listen(get_engine(),
"before_cursor_execute", ...)``. Geprüft werden relative Obergrenzen (Query-Zahl
unabhängig von Baumbreite bzw. -tiefe) statt fragiler exakter Zahlen; ergänzend
sicherstellen, dass Papierkorb/Kopie/Tiefe und die Zyklus-Absicherung unverändert
funktionieren.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy import event, update
from sqlalchemy.orm import Session

from ifs.core import namespace
from ifs.db import get_engine
from ifs.models import Entry, User

from .conftest import make_blob, make_user


@contextlib.contextmanager
def query_counter() -> Iterator[list[str]]:
    """Zählt die SQL-Statements, die während des Kontexts ausgeführt werden."""
    engine = get_engine()
    statements: list[str] = []

    def _record(*args) -> None:
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _build_wide_folder(
    db: Session, user: User, parent: Entry, name: str, width: int
) -> tuple[Entry, int, int]:
    """Ordner mit ``width`` Dateien plus einem Unterordner mit einer Datei.

    Liefert ``(Ordner, Dateien im Teilbaum, Bytes im Teilbaum)``.
    """
    folder = namespace.create_folder(db, parent, name, user.id)
    sub = namespace.create_folder(db, folder, "sub", user.id)
    total = 0
    for index in range(width):
        payload = f"{name}-{index:03d}".encode()
        namespace.apply_write(db, folder, f"f{index:03d}.bin", user.id, make_blob(db, payload))
        total += len(payload)
    deep = f"{name}-tief".encode()
    namespace.apply_write(db, sub, "tief.bin", user.id, make_blob(db, deep))
    total += len(deep)
    db.flush()
    return folder, width + 1, total


def _build_wide_folders(
    db: Session, user: User, parent: Entry, name: str, width: int
) -> tuple[Entry, int]:
    """Ordner mit ``width`` Unterordnern (je einer Datei).

    Liefert ``(Ordner, Bytes im Teilbaum)``. Anders als bei reinen Dateien war
    der alte ``_active_subtree_size``-Algorithmus hier pro Ordner N+1.
    """
    folder = namespace.create_folder(db, parent, name, user.id)
    total = 0
    for index in range(width):
        child = namespace.create_folder(db, folder, f"o{index:03d}", user.id)
        payload = f"{name}-{index:03d}".encode()
        namespace.apply_write(db, child, "datei.bin", user.id, make_blob(db, payload))
        total += len(payload)
    db.flush()
    return folder, total


# --- Query-Zähler: Breite/Tiefe dürfen die Query-Zahl nicht treiben ----------


def test_subtree_stats_query_anzahl_unabhaengig_von_breite(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    small, small_files, small_size = _build_wide_folder(db, user, root, "klein", 5)
    big, big_files, big_size = _build_wide_folder(db, user, root, "gross", 60)

    with query_counter() as small_queries:
        stats_small = namespace.subtree_stats(db, small)
    with query_counter() as big_queries:
        stats_big = namespace.subtree_stats(db, big)

    assert stats_small == (small_files, small_size)
    assert stats_big == (big_files, big_size)
    # Breite 60 statt 5: Query-Zahl bleibt (nahezu) konstant; früher fiel eine
    # Child-Query je Knoten an (hier ~63 statt ~3).
    assert len(big_queries) <= len(small_queries) + 1
    assert len(big_queries) <= 5


def test_active_subtree_size_query_anzahl_unabhaengig_von_breite(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    small, small_size = _build_wide_folders(db, user, root, "aktiv-klein", 5)
    big, big_size = _build_wide_folders(db, user, root, "aktiv-gross", 60)

    with query_counter() as small_queries:
        size_small = namespace._active_subtree_size(db, small)
    with query_counter() as big_queries:
        size_big = namespace._active_subtree_size(db, big)

    assert size_small == small_size
    assert size_big == big_size
    # Breite 60 statt 5: weitere Ordner erzeugen keine weiteren Child-Queries.
    assert len(big_queries) <= len(small_queries) + 1
    assert len(big_queries) <= 3
    assert len(big_queries) < 10


def test_path_of_query_anzahl_unabhaengig_von_tiefe(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    depth = 25
    node = root
    for index in range(depth):
        node = namespace.create_folder(db, node, f"ebene{index:02d}", user.id)
    db.flush()

    with query_counter() as queries:
        path = namespace.path_of(db, node)

    assert path == "/" + "/".join(f"ebene{index:02d}" for index in range(depth))
    # Früher eine Lazy-Load-Query je Ebene (~25), jetzt eine CTE-Query.
    assert len(queries) <= 3


def test_soft_delete_query_anzahl_unabhaengig_von_breite(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    small, _files, _size = _build_wide_folder(db, user, root, "loesch-klein", 5)
    big, _files2, _size2 = _build_wide_folder(db, user, root, "loesch-gross", 60)

    with query_counter() as small_queries:
        namespace.soft_delete(db, small, user.id)
    # Offene Outbox-Inserts des ersten Aufrufs vor der zweiten Messung flushen.
    db.flush()
    with query_counter() as big_queries:
        namespace.soft_delete(db, big, user.id)

    assert small.trashed_at is not None
    assert big.trashed_at is not None
    assert namespace.resolve_path(db, "/loesch-gross", root) is None
    assert len(big_queries) <= len(small_queries) + 1
    assert len(big_queries) <= 5


def test_list_trash_query_anzahl_unabhaengig_von_anzahl(db):
    user = make_user(db)
    user_id = user.id
    root = namespace.ensure_root(db, user.id)
    count = 25
    for index in range(count):
        # Je Eintrag ein eigener, aktiver Elternordner: der alte Lazy-Load-Pfad
        # hätte je Eintrag eine Parent-Query ausgelöst.
        parent = namespace.create_folder(db, root, f"ordner{index:02d}", user.id)
        folder = namespace.create_folder(db, parent, f"muelleimer{index:02d}", user.id)
        namespace.apply_write(
            db, folder, "datei.bin", user.id, make_blob(db, f"inhalt-{index}".encode())
        )
        namespace.soft_delete(db, folder, user.id)
    db.flush()
    # Session leeren, damit die aktiven Eltern nicht aus der Identity-Map
    # bedient werden (entspricht dem frischen Request-Kontext).
    db.expunge_all()

    fresh_user = db.get(User, user_id)
    with query_counter() as queries:
        trash = namespace.list_trash(db, fresh_user)

    assert len(trash) == count
    # Früher eine Parent-Lazy-Load-Query je Eintrag (~25), jetzt gebündelt.
    assert len(queries) <= 6
    assert len(queries) < count

    with query_counter() as count_queries:
        assert namespace.trash_count(db, fresh_user) == count
    assert len(count_queries) <= 6
    assert len(count_queries) < count


def test_apply_write_vermeidet_doppelte_namenssuche(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)
    namespace.apply_write(db, folder, "datei.bin", user.id, make_blob(db, b"v1"))
    db.flush()

    with query_counter() as queries:
        entry = namespace.apply_write(db, folder, "datei.bin", user.id, make_blob(db, b"v2"))

    # Genau ein Kandidaten-Lookup (früher zwei: ensure_writable + apply_write).
    lookups = [statement for statement in queries if "lower(entries.name)" in statement.lower()]
    assert len(lookups) == 1
    # Verhalten unverändert: neue Version auf demselben Eintrag.
    assert entry.current_version_id is not None
    assert [version.seq for version in namespace.list_versions(db, entry)][:2] == [2, 1]


# --- Verhalten: Sortierung, Papierkorb/Kopie, Tiefe, Zyklen -----------------


def test_list_children_sortierung_wie_zuvor(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "sortiert", user.id)
    for name in ("b.txt", "A.txt", "c.txt"):
        namespace.apply_write(db, folder, name, user.id, make_blob(db, name.encode()))
    namespace.create_folder(db, folder, "z-ordner", user.id)
    namespace.create_folder(db, folder, "M-ordner", user.id)
    db.flush()

    names = [child.name for child in namespace.list_children(db, folder)]
    # Ordner zuerst, dann case-insensitiv nach Name (wie die frühere Python-Sortierung).
    assert names == ["M-ordner", "z-ordner", "A.txt", "b.txt", "c.txt"]


def test_copy_breit_und_tief_korrekt(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    source, files, size = _build_wide_folder(db, user, root, "quelle", 40)

    clone = namespace.copy(db, source, root, "klon", user.id)

    assert clone.id != source.id
    assert namespace.resolve_path(db, "/klon/sub/tief.bin", root) is not None
    assert namespace.subtree_stats(db, clone) == (files, size)
    source_file = namespace.resolve_path(db, "/quelle/f000.bin", root)
    cloned_file = namespace.resolve_path(db, "/klon/f000.bin", root)
    assert source_file is not None and cloned_file is not None
    assert cloned_file.id != source_file.id


def test_copy_tiefe_kette_korrekt(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    top = namespace.create_folder(db, root, "spitze", user.id)
    node = top
    for index in range(30):
        node = namespace.create_folder(db, node, f"t{index:02d}", user.id)
    namespace.apply_write(db, node, "unten.bin", user.id, make_blob(db, b"unten"))
    db.flush()

    namespace.copy(db, top, root, "spitze-klon", user.id)

    deep_path = "/spitze-klon/" + "/".join(f"t{index:02d}" for index in range(30)) + "/unten.bin"
    assert namespace.resolve_path(db, deep_path, root) is not None


def test_papierkorb_tiefe_kette_restore(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    top = namespace.create_folder(db, root, "tief", user.id)
    node = top
    for index in range(40):
        node = namespace.create_folder(db, node, f"e{index:02d}", user.id)
    namespace.apply_write(db, node, "unten.bin", user.id, make_blob(db, b"unten"))
    db.flush()
    folder_path = "/tief/" + "/".join(f"e{index:02d}" for index in range(40))
    deep_path = folder_path + "/unten.bin"
    assert namespace.path_of(db, node) == folder_path

    namespace.soft_delete(db, top, user.id)
    assert namespace.resolve_path(db, deep_path, root) is None
    assert [entry.id for entry in namespace.list_trash(db, user)] == [top.id]
    assert namespace.trash_count(db, user) == 1

    namespace.restore(db, top, user.id)
    assert namespace.resolve_path(db, deep_path, root) is not None
    assert namespace.path_of(db, node) == folder_path


def test_zyklen_terminieren_ohne_endlosschleife(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    a = namespace.create_folder(db, root, "a", user.id)
    b = namespace.create_folder(db, a, "b", user.id)
    # Zyklus direkt in der DB erzeugen (Defense-in-Depth-Fall).
    db.execute(update(Entry).where(Entry.id == a.id).values(parent_id=b.id))
    db.execute(update(Entry).where(Entry.id == b.id).values(parent_id=a.id))
    db.flush()
    db.expire_all()

    reloaded = db.get(Entry, a.id)
    # path_of bricht exakt wie zuvor beim ersten Wiederholungsknoten ab.
    assert namespace.path_of(db, reloaded) == "/b/a"
    # Teilbaum-Operationen besuchen jeden Knoten genau einmal.
    assert namespace.subtree_stats(db, reloaded) == (0, 0)
    assert namespace._active_subtree_size(db, reloaded) == 0
    # Auch das Trash-Marking terminiert (seen-Menge).
    namespace.soft_delete(db, reloaded, user.id)
    assert reloaded.trashed_at is not None
    assert db.get(Entry, b.id).trashed_at is not None
