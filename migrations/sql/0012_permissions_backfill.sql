-- Rollen -> Berechtigungen, Schritt 2: nicht destruktiver Backfill
--
-- Erzeugt aus den bisherigen Rollen echte Grants:
--   * Ressourcenrollen -> ACL am jeweiligen Eintrag,
--   * auditor        -> system_permissions.audit,
--   * ADMIN-Systemrollen -> Adminrollen-Zuweisung,
--   * users.is_admin -> Adminrollen-Zuweisung,
--   * bestehende ACLs mit share -> zusätzlich publish.
--
-- Rollen und role_assignments bleiben absichtlich erhalten (Rollback ohne
-- Datenverlust). Die Bereinigung folgt erst nach der Verifikation in 0013.
--
-- Anwenden:
--   docker compose exec -T db psql -U ifs -d ifs < migrations/sql/0012_permissions_backfill.sql
--
-- Idempotent: Upserts (ON CONFLICT) bzw. NOT EXISTS verhindern Duplikate.

BEGIN;

-- Adminrolle sicherstellen und auf alle (neuen) Bits setzen.
INSERT INTO roles (id, name, description, permissions, scope, is_builtin, created_at)
SELECT gen_random_uuid(), 'admin', 'Vollzugriff (systemweit)', 255, 'system', true, now()
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'admin');

UPDATE roles
SET permissions = 255, scope = 'system', is_builtin = true
WHERE name = 'admin';

-- Ressourcenrollen -> ACL. SHARE impliziert PUBLISH, damit bestehende Nutzer
-- ihre Veröffentlichungsmöglichkeit nicht verlieren.
INSERT INTO acl (id, entry_id, principal_type, principal_id, perms, inherited, created_at)
SELECT gen_random_uuid(),
       ra.entry_id,
       ra.principal_type,
       ra.principal_id,
       (r.permissions & 63)
           | CASE WHEN (r.permissions & 8) <> 0 THEN 32 ELSE 0 END,
       false,
       now()
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.scope = 'resource'
  AND ra.entry_id IS NOT NULL
  AND ((r.permissions & 63)
       | CASE WHEN (r.permissions & 8) <> 0 THEN 32 ELSE 0 END) <> 0
ON CONFLICT (entry_id, principal_type, principal_id)
DO UPDATE SET perms = acl.perms | EXCLUDED.perms;

-- auditor (bisher 0 Rechte) -> echtes AUDIT-Recht; weitere Systemrollen mit
-- audit/monitor werden übernommen. Die Adminrolle selbst erhält keinen
-- System-Grant (Admin kommt über die Rolle).
INSERT INTO system_permissions (id, principal_type, principal_id, permissions, created_at)
SELECT gen_random_uuid(),
       ra.principal_type,
       ra.principal_id,
       CASE WHEN r.name = 'auditor' THEN 64 ELSE (r.permissions & 192) END,
       now()
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.scope = 'system'
  AND ra.entry_id IS NULL
  AND r.name <> 'admin'
  AND (CASE WHEN r.name = 'auditor' THEN 64 ELSE (r.permissions & 192) END) <> 0
ON CONFLICT (principal_type, principal_id)
DO UPDATE SET permissions = system_permissions.permissions | EXCLUDED.permissions;

-- Systemrollen mit ADMIN -> Zuweisung zur Adminrolle.
INSERT INTO role_assignments (id, role_id, principal_type, principal_id, entry_id, created_at)
SELECT gen_random_uuid(), admin.id, src.principal_type, src.principal_id, NULL, now()
FROM (
    SELECT DISTINCT ra.principal_type, ra.principal_id
    FROM role_assignments ra
    JOIN roles r ON r.id = ra.role_id
    WHERE r.scope = 'system'
      AND ra.entry_id IS NULL
      AND r.name <> 'admin'
      AND (r.permissions & 16) <> 0
) src
CROSS JOIN (SELECT id FROM roles WHERE name = 'admin') admin
WHERE NOT EXISTS (
    SELECT 1 FROM role_assignments a
    WHERE a.role_id = admin.id
      AND a.principal_type = src.principal_type
      AND a.principal_id = src.principal_id
      AND a.entry_id IS NULL
);

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

-- Bestehende ACLs: share -> zusätzlich publish.
UPDATE acl
SET perms = perms | 32
WHERE (perms & 8) <> 0
  AND (perms & 32) = 0;

COMMIT;
