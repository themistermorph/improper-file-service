#!/usr/bin/env bash
# DB-Latenz im FTP-Container: Commit vs. Rollback vs. Pre-Ping.
set -uo pipefail
cd /home/ifs/ifs

docker compose exec -T ftp python - <<'PY'
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ifs.config import get_settings
from ifs.db import get_session_factory, session_scope

N = 50
settings = get_settings()


def bench(label, fn):
    start = time.perf_counter()
    for index in range(N):
        fn(index)
    elapsed = time.perf_counter() - start
    print(f"{label:34s} {elapsed / N * 1000:7.2f} ms/op  ({N / elapsed:6.1f} ops/s)")


def commit_select(index):
    with session_scope() as db:
        db.execute(text("SELECT 1"))


def rollback_select(index):
    session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.rollback()
        session.close()


def close_select(index):
    session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()


reused = get_session_factory()()


def reused_query(index):
    reused.execute(text("SELECT 1"))


bench("session + SELECT + commit", commit_select)
bench("session + SELECT + rollback", rollback_select)
bench("session + SELECT + close", close_select)
bench("reused session SELECT", reused_query)
reused.rollback()
reused.close()

engine = create_engine(settings.database_url, pool_pre_ping=False, future=True)
factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def noping_select(index):
    with factory() as session:
        session.execute(text("SELECT 1"))
        session.commit()


bench("ohne pre_ping + SELECT + commit", noping_select)
engine.dispose()
print("PROFILE_DB_FTP_DONE")
PY
