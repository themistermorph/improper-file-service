-- Ausbaustufe 2 – Sicherheits-Review N3
-- Eindeutiger (case-insensitive) Name je Parent, nur für aktive Einträge.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0004_unique_entry_names.sql
--
-- Hinweis: Schlägt fehl, wenn bereits Duplikate existieren. Diese vorher bereinigen:
--   SELECT parent_id, lower(name), count(*) FROM entries
--   WHERE trashed_at IS NULL GROUP BY 1,2 HAVING count(*) > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_parent_lower_name
    ON entries (parent_id, lower(name))
    WHERE trashed_at IS NULL;
