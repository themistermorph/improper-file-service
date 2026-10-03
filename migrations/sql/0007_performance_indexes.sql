-- Ausbaustufe 4 – Datenbank-Performance
-- Fehlende Indizes passend zu den realen Abfragen (Namespace, Authz, Quota,
-- Worker, Audit, Freigaben). Das Schema selbst bleibt unverändert.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0007_performance_indexes.sql
--
-- Idempotent: kann mehrfach angewendet werden. Einzelindizes, die von den
-- neuen zusammengesetzten Indizes vollständig abgedeckt werden, werden ersetzt
-- (Spalte bleibt führende Spalte des neuen Index); neue Installationen erhalten
-- den Endzustand direkt über create_all.

-- Namespace: Kinder auflisten und Pfad-Traversierung (parent_id + trashed_at).
CREATE INDEX IF NOT EXISTS ix_entries_parent_trashed
    ON entries (parent_id, trashed_at);

-- Quota-Summe und Besitzer-Abfragen (owner_id + trashed_at).
CREATE INDEX IF NOT EXISTS ix_entries_owner_trashed
    ON entries (owner_id, trashed_at);

-- Papierkorb-Liste und Aufräumen durch den Worker (trashed_at IS NOT NULL).
CREATE INDEX IF NOT EXISTS ix_entries_trashed_at
    ON entries (trashed_at)
    WHERE trashed_at IS NOT NULL;

-- Versionshistorie: max(seq) je Eintrag bzw. Sortierung nach seq.
CREATE INDEX IF NOT EXISTS ix_versions_entry_seq
    ON versions (entry_id, seq);

-- Rechteprüfung je Eintrag (ACL.entry_id) inklusive Principal als Filter.
CREATE INDEX IF NOT EXISTS ix_acl_entry_principal
    ON acl (entry_id, principal_type, principal_id);

-- Rollenprüfung je Principal (auch systemweite Rollen mit entry_id IS NULL)
-- und die Auflistung „shared“.
CREATE INDEX IF NOT EXISTS ix_role_assignments_principal_entry
    ON role_assignments (principal_type, principal_id, entry_id);

-- Audit-Ansicht: ORDER BY ts DESC.
CREATE INDEX IF NOT EXISTS ix_audit_log_ts
    ON audit_log (ts);

-- Outbox: unveröffentlichte Events in Reihenfolge (published_at IS NULL).
CREATE INDEX IF NOT EXISTS ix_outbox_published_created
    ON outbox (published_at, created_at);

-- Worker: veraltete Upload-Sessions finden (status + updated_at).
CREATE INDEX IF NOT EXISTS ix_upload_sessions_status_updated
    ON upload_sessions (status, updated_at);

-- Eigene Freigaben auflisten bzw. beim Löschen eines Benutzers entfernen.
CREATE INDEX IF NOT EXISTS ix_shares_created_by_created
    ON shares (created_by, created_at);

-- Ersetzte Einzelindizes (redundant zu den neuen zusammengesetzten Indizes).
DROP INDEX IF EXISTS ix_entries_parent_id;
DROP INDEX IF EXISTS ix_entries_owner_id;
DROP INDEX IF EXISTS ix_versions_entry_id;
DROP INDEX IF EXISTS ix_acl_entry_id;
DROP INDEX IF EXISTS ix_outbox_published_at;
