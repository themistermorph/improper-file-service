"""Testkonfiguration: SQLite statt PostgreSQL, temporäres Spool-Verzeichnis.

Performance: Das Schema wird einmal pro Worker-Prozess angelegt und zwischen den
Tests nur geleert (statt pro Test ``create_all``/``drop_all``). Argon2 läuft mit
bewusst schwachen Parametern und der S3-Bucket-Check des App-Starts ist im Test
deaktiviert – beides ist reine Testbeschleunigung ohne Einfluss auf die
Produktionskonfiguration.
"""

from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ifs-test-")
os.environ["IFS_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["IFS_UPLOAD_SPOOL_DIR"] = os.path.join(_tmp, "spool")
# Schema legt die Session-Fixture unten an; der App-Lifespan muss das nicht je
# TestClient erneut prüfen.
os.environ["IFS_AUTO_CREATE_SCHEMA"] = "false"
os.environ["IFS_JWT_SECRET"] = "test-secret-with-at-least-32-characters-1234567890"
os.environ["IFS_FTP_ENABLED"] = "false"
os.environ["IFS_ADMIN_USERNAME"] = "admin"
os.environ["IFS_ADMIN_PASSWORD"] = "admin"

import pytest
from argon2 import PasswordHasher
from sqlalchemy import event

from ifs import security
from ifs.config import get_settings
from ifs.core import content as content_module
from ifs.db import Base, create_all, get_engine, reset_engine
from ifs.models import Blob, BlobStatus, User
from ifs.security import hash_password

# Argon2 ist im Betrieb bewusst langsam; in Tests würde das pro Hash ~0,1 s
# kosten. Für die Korrektheit genügen minimale Parameter.
security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

# Kein echter S3-Zugriff beim Start jedes TestClient-Lifespans.
content_module.ensure_bucket = lambda: None


def _fast_sqlite(dbapi_connection, _connection_record) -> None:
    """SQLite im Test ohne fsync pro Commit betreiben (Temp-DB, kein Datenverlust-Risiko)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=MEMORY")
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.close()


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Legt das Schema einmal pro Worker-Prozess an und räumt am Ende auf."""
    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    event.listen(engine, "connect", _fast_sqlite)
    create_all()
    yield
    Base.metadata.drop_all(bind=get_engine())
    reset_engine()


@pytest.fixture(autouse=True)
def _fresh_db(_schema):
    get_settings.cache_clear()
    from ifs.core import ratelimit

    ratelimit.reset()
    from ifs.core import archive_jobs

    archive_jobs.reset()
    # Alle Tabellen in Abhängigkeitsreihenfolge leeren (schneller als DDL).
    with get_engine().begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


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
