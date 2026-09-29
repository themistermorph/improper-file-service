-- Ausbaustufe 3 – Per-User-Wurzeln
-- Genau eine aktive Wurzel (parent_id IS NULL) je Besitzer.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0006_per_user_roots.sql
--
-- Hinweis: Neue Installationen erhalten den Index automatisch über create_all.
-- Für bestehende Installationen vor dem Index für jeden Benutzer eine Wurzel anlegen
-- (bzw. den Test-/Bestandsbaum bewusst migrieren).

CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_root_owner
    ON entries (owner_id)
    WHERE parent_id IS NULL AND trashed_at IS NULL;
