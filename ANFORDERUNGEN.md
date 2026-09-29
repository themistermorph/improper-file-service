# Anforderungskatalog – Webbasiertes Dateisystem (IFS)

> Status: Entwurf / Diskussionsgrundlage
> Priorisierung nach RFC-2119-Konvention: **MUSS** = zwingend, **SOLLTE** = stark empfohlen, **KANN** = optional.

---

## 0. Festgelegte Rahmenbedingungen

| Thema | Entscheidung |
|---|---|
| Nutzerkreis | Internes Team / kleine Gruppe (kein Multi-Tenant, keine öffentliche Plattform) |
| Speicher-Backend | S3-kompatibler Objektspeicher |
| FTP | **Nur FTPS** (explizit/implicit TLS), Plain-FTP wird nicht unterstützt |
| Projektstand | Grüne Wiese, kein Altsystem zur Migration |

**Konsequenzen für den Anforderungsumfang**

- Mandantenfähigkeit, Georedundanz und horizontale Massenskalierung entfallen (bzw. nur als
  spätere Option). Ein einzelner Applikations- und FTPS-Dienst genügt dem Zielbild.
- S3-kompatibles Backend ist gesetzt: Metadaten und Struktur liegen getrennt von den Objekten.
- Da Plain-FTP entfällt, müssen alle FTP-Clients TLS beherrschen (heute Standard bei
  FileZilla, WinSCP, lftp, curl). Nicht-TLS-fähige Altgeräte sind ein Restrisiko und
  ggf. über WebDAV oder ein Upload-Formular abzufangen.
- DSGVO/Compliance bleibt relevant, aber vereinfacht (kein öffentlicher Datenverkehr,
  klarer interner Verantwortungsbereich).

---

## 1. Kontext und Zielsetzung

Ein zentrales Dateisystem, das seine Inhalte über das Web zugänglich macht. Nutzer greifen
mit unterschiedlichen Werkzeugen (Browser, CLI, FTP-Clients, Scanner/Multifunktionsgeräte,
Skripte) auf **denselben** Datenbestand zu. Es entsteht keine zweite Datenhaltung neben dem
Web-Zugriff: alle Zugriffswege bedienen ein gemeinsames Backend.

**Leitprinzipien**

- *Single Source of Truth*: Dateien, Metadaten und Berechtigungen existieren genau einmal.
- *Protokollunabhängige Rechte*: Dieselben Rechte gelten für Web-UI, HTTP-API und FTP.
- *Interoperabilität*: Standard-Clients müssen ohne Spezialsoftware funktionieren.
- *Sicherheit by Default*: Transport immer verschlüsselt, kein anonymer Schreibzugriff.

**Typische Nutzerrollen**

| Rolle | Beschreibung |
|---|---|
| Gast / anonymer Leser (optional) | Nur explizit freigegebene Inhalte, read-only |
| Endnutzer | Eigene und freigegebene Dateien, up-/download |
| Power-User / Automat | API-Keys, Skripte, Bulk-Transfers |
| Team-/Gruppenadmin | Verwaltet Freigaben einer Gruppe |
| Systemadministrator | Benutzer, Quotas, Speicher, Policies |
| Auditor / Compliance | Nur-Lesezugriff auf Logs und Metadaten |

**Abgrenzung (nicht Teil des Systems)**

- Kein Ersatz für ein vollwertiges Groupware-/Collaboration-Suite (Chat, Kalender).
- Keine Ende-zu-Ende-Verschlüsselung im ersten Ausbauschritt (siehe offene Entscheidungen).
- Kein klassisches Block-Storage (iSCSI/NFS als Ersatz) – wohl aber optionales NFS/SMB-Export in späterer Stufe.

---

## 2. Funktionale Anforderungen

### F1 – Datei- und Verzeichnisverwaltung

| ID | Anforderung | Prio |
|---|---|---|
| F1.1 | Verzeichnisse anlegen, umbenennen, verschieben, kopieren, löschen | MUSS |
| F1.2 | Dateien anlegen (leer), umbenennen, verschieben, kopieren, überschreiben, löschen | MUSS |
| F1.3 | Hierarchische Pfade beliebiger Tiefe | MUSS |
| F1.4 | Mehrfachauswahl / Bulk-Operationen | SOLLTE |
| F1.5 | Dateinamen mit Unicode (UTF-8), Leerzeichen, Umlauten, Sonderzeichen | MUSS |
| F1.6 | Reservierte/verbotene Zeichen pro Zielplattform filtern, Pfadlängen-Limits beachten | MUSS |
| F1.7 | Pfad-Traversal (`../`, absolute Pfade, Symlink-Escape) verhindern | MUSS |
| F1.8 | Atomare Operationen (kein halb sichtbarer Zustand bei Upload/Move) | MUSS |
| F1.9 | Papierkorb mit Wiederherstellung | SOLLTE |
| F1.10 | Versionierung von Dateien (Historie, Rollback) | KANN |

### F2 – Up- und Download über HTTP(S)

| ID | Anforderung | Prio |
|---|---|---|
| F2.1 | Download einzelner Dateien per `GET` | MUSS |
| F2.2 | Streaming ohne vollständiges Puffern im RAM (auch bei mehreren GB) | MUSS |
| F2.3 | **Resumable Download** via `Range`-Requests (`206 Partial Content`) | MUSS |
| F2.4 | Korrekte Header: `Content-Length`, `Content-Type`, `ETag`, `Last-Modified`, `Accept-Ranges` | MUSS |
| F2.5 | `HEAD`-Support (Größe, Typ, Existenz prüfen ohne Download) | MUSS |
| F2.6 | **Resumable/chunked Upload** (z. B. tus, S3-Multipart-Alternative, oder `Content-Range`-PUT) | SOLLTE |
| F2.7 | Upload per `PUT` (Rohbody) und `POST multipart/form-data` | MUSS |
| F2.8 | `Content-Disposition` steuerbar (Inline-Anzeige vs. Download) | SOLLTE |
| F2.9 | Shortcuts: Harte/weiche Links, symbolische Links (kontrolliert) | KANN |
| F2.10 | Download ganzer Verzeichnisse als ZIP/TAR (on the fly, streamend) | SOLLTE |
| F2.11 | Übertragungsabbrüche sind idempotent wiederholbar, kein Datenverlust/Doppelanlage | MUSS |
| F2.12 | Integritätsprüfung per `Content-MD5` / `Digest` oder SHA-256-Header | SOLLTE |
| F2.13 | CORS-Konfiguration für programmatische Browser-Zugriffe | SOLLTE |
| F2.14 | Cache-Control/ETag-basierte `304 Not Modified`-Antworten | SOLLTE |

### F3 – Up- und Download über FTP(S)

| ID | Anforderung | Prio |
|---|---|---|
| F3.1 | FTP-Zugang (Login) gegen **denselben** Benutzer-/Rechtebestand wie HTTP | MUSS |
| F3.2 | **FTPS** (explizites AUTH TLS) verpflichtend; implizites FTPS unterstützen | MUSS |
| F3.3 | Plain-FTP ist **nicht** unterstützt; Klartext-Login/-Transfer wird abgewiesen | MUSS |
| F3.4 | Passive-Mode (`PASV`/`EPSV`) für NAT-/Firewall-Umgebungen | MUSS |
| F3.5 | Active-Mode (`PORT`/`EPRT`) als Option | SOLLTE |
| F3.6 | Binär- (`TYPE I`) und ASCII-Modus (`TYPE A`) | MUSS |
| F3.7 | Directory-Listing `LIST`, `NLST` und maschinenlesbar `MLSD`/`MLST` | MUSS |
| F3.8 | Verzeichnisse: `CWD`, `CDUP`, `MKD`, `RMD`, `PWD` | MUSS |
| F3.9 | `SIZE`, `MDTM` (Zeitstempel) für Client-Sync | MUSS |
| F3.10 | Resumable: `REST` für Download steuern, `APPE` zum Anhängen | SOLLTE |
| F3.11 | Umbenennen/Löschen: `RNFR`/`RNTO`, `DELE` | MUSS |
| F3.12 | `FEAT` meldet unterstützte Erweiterungen korrekt | SOLLTE |
| F3.13 | Optional: SFTP (SSH-basiert) als zusätzlicher sicherer Zugang | KANN |
| F3.14 | FTP-Sessions erhalten kein direkter Dateisystemzugriff – nur über Rechte-Schicht | MUSS |
| F3.15 | Protokollierung von FTP-Logins, CWDs und Transfers (Audit) | MUSS |
| F3.16 | Gleichzeitige Sessions pro Benutzer / Verbindungslimits konfigurierbar | SOLLTE |

> Hinweis: FTP ist ein reines Transferprotokoll. Es kennt keine Metadaten-Felder, keine
> Versionierung und keine Link-Freigaben. Diese Funktionen bleiben der Web-UI/API vorbehalten,
> FTP ordnet sich der Rechte- und Namenslogik des Systems unter.

### F4 – Weitere Zugriffswege

| ID | Anforderung | Prio |
|---|---|---|
| F4.1 | Web-UI zum Browsen, Suchen, Vorschau, Sharen | MUSS |
| F4.2 | REST-API (JSON) für alle Kernoperationen | MUSS |
| F4.3 | WebDAV (PROPFIND, MKCOL, PUT, GET, MOVE, COPY, DELETE, LOCK/UNLOCK) | SOLLTE |
| F4.4 | CLI/SDK für Automatisierung und Skripte | SOLLTE |
| F4.5 | Webhooks/Event-Stream bei Änderungen | KANN |

### F5 – Metadaten und Suche

| ID | Anforderung | Prio |
|---|---|---|
| F5.1 | System-Metadaten: Größe, Typ, MIME, Zeitstempel, Hash, Besitzer | MUSS |
| F5.2 | Benutzerdefinierte Eigenschaften/Tags pro Datei | SOLLTE |
| F5.3 | Volltextsuche in Dateinamen | MUSS |
| F5.4 | Volltextsuche in Dateiinhalten (PDF, Office, Text), extrahiertes OCR | KANN |
| F5.5 | Filter nach Typ, Datum, Größe, Tag, Besitzer | SOLLTE |

### F6 – Freigaben und Links

| ID | Anforderung | Prio |
|---|---|---|
| F6.1 | Internes Teilen mit Nutzern/Gruppen (read/write) | MUSS |
| F6.2 | Externe Links mit Ablaufdatum, Passwort, Download-Limit | SOLLTE |
| F6.3 | Upload-Requests (Dritte laden hoch, ohne Konto) | KANN |
| F6.4 | Widerrufbare Links, Änderungshistorie pro Freigabe | SOLLTE |

### F7 – Benutzer, Gruppen, Rechte

| ID | Anforderung | Prio |
|---|---|---|
| F7.1 | Lokale Benutzerverwaltung + Anbindung LDAP/AD | MUSS |
| F7.2 | OIDC/OAuth2-SSO für Web/API | SOLLTE |
| F7.3 | Gruppen und Vererbung von Rechten entlang der Verzeichnisstruktur | MUSS |
| F7.4 | Rechtemodell: read, write, delete, share, admin (feingranular) | MUSS |
| F7.5 | API-Keys / Personal Access Tokens mit Scopes | SOLLTE |
| F7.6 | MFA/2FA für privilegierte Konten | SOLLTE |
| F7.7 | Rechte gelten identisch über HTTP(S) und FTP | MUSS |

### F8 – Quotas, Limits, Policies

| ID | Anforderung | Prio |
|---|---|---|
| F8.1 | Speicher-Quota pro Nutzer/Gruppe | MUSS |
| F8.2 | Maximale Dateigröße und Dateianzahl konfigurierbar | SOLLTE |
| F8.3 | Bandbreiten-Drosselung und Rate-Limits | SOLLTE |
| F8.4 | Dateityp-Whitelist/Blacklist | KANN |

### F9 – Administration und Betriebsfunktionen

| ID | Anforderung | Prio |
|---|---|---|
| F9.1 | Admin-Konsole (Nutzer, Rechte, Quotas, Policies) | MUSS |
| F9.2 | Audit-Log aller relevanten Aktionen, fälschungssicher | MUSS |
| F9.3 | Integritäts-/Konsistenzprüfung des Speichers | SOLLTE |
| F9.4 | Backup-/Restore-Konzept und Export | MUSS |
| F9.5 | Monitoring-Endpunkt (Health, Metriken) | SOLLTE |

---

## 3. Nichtfunktionale Anforderungen

### N1 – Sicherheit

| ID | Anforderung | Prio |
|---|---|---|
| N1.1 | TLS verpflichtend für HTTP(S) und FTPS; TLS ≥ 1.2, moderne Cipher-Suiten | MUSS |
| N1.2 | Passwörter mit starkem Hash (Argon2id/bcrypt), niemals im Log | MUSS |
| N1.3 | Schutz vor Pfad-Traversal, Injection, XSS, CSRF, SSRF | MUSS |
| N1.4 | Schutz vor Brute-Force (Lockout, Backoff), Session-Timeout | MUSS |
| N1.5 | Rechteprüfung serverseitig bei **jedem** Zugriff, nie nur clientseitig | MUSS |
| N1.6 | Viren-/Malware-Scan von Uploads (asynchron oder inline) | SOLLTE |
| N1.7 | Sichere Defaults: keine anonymen Schreibrechte, kein Plain-FTP | MUSS |
| N1.8 | Verschlüsselung ruhender Daten (Storage) | SOLLTE |
| N1.9 | Secret-Rotation, Trennung von Konfigurations-Secrets | SOLLTE |

### N2 – Performance und Skalierbarkeit

| ID | Anforderung | Prio |
|---|---|---|
| N2.1 | Große Dateien (≥ 4 GB) ohne 32-Bit-Grenzenproblem | MUSS |
| N2.2 | Hohe Parallelität (viele gleichzeitige Transfers) | MUSS |
| N2.3 | S3-kompatibles Objektspeicher-Backend (gesetzt) | MUSS |
| N2.4 | Vertikale Skalierung des einzelnen Dienstes genügt dem Zielbild | MUSS |
| N2.5 | Horizontale Skalierung / Cluster | KANN |
| N2.6 | Definierte Mindest-Durchsätze pro Transfer | SOLLTE |

### N3 – Verfügbarkeit und Robustheit

| ID | Anforderung | Prio |
|---|---|---|
| N3.1 | Neustart eines Protokoll-Gateways unterbricht andere nicht | SOLLTE |
| N3.2 | Wiederaufsetzbare Transfers nach Verbindungsabbruch | MUSS |
| N3.3 | Backups ohne Downtime (snapshotbasiert) | MUSS |
| N3.4 | Definierte RPO/RTO (noch festzulegen) | MUSS |

### N4 – Bedienbarkeit und Barrierefreiheit

| ID | Anforderung | Prio |
|---|---|---|
| N4.1 | Responsive Web-UI, funktioniert in gängigen Browsern | MUSS |
| N4.2 | Drag-&-Drop-Upload mit Fortschritt und Fehleranzeige | SOLLTE |
| N4.3 | Barrierefreiheit/BITV 2.0 (für öffentliche Stellen relevant) | KANN |

### N5 – Interoperabilität und Kompatibilität

| ID | Anforderung | Prio |
|---|---|---|
| N5.1 | Standard-HTTP-Clients (curl, wget, Browser) funktionieren ohne Anpassung | MUSS |
| N5.2 | Standard-FTP-Clients (FileZilla, WinSCP, curl, lftp) funktionieren | MUSS |
| N5.3 | Multifunktionsdrucker/Scanner (FTP-Upload, SMB optional) | SOLLTE |
| N5.4 | Fallende Kompatibilität zu Windows-/macOS-/Linux-Clients | MUSS |

### N6 – Wartbarkeit

| ID | Anforderung | Prio |
|---|---|---|
| N6.1 | Klare Trennung: Protokoll-Gateway ↔ Kern-Domain ↔ Speicher | MUSS |
| N6.2 | Konfiguration versionierbar (Infrastructure as Code) | SOLLTE |
| N6.3 | Automatisierte Tests inkl. Protokoll-Konformität | SOLLTE |
| N6.4 | Dokumentation von API, Betrieb und Notfällen | MUSS |

### N7 – Recht und Compliance

| ID | Anforderung | Prio |
|---|---|---|
| N7.1 | DSGVO-Konformität (Löschkonzept, Auskunft, Zweckbindung) | MUSS |
| N7.2 | Aufbewahrungsfristen und Löschkonzept | SOLLTE |
| N7.3 | Audit-Trail für nachweispflichtige Prozesse | SOLLTE |
| N7.4 | Lizenz-Compliance eingesetzter Komponenten | MUSS |

---

## 4. Schnittstellen-Übersicht

| Kanal | Zweck | Auth | Verschlüsselung | Status |
|---|---|---|---|---|
| Web-UI (HTTPS) | Komfortabler menschlicher Zugriff | Session/OIDC | TLS | Pflicht |
| REST-API (HTTPS) | Automatisierung, Integration | Bearer/API-Key/OIDC | TLS | Pflicht |
| WebDAV (HTTPS) | OS-Einbindung, Clients | Basic/Bearer | TLS | empfohlen |
| FTPS (AUTH TLS) | Legacy-Clients, Scanner, Bulk | FTP-Login | TLS | Pflicht |
| FTP (plain) | – | – | keine | **nicht unterstützt** |
| SFTP (SSH) | sicherer Datei-Transfer | SSH-Key/Passwort | SSH | optional |
| SMB/NFS | Netzwerk-Mounts | Systemabhängig | variabel | Ausbaustufe |

**Externe Anbindungen:** LDAP/AD, OIDC-Provider, S3-Objektspeicher, Virenscanner,
Benachrichtigungsdienst (E-Mail/Webhook), Monitoring.

---

## 5. Architektur-Eckpunkte

```
                   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 Clients ─────────►│ HTTP(S)-Edge │  │  FTP(S)-Svc  │  │ WebDAV/API   │
 (Browser, curl,   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
  Scanner, Scripts)       │                 │                 │
                          └────────┬────────┴────────┬────────┘
                                   ▼
                        ┌───────────────────────┐
                        │   Kern-Domain/Core    │
                        │  Auth · Rechte · Meta │
                        │  Quota · Events       │
                        └─────┬──────────┬──────┘
                              ▼          ▼
                    ┌──────────────┐ ┌────────────────┐
                    │ Metadaten-DB │ │ Objektspeicher │
                    │ (Postgres)   │ │ (S3-kompatibel)│
                    └──────────────┘ └────────────────┘
```

**Kernaussagen**

1. **Ein gemeinsamer Kern** für Authentifizierung, Autorisierung, Metadaten und Quota.
   Die Protokolle (HTTP, FTP, WebDAV) sind reine Adapter/Skins darüber.
2. **Zero-Copy-Transfers** wo möglich: der Gateway streamt direkt zum/vom Speicher,
   ohne große Dateien durch den Anwendungsspeicher zu ziehen.
3. **Objektspeicher + Metadaten-DB** trennen Inhalt von Struktur; ermöglicht Versionierung,
   Skalierung und Remote-Storage.
4. **Idempotente, wiederaufsetzbare Transfers** als durchgängiges Muster (Range, tus, REST/APPE).
5. **Rechteprüfung im Kern**, nicht im Protokolladapter – damit FTP und HTTP nie divergieren.

---

## 6. Protokoll-Detailprofil (Kernoperationen)

### HTTP(S) – Kernoperationen

| Operation | Methode | Erfolg | Wiederaufsetzbar |
|---|---|---|---|
| Datei lesen | `GET` | `200` / `206` | ja (`Range`) |
| Datei prüfen | `HEAD` | `200` | – |
| Datei schreiben | `PUT` | `201` / `204` | ja (`Content-Range`) |
| Upload (Formular) | `POST` | `201` | eingeschränkt |
| Chunked Upload | `PATCH`/`POST` (tus) | `204` | ja |
| Löschen | `DELETE` | `204` | – |
| Umbenennen/Verschieben | `MOVE`/`PATCH` | `200`/`201` | – |
| Verzeichnis listet | `GET` (API) / `PROPFIND` | `200` / `207` | – |
| Verzeichnis anlegen | `MKCOL`/`POST` | `201` | – |

### FTP(S) – Kernoperationen

| Operation | Kommando | Anmerkung |
|---|---|---|
| Login | `USER` / `PASS` | gegen Kern-Auth |
| TLS aushandeln | `AUTH TLS`, `PBSZ`, `PROT` | FTPS Pflicht |
| Listing | `LIST` / `NLST` / `MLSD` | MLSD maschinenlesbar |
| Download | `RETR` | Resume via `REST` |
| Upload | `STOR` / `APPE` | APPE zum Anhängen |
| Größe/Zeit | `SIZE` / `MDTM` | Sync-Client-Unterstützung |
| Verzeichnis | `CWD`/`PWD`/`MKD`/`RMD` | – |
| Umbenennen/Löschen | `RNFR`/`RNTO`, `DELE` | – |
| Passive | `PASV` / `EPSV` | NAT-Freundlichkeit |
| Features | `FEAT` / `OPTS UTF8 ON` | Interop |

---

## 7. MVP vs. Ausbaustufen

**MVP (Stufe 1)**
- Web-UI + REST-API
- HTTP(S) Download/Upload inkl. Range und Resumable Upload
- FTPS (explizit) mit derselben Rechteprüfung; Plain-FTP abgewiesen
- S3-kompatibles Objektspeicher-Backend + Metadaten-DB
- Benutzer/Gruppen lokal, Basis-Rechte, Quota
- Audit-Log, Backup, Monitoring-Health
- Große Dateien, Unicode, Pfad-Traversal-Schutz

**Ausbau (Stufe 2)**
- WebDAV, externe Freigabelinks, Versionierung, Papierkorb
- LDAP/AD-Anbindung und OIDC/SSO, API-Keys, MFA
- Volltextsuche, Tags, Vorschau/OCR, Virenscan

**Später (Stufe 3)**
- SFTP als zusätzlicher Zugang
- Webhooks/Event-Stream, Client-Sync-Agent
- Horizontale Skalierung, Georedundanz

---

## 8. Entscheidungen und offene Punkte

### 8.1 Bereits entschieden

| # | Entscheidung |
|---|---|
| E1 | Nutzerkreis: internes Team / kleine Gruppe – kein Multi-Tenant |
| E2 | Speicher: S3-kompatibler Objektspeicher (gesetzt) |
| E3 | Plain-FTP wird nicht unterstützt – ausschließlich FTPS |
| E4 | Grüne Wiese, keine Migration eines Altsystems |

### 8.2 Noch offen

1. **Verschlüsselung ruhender Daten**: server-/storage-seitig oder clientseitig (Zero-Knowledge)?
2. **Technologie-Stack** und vorhandene Infrastruktur (Auth, Monitoring, S3-Provider, Deployment).
3. **Betriebsmodell**: On-Prem, Container/Kubernetes, oder gehostet?
4. **RPO/RTO und Backup-Ziel** konkretisieren.
5. **Compliance-Rahmen**: DSGVO-Umfang, ggf. interne Vorgaben/BITV nur bei Bedarf.
6. **Kollisions-/Sperrverhalten** bei gleichzeitigen Schreibzugriffen über verschiedene Protokolle.
7. **Umgang mit nicht-TLS-fähigen Altgeräten** (Scanner): WebDAV, Upload-Formular oder Verzicht?

### 8.3 Risiken

- FTP-Clients ohne TLS-Unterstützung können nicht angebunden werden (durch E3 bewusst).
- S3-Objektspeicher bringt Eventual-Consistency-/Locking-Eigenheiten mit – Verhalten bei
  gleichzeitigen Schreibzugriffen und bei Umbenennungen muss definiert werden.

---

## 9. Annahmen

- Es existiert kein Altsystem, das migriert werden muss.
- Zielplattform ist Web/Cloud; Clients sind Browser, CLI und FTPS-fähige Geräte.
- Ein zentrales (kleines) Team betreibt und wartet das System.
- Alle FTP-Clients beherrschen FTPS (TLS).
