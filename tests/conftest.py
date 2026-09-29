"""Testkonfiguration: SQLite statt PostgreSQL, temporäres Spool-Verzeichnis."""

from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ifs-test-")
os.environ["IFS_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["IFS_UPLOAD_SPOOL_DIR"] = os.path.join(_tmp, "spool")
os.environ["IFS_AUTO_CREATE_SCHEMA"] = "true"
os.environ["IFS_JWT_SECRET"] = "test-secret-with-at-least-32-characters-1234567890"
os.environ["IFS_FTP_ENABLED"] = "false"
os.environ["IFS_ADMIN_USERNAME"] = "admin"
os.environ["IFS_ADMIN_PASSWORD"] = "admin"

import pytest

from ifs.config import get_settings
from ifs.db import Base, create_all, get_engine, reset_engine
from ifs.models import Blob, BlobStatus, User
from ifs.security import hash_password


@pytest.fixture(autouse=True)
def _fresh_db():
    get_settings.cache_clear()
    reset_engine()
    create_all()
    from ifs.core import ratelimit

    ratelimit.reset()
    from ifs.core import archive_jobs

    archive_jobs.reset()
    yield
    Base.metadata.drop_all(bind=get_engine())
    reset_engine()


@pytest.fixture
def db():
    from ifs.db import session_scope

    with session_scope() as session:
        yield session


def make_user(db, username: str = "alice", is_admin: bool = False) -> User:
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    return user


def make_blob(db, content: bytes = b"hello") -> Blob:
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    blob = Blob(
        storage_key=f"cas/{sha[:2]}/{sha}",
        sha256=sha,
        size=len(content),
        status=BlobStatus.ready,
    )
    db.add(blob)
    db.flush()
    return blob
