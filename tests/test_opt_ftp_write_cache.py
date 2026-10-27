"""Tests für die zusammengelegte STOR/APPE-Autorisierung (Kontext-Cache)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyftpdlib.exceptions import FilesystemError

from ifs.core import namespace
from ifs.db import session_scope
from ifs.ftp import context as ftp_context
from ifs.ftp import fs as fs_module
from ifs.ftp.fs import IFSFilesystem
from ifs.ftp.server import IFSAuthorizer

from .conftest import make_user


def _fs(username: str) -> IFSFilesystem:
    fs = IFSFilesystem.__new__(IFSFilesystem)
    fs.cmd_channel = SimpleNamespace(
        username=username,
        remote_ip="127.0.0.1",
        use_gmt_times=False,
        encoding="utf-8",
        unicode_errors="strict",
    )
    fs._pending = {}
    return fs


def _root_for(username: str) -> tuple:
    with session_scope() as db:
        user = make_user(db, username)
        root = namespace.ensure_root(db, user.id)
        return user.id, root.id


def test_has_perm_prepares_write_target():
    user_id, root_id = _root_for("cache-target")
    ftp_context.clear()
    ftp_context.ctx.cmd = "STOR"
    ftp_context.ctx.fs_path = "/cache-a.txt"
    try:
        assert IFSAuthorizer().has_perm("cache-target", "w", "/cache-a.txt") is True
        target = ftp_context.ctx.target
        assert target is not None
        assert target.name == "cache-a.txt"
        assert target.parent_id == root_id
        assert target.user_id == user_id
        assert target.existing_id is None
    finally:
        ftp_context.clear()


def test_open_writer_uses_cached_target_without_db(monkeypatch):
    user_id, root_id = _root_for("cache-writer")
    fs = _fs("cache-writer")
    ftp_context.clear()
    ftp_context.ctx.target = ftp_context.WriteTarget(
        path="/cache-b.txt",
        user_id=user_id,
        parent_id=root_id,
        parent_path="/",
        name="cache-b.txt",
    )

    def _boom():
        raise AssertionError("read_scope darf im Cache-Pfad nicht aufgerufen werden")

    monkeypatch.setattr(fs_module, "read_scope", _boom)
    try:
        writer = fs._open_writer("/cache-b.txt", "wb")
        writer.write(b"abc")
        writer.close()
        assert "/cache-b.txt" in fs._pending
        assert fs._pending["/cache-b.txt"][1] == "cache-b.txt"
    finally:
        fs.cleanup()
        ftp_context.clear()


def test_cached_folder_target_is_rejected():
    username = "cache-folder"
    with session_scope() as db:
        user = make_user(db, username)
        root = namespace.ensure_root(db, user.id)
        folder = namespace.create_folder(db, root, "cache-folder", user.id)
        user_id, root_id, folder_id = user.id, root.id, folder.id

    fs = _fs(username)
    ftp_context.clear()
    ftp_context.ctx.target = ftp_context.WriteTarget(
        path="/cache-folder",
        user_id=user_id,
        parent_id=root_id,
        parent_path="/",
        name="cache-folder",
        existing_id=folder_id,
        existing_is_folder=True,
    )
    try:
        with pytest.raises(FilesystemError):
            fs._open_writer("/cache-folder", "wb")
    finally:
        ftp_context.clear()
