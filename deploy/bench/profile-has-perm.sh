#!/usr/bin/env bash
# Zerlegt IFSAuthorizer.has_perm: SQL-Anzahl, ensure_root, resolve_path, authz.can.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

docker compose exec -T ftp python - "$USER" <<'PY'
import sys
import time

from sqlalchemy import event, select

from ifs.core import authz, namespace
from ifs.db import get_engine, session_scope
from ifs.ftp.server import IFSAuthorizer
from ifs.models import User

user_name = sys.argv[1]
authorizer = IFSAuthorizer()
queries: list[tuple[float, str]] = []
current: dict[str, float] = {}


@event.listens_for(get_engine(), "before_cursor_execute")
def before(conn, cursor, statement, parameters, context, executemany):
    current["start"] = time.perf_counter()


@event.listens_for(get_engine(), "after_cursor_execute")
def after(conn, cursor, statement, parameters, context, executemany):
    queries.append((time.perf_counter() - current["start"], " ".join(statement.split())[:110]))


def bench(label, fn, n=30):
    start = time.perf_counter()
    for index in range(n):
        fn(index)
    elapsed = time.perf_counter() - start
    print(f"{label:34s} {elapsed / n * 1000:7.2f} ms/op  ({n / elapsed:6.1f} ops/s)")


def has_perm(index):
    authorizer.has_perm(user_name, "w", f"/perm-{index}.bin")


def full_session(index):
    with session_scope() as db:
        db.execute(select(User).where(User.username == user_name)).scalars().first()


def root_only(index):
    with session_scope() as db:
        user = db.execute(select(User).where(User.username == user_name)).scalars().first()
        namespace.ensure_root(db, user.id)


def resolve_only(index):
    with session_scope() as db:
        user = db.execute(select(User).where(User.username == user_name)).scalars().first()
        root = namespace.ensure_root(db, user.id)
        namespace.resolve_path(db, f"/perm-{index}.bin", root)


def can_only(index):
    with session_scope() as db:
        user = db.execute(select(User).where(User.username == user_name)).scalars().first()
        root = namespace.ensure_root(db, user.id)
        authz.can(db, user, "w", root)


with session_scope() as db:
    user = db.execute(select(User).where(User.username == user_name)).scalars().first()
    root = namespace.ensure_root(db, user.id)
    print("root owner == user:", root.owner_id == user.id, "type:", root.type)

bench("session + user select", full_session)
bench("… + ensure_root", root_only)
bench("… + resolve_path", resolve_only)
bench("… + authz.can", can_only)
bench("has_perm total", has_perm)

queries.clear()
authorizer.has_perm(user_name, "w", "/perm-0.bin")
print(f"has_perm SQL-Statements: {len(queries)}")
for duration, statement in queries:
    print(f"  {duration * 1000:7.2f} ms  {statement}")

print("PROFILE_HAS_PERM_DONE")
PY
