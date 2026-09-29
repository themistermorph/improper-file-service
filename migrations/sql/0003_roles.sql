-- Ausbaustufe 2, M3 – Rollenmanagement
-- Für bestehende Installationen (idempotent). Neue Installationen erhalten die
-- Tabellen automatisch über create_all bzw. Alembic. Die eingebauten Rollen
-- werden beim Start der API angelegt (ensure_builtin_roles).
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0003_roles.sql

CREATE TABLE IF NOT EXISTS roles (
    id          uuid PRIMARY KEY,
    name        varchar(64) NOT NULL UNIQUE,
    description varchar(255),
    permissions integer NOT NULL DEFAULT 0,
    scope       varchar(16) NOT NULL DEFAULT 'resource',
    is_builtin  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_roles_name ON roles (name);

CREATE TABLE IF NOT EXISTS role_assignments (
    id             uuid PRIMARY KEY,
    role_id        uuid NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    principal_type varchar(16) NOT NULL,
    principal_id   uuid NOT NULL,
    entry_id       uuid REFERENCES entries (id) ON DELETE CASCADE,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_role_assignments_role ON role_assignments (role_id);
CREATE INDEX IF NOT EXISTS ix_role_assignments_principal ON role_assignments (principal_id);
CREATE INDEX IF NOT EXISTS ix_role_assignments_entry ON role_assignments (entry_id);
