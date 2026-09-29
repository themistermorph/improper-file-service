-- Ausbaustufe 2, M1 – Papierkorb
-- Für bestehende Installationen (idempotent). Neue Installationen erhalten die
-- Spalten automatisch über create_all bzw. Alembic.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0001_trash_columns.sql

ALTER TABLE entries ADD COLUMN IF NOT EXISTS trashed_by uuid;
ALTER TABLE entries ADD COLUMN IF NOT EXISTS original_parent_id uuid;
