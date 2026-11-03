-- Rollen -> Berechtigungen, Schritt 1: additives Schema
--
-- - neue Tabelle `system_permissions` (audit/monitor direkt je Principal),
-- - Eindeutigkeit je ACL-Ziel (entry_id, principal_type, principal_id).
--
-- Bestehende Rollen und `role_assignments` bleiben unverändert lesbar; der
-- Backfill folgt in 0012, die destruktive Bereinigung erst in 0013.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0011_permissions_schema.sql
--
-- Idempotent: kann mehrfach angewendet werden. Neue Installationen erhalten
-- Tabelle und Index automatisch über create_all.

BEGIN;

CREATE TABLE IF NOT EXISTS system_permissions (
    id             uuid PRIMARY KEY,
    principal_type varchar(16) NOT NULL,   -- user|group
    principal_id   uuid NOT NULL,
    permissions    integer NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_system_permissions_principal
    ON system_permissions (principal_type, principal_id);

-- Vor dem Unique-Index vorhandene Duplikate zusammenführen (Bit-OR). Das ist
-- wichtig für Produktionsdaten, die vor der Umstellung mehrere ACL-Zeilen für
-- denselben Principal/Eintrag enthalten können. Die Tabellensperre verhindert
-- konkurrierende Inserts zwischen Dedupe und Index-Erstellung.
LOCK TABLE acl IN SHARE ROW EXCLUSIVE MODE;

DROP TABLE IF EXISTS _acl_dedup;
CREATE TEMP TABLE _acl_dedup AS
SELECT entry_id,
       principal_type,
       principal_id,
       bit_or(perms) AS perms
FROM acl
GROUP BY entry_id, principal_type, principal_id
HAVING count(*) > 1;

DELETE FROM acl a
USING _acl_dedup d
WHERE a.entry_id = d.entry_id
  AND a.principal_type = d.principal_type
  AND a.principal_id = d.principal_id;

INSERT INTO acl (id, entry_id, principal_type, principal_id, perms, inherited, created_at)
SELECT gen_random_uuid(), d.entry_id, d.principal_type, d.principal_id,
       d.perms, false, now()
FROM _acl_dedup d;

DROP TABLE _acl_dedup;

CREATE UNIQUE INDEX IF NOT EXISTS uq_acl_entry_principal
    ON acl (entry_id, principal_type, principal_id);

COMMIT;
