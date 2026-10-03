-- Öffentliche Veröffentlichungen – öffentlicher Anzeigename
-- Optionaler Name, unter dem eine Veröffentlichung in der Galerie erscheint.
-- Leer = interner Datei-/Ordnername.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0009_published_public_name.sql
--
-- Idempotent: kann mehrfach angewendet werden. Neue Installationen erhalten die
-- Spalte automatisch über create_all bzw. Alembic.

ALTER TABLE published_entries
    ADD COLUMN IF NOT EXISTS public_name varchar(255);
