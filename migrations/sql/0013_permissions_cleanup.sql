-- Rollen -> Berechtigungen, Schritt 3: Cleanup (destruktiv)
--
-- NUR nach erfolgreichem Backfill (0012) und verifizierter Testmatrix
-- ausführen. Danach existiert nur noch die Adminrolle; alle fachlichen Rollen
-- und deren Zuweisungen werden entfernt. Vorher Backup erstellen!
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0013_permissions_cleanup.sql
--
-- Idempotent: kann mehrfach angewendet werden.

BEGIN;

-- Adminrolle sicherstellen und auf alle Bits setzen.
INSERT INTO roles (id, name, description, permissions, scope, is_builtin, created_at)
SELECT gen_random_uuid(), 'admin', 'Vollzugriff (systemweit)', 255, 'system', true, now()
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'admin');

UPDATE roles
SET permissions = 255, scope = 'system', is_builtin = true,
    description = 'Vollzugriff (systemweit)'
WHERE name = 'admin';

-- Alle fachlichen Rollen entfernen (CASCADE entfernt Zuweisungen zusätzlich).
DELETE FROM roles WHERE name <> 'admin';

-- Entry-gebundene Admin-Zuweisungen sind nach der Umstellung ungültig.
DELETE FROM role_assignments WHERE entry_id IS NOT NULL;

-- Bootstrap-Flag users.is_admin -> Adminrollen-Zuweisung.
INSERT INTO role_assignments (id, role_id, principal_type, principal_id, entry_id, created_at)
SELECT gen_random_uuid(), admin.id, 'user', u.id, NULL, now()
FROM users u
CROSS JOIN (SELECT id FROM roles WHERE name = 'admin') admin
WHERE u.is_admin = true
  AND NOT EXISTS (
      SELECT 1 FROM role_assignments a
      WHERE a.role_id = admin.id
        AND a.principal_type = 'user'
        AND a.principal_id = u.id
        AND a.entry_id IS NULL
  );

COMMIT;
