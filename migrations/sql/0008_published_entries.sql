-- Ausbaustufe 5 – Öffentliche Veröffentlichungen (Galerie unter /published)
-- Benutzer können einzelne Dateien ausdrücklich veröffentlichen; die Galerie
-- listet diese Dateien ohne Anmeldung auf und erlaubt den Download.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0008_published_entries.sql
--
-- Idempotent: kann mehrfach angewendet werden. Neue Installationen erhalten die
-- Tabelle automatisch über create_all bzw. Alembic.

CREATE TABLE IF NOT EXISTS published_entries (
    entry_id     uuid PRIMARY KEY REFERENCES entries (id) ON DELETE CASCADE,
    published_by uuid,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Galerie-Auflistung: neueste Veröffentlichung zuerst.
CREATE INDEX IF NOT EXISTS ix_published_entries_created
    ON published_entries (created_at);
