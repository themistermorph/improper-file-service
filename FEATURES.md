# Featurebeschreibung – Ausbaustufe 2

> Status: Entwurf / Diskussionsgrundlage
> Grundlage: [`ANFORDERUNGEN.md`](ANFORDERUNGEN.md), [`ARCHITEKTUR.md`](ARCHITEKTUR.md)
> Priorisierung: **MUSS** = Kern des Features, **SOLLTE** = wichtig, **KANN** = optional.

Dieses Dokument beschreibt mittelfein die nächsten Funktionen des IFS:

1. [Account-Manager](#1-account-manager)
2. [Papierkorb](#2-papierkorb)
3. [Rollenmanagement](#3-rollenmanagement)
4. [Weitere Vorschläge (priorisiert)](#4-weitere-vorschläge)
5. [Umsetzungsreihenfolge](#5-umsetzungsreihenfolge)
6. [Querschnittsanforderungen](#6-querschnittsanforderungen)

---

## 0. Festgelegte Entscheidungen (Ausbaustufe 2)

| Thema | Entscheidung |
|---|---|
| Start-Reihenfolge | **M1 Papierkorb** → M2 Account-Manager → M3 Rollenmanagement |
| Rollen vs. ACLs | **Rollen ergänzen ACLs** (bestehende feingranulare ACLs bleiben erhalten) |
| Benutzer löschen | **Deaktivieren ist Standard**; endgültiges Löschen optional mit Besitzübertragung |
| Priorisierte Zusatzfeatures | Versionshistorie, Bulk-Aktionen, Freigaben-Verwaltung, Audit-Log-Ansicht, **Datei-Vorschau** (Text/Code/HTML/PDF/Bilder/Video im Browser) |
| Per-User-Wurzel | Jeder Benutzer hat ein eigenes Home; Systemadmins haben **keinen** Dateizugriff auf fremde Wurzeln; interne Freigaben über „Mit mir geteilt" (Phase 2: Zero-Knowledge-E2E) |


## 1. Account-Manager

**Ziel:** Administratoren können Benutzer vollständig verwalten; Nutzer verwalten ihr
eigenes Profil und ihre Zugangsdaten.

**Akteure:** Systemadministrator (Vollzugriff), angemeldeter Benutzer (Selbstbedienung).

### Funktionale Anforderungen

| ID | Anforderung | Prio |
|---|---|---|
| AM-1 | Benutzer auflisten mit Suche, Filter (aktiv/inaktiv/Admin), Sortierung und Paginierung | MUSS |
| AM-2 | Benutzer anlegen (Benutzername, E-Mail, Passwort, Admin-Flag, Gruppen) | MUSS |
| AM-3 | Benutzer bearbeiten (E-Mail, Anzeigename, Admin-Flag) | MUSS |
| AM-4 | Passwort eines Benutzers zurücksetzen (Admin) – optional generiert | MUSS |
| AM-5 | Eigenes Passwort ändern (alt + neu) mit Stärkeprüfung | MUSS |
| AM-6 | Eigenes Profil bearbeiten (E-Mail, Anzeigename) | SOLLTE |
| AM-7 | Benutzer deaktivieren/aktivieren (Soft) statt sofortigem Löschen | MUSS |
| AM-8 | Benutzer löschen; Besitzübertragung optional (`transfer_to`) | SOLLTE |
| AM-9 | Gruppenmitgliedschaften pflegen (hinzufügen/entfernen) | MUSS |
| AM-10 | Letzten aktiven Administrator schützen (nicht deaktivieren/löschen) | MUSS |
| AM-11 | Persönliche API-Tokens/Sitzungen verwalten (erstellen, scopes, widerrufen) | KANN |
| AM-12 | Alle Aktionen auditieren | MUSS |
| AM-13 | Mehrfachauswahl: mehrere Benutzer gleichzeitig aktivieren/deaktivieren oder löschen (Batch, mit Besitzübertragung) | SOLLTE |

### Backend

- **Modell:** `users` (vorhanden) + optional `display_name`, `last_login_at`.
  `credentials` (vorhanden) für Tokens.
- **Endpunkte (Admin):**
  - `GET /api/users?q=&active=&admin=&limit=&offset=`
  - `POST /api/users`, `GET /api/users/{id}`, `PATCH /api/users/{id}`
  - `POST /api/users/{id}/password` (Reset), `POST /api/users/{id}/activate|deactivate`
  - `DELETE /api/users/{id}?transfer_to={uuid}`
  - `GET /api/users/{id}/groups`, `POST/DELETE /api/users/{id}/groups/{group_id}`
- **Selbstbedienung:**
  - `PATCH /api/me`, `POST /api/me/password`
  - `GET/POST /api/me/tokens`, `DELETE /api/me/tokens/{id}` (optional)
- **Regeln:** Admin nur für Verwaltung; Passwort-Reset erzwingt Ablauf? (nein, MVP);
  Deaktivieren invalidiert bestehende Tokens.

### UI

- Admin-Bereich **„Benutzer“**: Tabelle (Suche, Statusbadges), Anlegen-Dialog,
  Bearbeiten, Passwort-Reset, Deaktivieren/Aktivieren, Löschen, Gruppen.
- **„Mein Profil“**: E-Mail/Anzeigename, Passwort ändern, eigene Tokens.

### Abnahmekriterien (Auszug)

- Ein Admin kann einen Benutzer anlegen, ihm eine Gruppe zuweisen und ihn deaktivieren.
- Ein deaktivierter Benutzer kann sich weder per Web noch per FTPS anmelden.
- Der letzte aktive Admin kann nicht deaktiviert/gelöscht werden (`409`).

---

## 2. Papierkorb

**Ziel:** Gelöschte Einträge auffindbar und wiederherstellbar machen, bevor sie endgültig
entfernt werden.

**Akteure:** Benutzer mit `delete`-Recht (eigener Papierkorb), Administrator (alle).

### Funktionale Anforderungen

| ID | Anforderung | Prio |
|---|---|---|
| TR-1 | Papierkorb auflisten (nur die obersten gelöschten Einträge, nicht jede Datei einzeln) | MUSS |
| TR-2 | Pro Eintrag: Originalpfad, Größe, Löschzeitpunkt, verbleibende Aufbewahrungstage | MUSS |
| TR-3 | Eintrag wiederherstellen (inkl. Teilbaum) an ursprünglichen Ort | MUSS |
| TR-4 | Wiederherstellen mit neuem Namen/Ziel bei Namenskonflikt | MUSS |
| TR-5 | Eintrag endgültig löschen (inkl. Teilbaum) | MUSS |
| TR-6 | Papierkorb leeren (eigene / als Admin alle) | SOLLTE |
| TR-7 | Automatisches endgültiges Löschen nach Aufbewahrungsfrist (Standard 30 Tage) | MUSS |
| TR-8 | Anzahl im Papierkorb anzeigen (Badge) | SOLLTE |
| TR-9 | Audit aller Aktionen | MUSS |
| TR-10 | Mehrfachauswahl: mehrere Einträge gleichzeitig wiederherstellen oder endgültig löschen (Batch) | SOLLTE |

### Backend und Datenmodell

- `entries.trashed_at` existiert. **Neu:** `trashed_by`, `original_parent_id`
  (für robustes Wiederherstellen, wenn der Zielordner inzwischen gelöscht wurde).
- **Wiederherstellungsregeln:**
  1. Beim Wiederherstellen werden noch gelöschte **Vorfahren bis zur Wurzel** mit
     reaktiviert (konsistenter Pfad).
  2. Ist `original_parent_id` nicht mehr vorhanden (endgültig gelöscht) → Wurzel.
  3. Namenskonflikt am Ziel → `409` mit Option `new_name` oder Auto-Suffix `(1)`.
- **Endpunkte:**
  - `GET /api/trash` (Liste der Wurzel-Einträge im Papierkorb), `GET /api/trash/count`
  - `POST /api/trash/{entry_id}/restore` (`parent_id?`, `new_name?`)
  - `POST /api/trash/bulk/restore` / `POST /api/trash/bulk/purge` (`{ ids: [...] }`) →
    Batch-Wiederherstellung bzw. -Endlöschung, Antwort `{ ok, failed }` (Purge zusätzlich `files`)
  - `DELETE /api/trash/{entry_id}` (endgültig), `DELETE /api/trash` (leeren)
- **Worker:** räumt nach `TRASH_RETENTION_DAYS` endgültig auf (vorhanden); Blob-GC folgt.

### UI

- Ansicht **„Papierkorb“**: Liste mit Originalpfad, Größe, Restlaufzeit; Aktionen
  „Wiederherstellen“, „Endgültig löschen“, „Papierkorb leeren“.
- **Mehrfachauswahl** (Checkboxen + „Alle“) mit Bulk-Leiste für „Wiederherstellen“ und
  „Endgültig löschen“.
- Zähler/Badge in der Navigation.

### Abnahmekriterien (Auszug)

- Ein gelöschter Ordner erscheint **einmal** (als oberster Eintrag), nicht mit allen Kindern.
- Wiederherstellen stellt den kompletten Teilbaum am Ursprungsort wieder her.
- Endgültiges Löschen entfernt Metadaten und (per GC) unreferenzierte Blobs.

---

## 3. Rollenmanagement

**Ziel:** Rechtevergabe über benannte **Rollen** statt roher Rechte-Bitmasken – einfacher,
konsistenter und wiederverwendbar. Bestehendes ACL-Modell bleibt kompatibel.

**Akteure:** Administrator (Rollen definieren/zuteilen), Besitzer (Zugriff auf eigene
Einträge verwalten).

### Konzept

- Eine **Rolle** ist ein benanntes Bündel von Rechten
  (`read`, `write`, `delete`, `share`, `admin`).
- **Systemrollen** (global): z. B. `admin`, `auditor`, `user`. Werden Benutzern global
  zugewiesen.
- **Ressourcenrollen** (pro Eintrag): z. B. `viewer`, `collaborator`, `editor`,
  `manager`. Werden Benutzern/Gruppen auf einen Ordner/Eintrag zugewiesen und vererben
  sich auf Nachfahren.
- Eingebaute Rollen sind nicht löschbar (`is_builtin`), können aber dupliziert werden.

### Vorgeschlagene Rollen

| Rolle | Scope | Rechte |
|---|---|---|
| `admin` | system | alle |
| `auditor` | system | Lesezugriff auf Audit (kein Dateizugriff) |
| `user` | system | Standardnutzer (Rechte über Ressourcenrollen) |
| `viewer` | resource | `read` |
| `collaborator` | resource | `read`, `write` |
| `editor` | resource | `read`, `write`, `delete` |
| `sharer` | resource | `read`, `share` |
| `manager` | resource | `read`, `write`, `delete`, `share`, `admin` |

### Datenmodell (Delta)

```
roles             (id, name UNIQUE, description, permissions INT, scope[system|resource],
                   is_builtin BOOL, created_at)
role_assignments  (id, role_id, principal_type[user|group], principal_id,
                   entry_id NULL, created_at,
                   UNIQUE(role_id, principal_type, principal_id, entry_id))
acl               (+ role_id NULL – Abwärtskompatibilität zu rohen perms)
```

**Rechteberechnung (erweitert):** effektive Rechte = Besitzer-Vollrechte ∪ systemweite
Rollen ∪ Rollen/ACLs entlang des Pfades. Reihenfolge/Union bleibt additiv wie bisher.

### API

- `GET/POST /api/roles`, `GET/PATCH/DELETE /api/roles/{id}` (Builtin geschützt)
- `GET/POST /api/entries/{id}/roles`, `DELETE /api/entries/{id}/roles/{assignment_id}`
- `GET /api/roles/{id}/assignments`
- Kompatibel: bestehende `POST /api/entries/{id}/acl` bleibt.
- Systemrolle zuweisen: `PATCH /api/users/{id}` erweitert um `system_role` (z. B. `auditor`).

### UI

- Admin **„Rollen“**: Liste, Anlegen/Bearbeiten (Rechte als Checkboxen), Zuweisungen.
- Im Dateimanager: Aktion **„Zugriff verwalten“** – Rollen an Benutzer/Gruppen vergeben.

### Abnahmekriterien (Auszug)

- Zuweisung `viewer` auf einen Ordner erlaubt Lesen (inkl. Nachfahren), aber kein Schreiben.
- Eingebaute Rollen können nicht gelöscht werden (`409`).
- Bestehende ACLs funktionieren unverändert weiter.

---

## 4. Weitere Vorschläge

Priorisiert nach Nutzen/Aufwand. Empfehlung: mit den ersten vier nach den Kernfeatures
weiterarbeiten.

| # | Feature | Nutzen | Aufwand |
|---|---|---|---|
| V1 | **Versionshistorie & Rollback** | Frühere Dateiversionen ansehen/wiederherstellen (Datenmodell `versions` existiert) | mittel |
| V2 | **Serverseitige Suche & Filter** | Dateien nach Name/Typ/Größe/Datum finden | klein–mittel |
| V3 | **Bulk-Aktionen** | Mehrfachauswahl: Download, Verschieben, Löschen, Freigabe | klein–mittel |
| V4 | **Freigaben-Verwaltung** | Übersicht aller Links, widerrufen (auch mehrfach), Ablauf/Downloads einsehen | klein |
| V5 | **Audit-Log-Ansicht** (Admin) | Nachvollziehbarkeit in der UI statt nur API | klein |
| V6 | **Quota-Verwaltung** | Speicherlimits pro Nutzer/Gruppe setzen und anzeigen | mittel |
| V7 | **Tags/Metadaten** | Verschlagwortung und gefilterte Suche | mittel |
| V8 | **Datei-Vorschau** | Text/Code/Python, HTML, PDF, Bilder und Video direkt im Browser (über die eigenen Ressourcen des Browsers) | mittel |
| V9 | **Upload-Requests** ✅ umgesetzt | Dritte laden ohne Konto in einen geteilten Ordner hoch (`allow_upload`) | mittel |
| V10 | **Benachrichtigungen** | E-Mail/Webhook bei Upload/Freigabe | mittel |
| V11 | **2FA/MFA & OIDC/SSO** | Sichere Anmeldung, zentrale Identität | groß |
| V12 | **Monitoring-Dashboard** | Health/Metriken/Jobstatus in der UI | klein |

---

## 5. Umsetzungsreihenfolge

Jede Stufe wird vollständig umgesetzt: **Modell/Migration → API → UI → Tests → Doku → Deploy.**

| Stufe | Inhalt | Abhängigkeiten |
|---|---|---|
| **M1** | **Papierkorb** (Backend + UI) ✅ umgesetzt | Migration `trashed_by`/`original_parent_id` |
| **M2** | **Account-Manager** (Admin-Bereich + Selbstbedienung) ✅ umgesetzt | – |
| **M3** | **Rollenmanagement** (Modelle, Autorisierung, API, UI) ✅ umgesetzt | baut auf M2 (Benutzer/Gruppen-UI) auf |
| **M4** | **Bulk-Aktionen** ✅, **Freigaben-Verwaltung** ✅ (inkl. Mehrfach-Widerruf), **Audit-Ansicht** ✅; **Suche** offen | baut auf UI auf |
| **M5** | **Versionshistorie & Rollback** ✅ umgesetzt; **Quota**, **Tags** offen | – |
| **M6** | **Datei-Vorschau** ✅ umgesetzt; **Upload-Requests** ✅ umgesetzt (Ordner-Freigaben mit `allow_upload`); **Benachrichtigungen** offen | – |
| **M7** | **2FA/OIDC**, **Monitoring-Dashboard** | – |

**Begründung der Reihenfolge**

- Der **Papierkorb** ist in sich geschlossen, nutzt das vorhandene Soft-Delete und
  schließt eine akute Lücke (gelöschte Testdaten sind derzeit schwer wiederzufinden).
- Der **Account-Manager** liefert die Benutzer-/Gruppen-Oberfläche, auf der das
  **Rollenmanagement** unmittelbar aufsetzt.
- Rollen zuletzt von den drei Kernfeatures, weil sie das Autorisierungsmodell erweitern
  und daher am sorgfältigsten getestet werden müssen.

---

## 6. Querschnittsanforderungen

Für **jede** Stufe verbindlich:

- **Migration:** Alembic-Revision, abwärtskompatibel.
- **Autorisierung:** zentraler PDP (`core/authz.py`); Rechte serverseitig geprüft.
- **Audit:** jede Mutation erzeugt einen Audit-Eintrag (Akteur, Aktion, Ziel, Ergebnis).
- **API:** Pydantic-Schemas, einheitliche Fehler, `problem+json`, OpenAPI aktuell.
- **UI:** deutsche Texte, konsistentes Design, Fehler-Toasts, Ladezustände.
- **Tests:** Unit-Tests (Kern) + API-Tests (SQLite, S3 gemockt) + UI-Smoke; `ruff` sauber.
- **Doku:** Update von `README.md`, `docs/api-referenz.md`, `docs/datenmodell.md`,
  `docs/sicherheit.md` sowie ein passendes Tutorial.
- **Deployment:** Änderungen auf das Testsystem ausrollen und per Sync-Check abgleichen.

---

## 7. Entscheidungen und offene Punkte

**Entschieden:** Rollen ergänzen ACLs; Benutzer werden standardmäßig deaktiviert;
Reihenfolge M1 Papierkorb → M2 Account-Manager → M3 Rollenmanagement; prioritäre
Zusatzfeatures siehe Abschnitt 0.

**Noch offen:**

1. **Aufbewahrungsfrist** des Papierkorbs: global über `IFS_TRASH_RETENTION_DAYS`
   (Standard 30) – je Ordner konfigurierbar erst später.
2. **Systemrollen:** zunächst nur `admin`/`user`; eigenes `auditor`-Recht erst mit der
   Audit-Ansicht.
3. **Bulk-Download:** einzelne Dateien ja, ganzes Verzeichnis als ZIP (Streaming)
   erst in einer späteren Stufe.
4. **Vorschau:** Umfang der Typen (Text/Code, HTML, PDF, Bilder, Video) – Office-Formate
   über Konvertierung zunächst außen vor.
