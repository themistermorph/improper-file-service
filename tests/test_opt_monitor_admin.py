"""Performance-Regressionstests für Admin-Monitoring und Audit.

Die Tests hängen sich per SQLAlchemy-Event (``before_cursor_execute``) an die
aktuelle Engine und zählen die tatsächlich ausgeführten Statements. Damit wird
belegt, dass wiederholtes Polling der Admin-Endpunkte keine doppelte
DB-Arbeit erzeugt und der kurze TTL-Cache nur Systemmetriken betrifft.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from ifs import main
from ifs.core import audit, system_stats
from ifs.db import get_engine

from .conftest import make_user
from .helpers import login


@pytest.fixture
def sql_statements():
    """Zeichnet alle SQL-Statements der aktuellen Engine auf."""
    statements: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def _counts_sql(statements: list[str]) -> list[str]:
    """Statements, die die kombinierten users/entries-Kennzahlen abfragen."""
    return [s for s in statements if "count(" in s.lower() and "from users" in s.lower()]


def _audit_sql(statements: list[str]) -> list[str]:
    return [s for s in statements if "from audit_log" in s.lower()]


# --- TTL-/Query-Zähler ---------------------------------------------------------


def test_polling_reuses_db_metrics_within_ttl(monkeypatch, sql_statements):
    monkeypatch.setattr(system_stats, "_DB_METRICS_TTL", 60.0)
    system_stats.reset_db_metrics_cache()
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        sql_statements.clear()
        first = client.get("/api/system/stats", headers=headers)
        second = client.get("/api/system/stats", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["ifs"] == second.json()["ifs"]
        # Mehrfaches Polling erzeugt nur eine Zähler-Abfrage.
        assert len(_counts_sql(sql_statements)) == 1
    system_stats.reset_db_metrics_cache()


def test_collect_uses_one_combined_query(monkeypatch, sql_statements, db):
    monkeypatch.setattr(system_stats, "_DB_METRICS_TTL", 60.0)
    system_stats.reset_db_metrics_cache()
    make_user(db, "cacheuser")
    sql_statements.clear()

    result = system_stats.collect(db, "/")
    assert result["ifs"]["users"] == 1
    counted = _counts_sql(sql_statements)
    assert len(counted) == 1, "Kennzahlen werden nicht in einer Abfrage gebündelt"
    assert "from entries" in counted[0].lower()
    assert "sum(" in counted[0].lower()

    # Zweiter Aufruf innerhalb der TTL: keine weitere Zähler-Abfrage.
    sql_statements.clear()
    again = system_stats.collect(db, "/")
    assert again["ifs"] == result["ifs"]
    assert _counts_sql(sql_statements) == []


def test_db_metrics_cache_expires_and_shows_changes(monkeypatch, sql_statements, db):
    monkeypatch.setattr(system_stats, "_DB_METRICS_TTL", 60.0)
    system_stats.reset_db_metrics_cache()
    before = system_stats.collect(db, "/")["ifs"]["users"]

    make_user(db, "lateruser")
    # Innerhalb der TTL bleibt der zuletzt ermittelte Wert stabil (kurze Verzögerung).
    assert system_stats.collect(db, "/")["ifs"]["users"] == before

    # Nach Ablauf/Invalidierung ist die Änderung sofort sichtbar.
    system_stats.reset_db_metrics_cache()
    assert system_stats.collect(db, "/")["ifs"]["users"] == before + 1


def test_db_metrics_cache_zero_ttl_always_queries(monkeypatch, sql_statements, db):
    monkeypatch.setattr(system_stats, "_DB_METRICS_TTL", 0.0)
    system_stats.reset_db_metrics_cache()
    system_stats.collect(db, "/")
    sql_statements.clear()
    system_stats.collect(db, "/")
    assert len(_counts_sql(sql_statements)) == 1


def test_metrics_shares_combined_counters(monkeypatch, sql_statements):
    monkeypatch.setattr(system_stats, "_DB_METRICS_TTL", 60.0)
    system_stats.reset_db_metrics_cache()
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        assert client.get("/api/system/stats", headers=headers).status_code == 200
        sql_statements.clear()
        response = client.get("/metrics", headers=headers)
        assert response.status_code == 200
        assert "ifs_users" in response.text
        # /metrics profitiert vom selben TTL-Cache: kein doppelter DB-Zugriff.
        assert _counts_sql(sql_statements) == []
    system_stats.reset_db_metrics_cache()


def test_healthz_runs_without_queries(sql_statements):
    with TestClient(main.app) as client:
        sql_statements.clear()
        response = client.get("/healthz")
        assert response.json() == {"status": "ok"}
        assert sql_statements == []


# --- Audit: Filter, Reihenfolge, Pagination, Join ------------------------------


def test_audit_list_recent_filter_pagination_and_join(sql_statements, db):
    user = make_user(db, "auditor")
    audit.record(db, "opt.first", actor_id=user.id)
    db.flush()
    time.sleep(0.01)
    audit.record(db, "opt.second", actor_id=user.id)
    db.flush()

    sql_statements.clear()
    rows = audit.list_recent(db, limit=50)
    assert [row.action for row, _, _ in rows] == ["opt.second", "opt.first"]
    assert all(username == "auditor" for _, username, _ in rows)
    combined = _audit_sql(sql_statements)
    assert len(combined) == 1, "Actor-Namen verursachen einen zweiten Roundtrip"
    assert "join users" in combined[0].lower()

    filtered = audit.list_recent(db, action="opt.first")
    assert [row.action for row, _, _ in filtered] == ["opt.first"]

    window = audit.list_recent(db, limit=1, offset=1)
    assert [row.action for row, _, _ in window] == ["opt.first"]

    # Klemmung wie bisher: limit 0 -> 1.
    assert len(audit.list_recent(db, limit=0)) == 1


def test_audit_endpoint_fresh_filtered_and_uncached(sql_statements):
    with TestClient(main.app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post(
            "/api/users",
            headers=headers,
            json={"username": "auditfresh", "password": "geheim123"},
        )
        assert created.status_code == 201

        # Frisch geschriebene Audit-Einträge sind sofort sichtbar (kein Cache).
        rows = client.get("/api/audit?action=user.create", headers=headers).json()
        assert rows
        assert all(row["action"] == "user.create" for row in rows)
        assert rows[0]["actor_username"] == "admin"
        assert set(rows[0]) == {
            "id",
            "ts",
            "actor_id",
            "actor_username",
            "actor_display_name",
            "action",
            "target_entry",
            "protocol",
            "ip",
            "result",
            "details",
        }

        # Zwei Abfragen -> zwei Statements: Audit wird nicht gecacht.
        sql_statements.clear()
        client.get("/api/audit?limit=5", headers=headers)
        client.get("/api/audit?limit=5", headers=headers)
        assert len(_audit_sql(sql_statements)) == 2
