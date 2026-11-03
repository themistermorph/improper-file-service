# Datenmodell und Speicherlayout

Dieses Dokument beschreibt, wie IFS Metadaten und Dateiinhalte ablegt.

**Grundregel:** Der Namespace lebt in **PostgreSQL**, die Inhalte als unveränderliche
Blobs in **S3**. `entries.current_version_id` ist eine *weiche* Referenz (kein
Fremdschlüssel), um einen zyklischen FK zwischen `entries` und `versions` zu vermeiden.

---

## 1. Überblick

```
        ┌──────────┐   n:m   ┌──────────┐
        │  users   │◄───────►│  groups  │
        └────┬─────┘         └────┬─────┘
             │ owner_id           │ principal_id
             ▼                    ▼
        ┌───────────────────────────────┐
        │            entries            │  Baum: parent_id → entries.id
        │  folder | file, current_version│
        └───────────────┬───────────────┘
                        │ entry_id
                        ▼
                 ┌─────────────┐   blob_id    ┌──────────────┐
                 │  versions   │─────────────►│    blobs     │
                 └─────────────┘              └──────┬───────┘
                                                    │ storage_key
                                                    ▼
                                          S3: cas/<sha[:2]>/<sha256>
```

Weitere Tabellen: `credentials`, `acl`, `system_permissions`, `roles`, `role_assignments`, `shares`,
`published_entries`, `upload_sessions`, `audit_log`, `outbox`.

---

## 2. Tabellen

### `users`

| Spalte | Typ | Anmerkung |
|---|---|---|
| `id` | UUID (v7) | Primärschlüssel |
| `username` | varchar(255) | eindeutig, indexiert |
| `display_name` | varchar(255) | optionaler Anzeigename; wird in Listen statt des Benutzernamens angezeigt (Fallback: Benutzername) |
| `email` | varchar(255) | optional |
| `password_hash` | varchar(255) | Argon2id |
| `is_admin` | bool | globaler Admin |
| `is_active` | bool | deaktivierbar (Standard statt Löschen) |
| `token_version` | int | wird bei Passwortänderung/Deaktivierung erhöht und invalidiert Tokens |
| `last_login_at` | timestamptz \| null | letzter erfolgreicher Login |
| `created_at` | timestamptz | |

### `groups` / `group_members`

`groups(id, name)`; `group_members(group_id, user_id)` als n:m-Verknüpfung.

### `credentials`

Zusätzliche Zugangsdaten/Tokens: `id`, `user_id`, `type` (`token`/`ftp`),
`secret_hash`, `scopes` (JSON), `expires_at`, `created_at`.

### `entries`

| Spalte | Typ | Anmerkung |
|---|---|---|
| `id` | UUID (v7) | Primärschlüssel |
| `parent_id` | UUID \| null | Wurzel hat `null` (eine Wurzel je `owner_id`) |
| `name` | varchar(1024) | Eindeutigkeit case-insensitiv pro Parent (App-seitig geprüft) |
| `type` | enum | `folder` \| `file` |
| `owner_id` | UUID | hat implizit alle Rechte |
| `current_version_id` | UUID \| null | weiche Referenz auf aktuelle Version |
| `size`, `mime`, `sha256` | – | aus der aktuellen Version denormalisiert |
| `trashed_at` | timestamptz \| null | Papierkorb |
| `trashed_by` | UUID \| null | Wer den Eintrag in den Papierkorb verschoben hat |
| `original_parent_id` | UUID \| null | Ursprünglicher Zielordner (für robustes Wiederherstellen) |
| `created_at`, `updated_at` | timestamptz | |

Indizes (u. a.): eindeutiger Name je Parent (`uq_entries_parent_lower_name`, nur aktive
Einträge) und **genau eine aktive Wurzel je Besitzer** (`uq_entries_root_owner` auf
`owner_id` mit `parent_id IS NULL AND trashed_at IS NULL`) – Grundlage der
Per-User-Wurzeln.

### `blobs`

| Spalte | Typ | Anmerkung |
|---|---|---|
| `id` | UUID | |
| `storage_key` | varchar(255) | eindeutig, z. B. `cas/9f/9f86…` |
| `sha256` | varchar(64) | indexiert (Dedup) |
| `size` | bigint | |
| `status` | enum | `pending` \| `ready` \| `deleted` |
| `created_at` | timestamptz | |

### `versions`

`id`, `entry_id`, `blob_id`, `seq` (fortlaufend je Eintrag), `size`, `mime`, `sha256`,
`created_by`, `created_at`.

### `acl`

`id`, `entry_id`, `principal_type` (`user`/`group`), `principal_id`, `perms` (Bitmaske),
`inherited`, `created_at`. Seit der Rollen-Umstellung ist
`(entry_id, principal_type, principal_id)` **eindeutig**
(`uq_acl_entry_principal`); wiederholte Vergaben werden per Upsert
zusammengeführt. Vor dem Index vorhandene Duplikate werden in Migration `0011`
per `bit_or(perms)` dedupliziert.

Eintrags-Bitmaske: `read=1`, `write=2`, `delete=4`, `share=8`, `admin=16`,
`publish=32`. `ALL_RESOURCE = 63`. Systemrechte (`audit=64`, `monitor=128`)
dürfen **nicht** in `acl` gespeichert werden.

### `system_permissions`

| Spalte | Typ | Anmerkung |
|---|---|---|
| `id` | UUID | |
| `principal_type` | varchar(16) | `user` \| `group` |
| `principal_id` | UUID | Benutzer- oder Gruppen-ID |
| `permissions` | int | nur `audit=64` und/oder `monitor=128` |
| `created_at` | timestamptz | |

Unique-Index `(principal_type, principal_id)`: ein Datensatz je Principal.
`ADMIN` ist hier bewusst nicht erlaubt – Systemverwaltung kommt ausschließlich
über die Adminrolle bzw. das Bootstrap-Flag `users.is_admin`.

### `roles`

Nach dem Cleanup enthält die Tabelle **nur noch** `admin` (systemweit,
`permissions=ALL=255`). Die früheren fachlichen Rollen (`auditor`, `user`,
`viewer`, `collaborator`, `editor`, `sharer`, `manager`) wurden in ACL- bzw.
System-Grants migriert und gelöscht.

### `role_assignments`

Enthält nur noch systemweite Admin-Zuweisungen (`entry_id IS NULL`).
Entry-gebundene Zuweisungen sind nach der Migration ungültig und werden in
`0013` entfernt. `users.is_admin` bleibt als Bootstrap-/Legacy-Flag lesbar und
wird mit der Adminrolle synchron gehalten.

**Rechteberechnung:** effektive Eintragsrechte = Besitz (`ALL_RESOURCE`) ∪ ACLs
entlang des Pfades. `system_perms` = `users.is_admin`/Adminrolle (`ADMIN`) ∪
`system_permissions` (`AUDIT`/`MONITOR`). Die beiden Namensräume werden nie
vermischt; Systemadmins haben weiterhin **keinen** Dateizugriff auf fremde
Bäume. Details: [Sicherheit](sicherheit.md).


### `shares`

`token` (PK), `entry_id`, `created_by`, `password_hash`, `expires_at`, `max_downloads`,
`downloads`, `allow_upload`, `overwrite`, `created_at`.

### `published_entries`

`entry_id` (PK, FK auf `entries` `ON DELETE CASCADE`), `published_by`, `public_name`,
`public_path`, `created_at`. Ein Eintrag kann höchstens einmal veröffentlicht sein;
Ordner werden beim Download als ZIP geliefert. `public_name` ist der optionale öffentliche
Anzeigename (leer = interner Name). `public_path` ist der optionale Vanity-Link
`<username>/<suffix>` (Suffix ohne `/`); der vollständige Pfad ist über einen
Unique-Index auf `lower(public_path)` case-insensitiv eindeutig. Leer = kein eigener
Link. Die öffentliche Galerie (`/published`) listet nur aktive Einträge; Papierkorb und
deaktivierte Konten ziehen die Veröffentlichung automatisch zurück (der Suffix wird
damit frei).

### `upload_sessions`

`id`, `owner_id`, `parent_id`, `name`, `size_expected`, `sha256_expected`, `offset`,
`spool_path`, `status` (`pending`/`completed`/`aborted`), `protocol`, Zeitstempel.

### `audit_log`

`id`, `ts`, `actor_id`, `action`, `target_entry`, `protocol`, `ip`, `result`, `details`
(JSON). Append-only.

### `outbox`

`id`, `event_type`, `payload` (JSON), `created_at`, `published_at`. Transaktional mit
der auslösenden Änderung geschrieben und vom Worker abgearbeitet.

---

## 3. Speicherlayout in S3

Object-Storage ist flach und kennt keine Ordner – die Hierarchie liegt ausschließlich in
der Datenbank. Blobs sind **content-addressed**:

```
<IFS_S3_BUCKET>/
  cas/
    9f/
      9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
    ab/
      ab5a…
```

- Der Schlüssel folgt aus dem SHA-256 des Inhalts → **automatische Deduplizierung**
  (`IFS_CAS_DEDUP=true`): identische Dateien belegen nur einmal Speicher.
- Blobs werden **niemals in-place** geändert. Neue Inhalte ergeben einen neuen Blob und
  eine neue `version`.
- S3-Schlüssel tragen **keine** Nutzer- oder Rechte-Semantik.

---

## 4. Atomarer Schreibvorgang

```
Upload ──► Temp-Datei / S3-Objekt (neuer Blob)
                │  Integritätsprüfung (Größe, SHA-256)
                ▼
        ┌─────────────────────────────────────────────┐
        │ eine DB-Transaktion:                         │
        │  1. version (seq = max+1) anlegen            │
        │  2. entries.current_version_id = version.id  │
        │  3. entries.size/mime/sha256 aktualisieren   │
        │  4. Outbox-Event entry.written               │
        └─────────────────────────────────────────────┘
```

Leser sehen dadurch entweder die alte oder die neue Version – nie einen Zwischenstand.

---

## 5. Löschen, Papierkorb, Garbage Collection

1. **Soft-Delete:** `DELETE` setzt `trashed_at`, `trashed_by` und `original_parent_id` im
   gesamten Teilbaum. Zugehörige **Freigabelinks und Veröffentlichungen werden
   automatisch widerrufen**.
2. **Wiederherstellen:** Reaktiviert den Teilbaum; der Zielordner wird bei Bedarf
   reaktiviert, Namenskonflikte werden abgewiesen (`409`).
3. **Papierkorb-Frist:** Der Worker löscht Einträge nach `IFS_TRASH_RETENTION_DAYS`
   (Standard 30 Tage) endgültig.
4. **Blob-GC:** Der Worker entfernt Blobs, auf die keine `version` mehr verweist, sowohl
   aus der DB als auch aus S3.

Die Deduplizierung teilt Blobs zwischen mehreren Einträgen – der GC berücksichtigt das
über die Referenzzählung („gibt es noch eine Version mit diesem `blob_id`?“).

---

## 6. Konsistenz und Nebenläufigkeit

| Thema | Festlegung |
|---|---|
| Quelle der Wahrheit | PostgreSQL. S3 wird nie gelistet, um Namespace abzuleiten. |
| Umbenennen/Verschieben | reine Metadaten-Änderung, atomar, O(1) |
| Namenskonflikte | case-insensitiver Vergleich pro Parent, `409` bei Konflikt |
| Schreibkonflikte | Optimistische Prüfung über Versionen; neue Version statt In-Place-Änderung |
| Idempotenz | Upload-Abschluss ist wiederholbar; fehlgeschlagene Uploads hinterlassen keinen sichtbaren Eintrag |
| Events | Outbox-Pattern → Worker (Scan, GC, Benachrichtigung) |

---

## 7. Migrationen

Das Schema ist in `src/ifs/models.py` definiert. Für den Schnellstart legt
`ifs init-db` es per `create_all` an. Produktive Änderungen werden als
idempotente SQL-Migration unter `migrations/sql/` ergänzt und mit
`bash deploy/apply-migrations.sh` angewendet. Details:
[Betrieb](betrieb.md#7-upgrades-und-migrationen) und
[`migrations/README.md`](../migrations/README.md).
