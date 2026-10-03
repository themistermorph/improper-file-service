"""Performance-Indizes: models ↔ Migration 0007 und unverändertes Kernverhalten.

Prüft (a) die per ``create_all`` erzeugten Indizes, (b) Idempotenz und Konsistenz
der statischen Migration und (c) unverändertes Quota-/Outbox-Verhalten.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from ifs.core import events, quota
from ifs.db import Base, get_engine
from ifs.errors import QuotaExceeded
from ifs.models import Entry, EntryType, Outbox
from ifs.utils import utcnow

from .conftest import make_user

MIGRATION = (
    Path(__file__).resolve().parent.parent / "migrations" / "sql" / "0007_performance_indexes.sql"
)

# Indexname -> (Tabelle, Spalten in Reihenfolge) – Soll-Zustand aus models.py.
NEUE_INDIZES: dict[str, tuple[str, tuple[str, ...]]] = {
    "ix_entries_parent_trashed": ("entries", ("parent_id", "trashed_at")),
    "ix_entries_owner_trashed": ("entries", ("owner_id", "trashed_at")),
    "ix_entries_trashed_at": ("entries", ("trashed_at",)),
    "ix_versions_entry_seq": ("versions", ("entry_id", "seq")),
    "ix_acl_entry_principal": ("acl", ("entry_id", "principal_type", "principal_id")),
    "ix_role_assignments_principal_entry": (
        "role_assignments",
        ("principal_type", "principal_id", "entry_id"),
    ),
    "ix_audit_log_ts": ("audit_log", ("ts",)),
    "ix_outbox_published_created": ("outbox", ("published_at", "created_at")),
    "ix_upload_sessions_status_updated": ("upload_sessions", ("status", "updated_at")),
    "ix_shares_created_by_created": ("shares", ("created_by", "created_at")),
}

# Einzelindizes, die von den neuen zusammengesetzten Indizes ersetzt werden.
ERSETZTE_INDIZES = {
    "ix_entries_parent_id",
    "ix_entries_owner_id",
    "ix_versions_entry_id",
    "ix_acl_entry_id",
    "ix_outbox_published_at",
}

TABELLEN = sorted({table for table, _ in NEUE_INDIZES.values()})


@pytest.mark.parametrize("name", sorted(NEUE_INDIZES))
def test_create_all_erzeugt_neue_indizes(name: str) -> None:
    table, spalten = NEUE_INDIZES[name]
    indizes = {
        index["name"]: tuple(index["column_names"])
        for index in inspect(get_engine()).get_indexes(table)
    }
    assert indizes.get(name) == spalten


@pytest.mark.parametrize("name", sorted(ERSETZTE_INDIZES))
def test_ersetzte_einzelindizes_sind_entfernt(name: str) -> None:
    for table in TABELLEN:
        namen = {index["name"] for index in inspect(get_engine()).get_indexes(table)}
        assert name not in namen


def _migrationstext() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _sql_indizes(text: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    pattern = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(?P<name>\w+)\s+"
        r"ON\s+(?P<table>\w+)\s*\((?P<columns>[^)]*)\)",
        re.IGNORECASE,
    )
    result: dict[str, tuple[str, tuple[str, ...]]] = {}
    for match in pattern.finditer(text):
        columns = tuple(column.strip().strip('"') for column in match.group("columns").split(","))
        result[match.group("name")] = (match.group("table").lower(), columns)
    return result


def test_migration_ist_idempotent() -> None:
    text = _migrationstext()
    creates = re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", text, re.IGNORECASE)
    creates_geschuetzt = re.findall(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\b", text, re.IGNORECASE
    )
    assert len(creates) == len(creates_geschuetzt)
    assert re.findall(r"DROP\s+INDEX\b(?!\s+IF\s+EXISTS)", text, re.IGNORECASE) == []
    drops = re.findall(r"DROP\s+INDEX\s+IF\s+EXISTS\s+(\w+)", text, re.IGNORECASE)
    assert len(drops) == len(ERSETZTE_INDIZES)


def test_migration_und_models_sind_konsistent() -> None:
    assert _sql_indizes(_migrationstext()) == NEUE_INDIZES

    metadata_indizes: dict[str, tuple[str, tuple[str, ...]]] = {}
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            metadata_indizes[index.name] = (table.name, tuple(c.name for c in index.columns))

    for name, ziel in NEUE_INDIZES.items():
        assert metadata_indizes.get(name) == ziel, f"models.py weicht ab: {name}"

    # Die Migration entfernt genau die Indizes, die models.py nicht mehr definiert.
    geloescht = re.findall(r"DROP\s+INDEX\s+IF\s+EXISTS\s+(\w+)", _migrationstext(), re.IGNORECASE)
    assert set(geloescht) == ERSETZTE_INDIZES
    for name in geloescht:
        assert name not in metadata_indizes, f"models.py definiert weiterhin: {name}"


def _entry(
    db,
    *,
    owner,
    name: str,
    parent_id=None,
    size: int = 0,
    entry_type: EntryType = EntryType.file,
    trashed: bool = False,
) -> Entry:
    entry = Entry(
        name=name, type=entry_type, owner_id=owner.id, parent_id=parent_id, size=size
    )
    if trashed:
        entry.trashed_at = utcnow()
    db.add(entry)
    db.flush()
    return entry


def test_quota_summe_und_grenzen_unveraendert(db, monkeypatch) -> None:
    user = make_user(db, "quota-user")
    other = make_user(db, "other-user")
    root = _entry(db, owner=user, name="/", entry_type=EntryType.folder)
    _entry(db, owner=user, name="aktiv.txt", parent_id=root.id, size=100)
    _entry(db, owner=user, name="papierkorb.txt", parent_id=root.id, size=50, trashed=True)
    _entry(db, owner=user, name="ordner", parent_id=root.id, size=999, entry_type=EntryType.folder)
    _entry(db, owner=other, name="fremd.txt", size=7)

    assert quota.usage_bytes(db, user.id) == 100

    monkeypatch.setattr(quota, "limit_bytes", lambda: 100)
    quota.enforce(db, user.id, 0)  # exakt am Limit: erlaubt
    with pytest.raises(QuotaExceeded) as exc:
        quota.enforce(db, user.id, 1)
    assert str(exc.value) == "Speicher-Quota überschritten"

    monkeypatch.setattr(quota, "limit_bytes", lambda: 0)
    quota.enforce(db, user.id, 10**9)  # 0 = unbegrenzt


def test_emit_flusht_nicht_und_behaelt_semantik(db) -> None:
    event = events.emit(db, "entry.created")
    assert event.event_type == "entry.created"
    assert event.payload == {}
    assert event.published_at is None

    zweites = events.emit(db, "entry.written", {"size": 1})
    assert zweites.payload == {"size": 1}

    # autoflush=False: ohne expliziten Flush darf noch nichts in der DB stehen.
    assert db.execute(select(func.count()).select_from(Outbox)).scalar_one() == 0
    db.flush()
    assert db.execute(select(func.count()).select_from(Outbox)).scalar_one() == 2
