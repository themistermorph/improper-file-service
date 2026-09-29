-- Ausbaustufe 3 – Sicherheits-Review F1 (Teil B)
-- Überschreiben wird von einem Request-Parameter zu einer Freigabe-Eigenschaft.
-- Bestehende Freigaben überschreiben damit nicht mehr automatisch (Default FALSE).
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0005_share_overwrite.sql
--
-- Hinweis: Neue Installationen erhalten die Spalte automatisch über create_all
-- bzw. Alembic.

ALTER TABLE shares ADD COLUMN IF NOT EXISTS overwrite boolean NOT NULL DEFAULT false;
