"""Tests für Namespace-Operationen (Metadaten-only)."""

from __future__ import annotations

import pytest

from ifs.core import namespace
from ifs.errors import Conflict
from ifs.models import EntryType

from .conftest import make_blob, make_user


def test_create_and_resolve_path(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    docs = namespace.create_folder(db, root, "docs", user.id)
    child = namespace.create_folder(db, docs, "2026", user.id)

    assert namespace.path_of(db, child) == "/docs/2026"
    assert namespace.resolve_path(db, "/docs/2026", root).id == child.id
    assert namespace.resolve_path(db, "/docs/missing", root) is None


def test_apply_write_creates_and_versions(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    folder = namespace.create_folder(db, root, "docs", user.id)

    blob1 = make_blob(db, b"v1")
    entry = namespace.apply_write(db, folder, "report.txt", user.id, blob1, "text/plain")
    assert entry.type == EntryType.file
    assert entry.size == 2
    first_version = entry.current_version_id

    blob2 = make_blob(db, b"version-2")
    same = namespace.apply_write(db, folder, "report.txt", user.id, blob2)
    assert same.id == entry.id
    assert same.current_version_id != first_version
    assert same.size == len(b"version-2")


def test_name_conflict(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    namespace.create_folder(db, root, "docs", user.id)

    with pytest.raises(Conflict):
        namespace.create_folder(db, root, "DOCS", user.id)


def test_rename_move_delete_and_copy(db):
    user = make_user(db)
    root = namespace.ensure_root(db, user.id)
    a = namespace.create_folder(db, root, "a", user.id)
    b = namespace.create_folder(db, root, "b", user.id)
    blob = make_blob(db, b"data")
    file_entry = namespace.apply_write(db, a, "file.bin", user.id, blob)

    namespace.rename(db, file_entry, "renamed.bin")
    assert file_entry.name == "renamed.bin"

    namespace.move(db, file_entry, b)
    assert file_entry.parent_id == b.id
    assert namespace.path_of(db, file_entry) == "/b/renamed.bin"

    clone = namespace.copy(db, file_entry, a, "copy.bin", user.id)
    assert clone.id != file_entry.id
    assert clone.current_version_id is not None
    assert namespace.resolve_path(db, "/a/copy.bin", root).id == clone.id

    namespace.soft_delete(db, file_entry)
    assert namespace.resolve_path(db, "/b/renamed.bin", root) is None
    assert namespace.path_of(db, b) == "/b"
