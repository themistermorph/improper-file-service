-- Öffentliche Veröffentlichungen – benutzerdefinierter Link-Pfad
-- Format: <username>/<suffix>; der Suffix enthält kein '/'.
-- Der komplette Pfad ist case-insensitiv eindeutig; leer/NULL = kein eigener
-- Link (kanonische API-URL /api/published/<uuid>/content).
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0010_published_public_path.sql
--
-- Idempotent: kann mehrfach angewendet werden. Neue Installationen erhalten die
-- Spalte automatisch über create_all bzw. Alembic.

ALTER TABLE published_entries
    ADD COLUMN IF NOT EXISTS public_path varchar(512);

CREATE UNIQUE INDEX IF NOT EXISTS uq_published_entries_public_path
    ON published_entries (lower(public_path));
