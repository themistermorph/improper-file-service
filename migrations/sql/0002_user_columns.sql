-- Ausbaustufe 2, M2 – Account-Manager
-- Für bestehende Installationen (idempotent). Neue Installationen erhalten die
-- Spalten automatisch über create_all bzw. Alembic.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0002_user_columns.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name varchar(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
