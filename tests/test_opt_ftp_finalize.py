"""Tests für die asynchrone FTP-Finalisierung (Kleindatei-Optimierung)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from ifs.ftp.server import IFSFTPHandler


class FakeFS:
    def __init__(self) -> None:
        self.finalized: list[tuple] = []
        self.pending = {"file": ("/file", "/", "file", object())}

    def take_pending(self, name):
        return self.pending.pop(name, None)

    def finalize_taken(self, *args):
        self.finalized.append(args)


def _handler(fs: FakeFS, pool=None) -> IFSFTPHandler:
    handler = IFSFTPHandler.__new__(IFSFTPHandler)
    handler.fs = fs
    handler.finalize_pool = pool
    handler.finalize_slots = threading.BoundedSemaphore(2)
    handler._ifs_finalizer_lock = threading.Lock()
    handler._ifs_finalizers = set()
    handler._ifs_path_futures = {}
    return handler


def test_async_finalize_runs_in_pool():
    fs = FakeFS()
    pool = ThreadPoolExecutor(max_workers=1)
    handler = _handler(fs, pool)
    try:
        handler.on_file_received("file")
        handler._ifs_wait_finalizers()
        assert len(fs.finalized) == 1
        assert fs.finalized[0][0] == "/file"
    finally:
        pool.shutdown(wait=True)


def test_sync_fallback_without_pool():
    fs = FakeFS()
    handler = _handler(fs, None)
    handler.on_file_received("file")
    assert len(fs.finalized) == 1


def test_missing_pending_is_noop():
    fs = FakeFS()
    handler = _handler(fs, None)
    handler.on_file_received("unknown")
    assert fs.finalized == []
