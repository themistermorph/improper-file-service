# Sicherheitskonzept

Dieses Dokument beschreibt die Sicherheitsentscheidungen von IFS, das Bedrohungsmodell
und die Härtungsmaßnahmen im Betrieb.

---

## 1. Schutzziele

| Ziel | Umsetzung |
|---|---|
| **Vertraulichkeit** | TLS überall (HTTPS/FTPS), Rechteprüfung serverseitig, optionale Verschlüsselung ruhender Daten || **Integrität** | SHA-256 je Blob, content-addressed Storage, atomare Versionierung |
| **Verfügbarkeit** | Backups (Postgres + S3), stateless Protokolladapter, Worker entkoppelt |
| **Nachvollziehbarkeit** | Append-only Audit-Log für Auth- und Mutationsereignisse |
| **Minimalrechte** | Deny by default, Vererbung über Gruppen/Ordner |

---

## 2. Bedrohungsmodell

| Bedrohung | Gegenmaßnahme |
|---|---|
| Unbefugter Zugriff | Authentifizierung (JWT/FTP-Login), zentrale Autorisierung |
| Rechteausweitung über Protokolle | HTTP und FTPS nutzen **denselben** Policy-Entscheider |
| Pfad-Traversal (`../`) | ausschließlich virtueller Namespace; `..` wird geklammert/abgelehnt |
| Abgreifen von Credentials | TLS erzwingen, Argon2id-Hashes, keine Secrets in Logs |
| Datenabgriff über Freigaben | Ablauf, Passwort, Downloadlimit, Widerruf |
| Malware-Uploads | Integritätsprüfung; Virenscan als Ausbau vorgesehen |
| Brute-Force | Audit + Backoff-Konzept; Token mit kurzer Lebensdauer |
| Denial of Service | Größenlimits (`IFS_MAX_UPLOAD_SIZE`), Passive-Range begrenzen |

---

## 3. Transport

- **HTTPS** für Web-UI und REST-API; TLS-Terminierung am Ingress (Traefik/Caddy/nginx).
- **FTPS** mit explizitem `AUTH TLS`, Steuer- und Datenkanal verschlüsselt
  (`tls_control_required`, `tls_data_required`).
- **Kein Plain-FTP.** Klartext-Verbindungen werden nicht angeboten.
- TLS ≥ 1.2, moderne Cipher-Suiten (Ingress-/Server-Defaults).

> FTPS ist **kein HTTP** und lässt sich nicht über einen HTTP-Reverse-Proxy
> terminieren. Der Dienst wird per TCP exponiert; Port und Passive-Range sind zu
> schützen.

---

## 4. Authentifizierung

| Kanal | Verfahren |
|---|---|
| Web/API | Benutzername + Passwort → JWT-Bearer-Token |
| FTPS | FTP-Login gegen denselben Benutzerbestand (über TLS) |

- Passwörter mit **Argon2id** gehasht, niemals im Klartext gespeichert oder geloggt.
- Tokens signiert (HMAC) mit `IFS_JWT_SECRET`, Gültigkeit über
  `IFS_ACCESS_TOKEN_TTL_SECONDS`.
- Fehlgeschlagene Logins werden als `auth.login.failed` auditiert.
- Benutzer sind deaktivierbar (`is_active`).
- **Token-Invalidierung:** Bei Passwortänderung, Admin-Reset und Deaktivierung wird die
  `token_version` erhöht; alle zuvor ausgestellten Tokens sind sofort ungültig.
- **Token-Zweck:** Access-Tokens tragen `purpose=access`. Vorschau- und Archiv-Tokens
  haben einen eigenen Zweck und werden als Bearer-Token **abgelehnt** – ein geleakter
  Vorschau-/Download-Link ist damit nicht als Sitzungstoken nutzbar.
- **Brute-Force-Schutz:** Fehlversuche werden pro Konto und pro IP gezählt; nach
  `IFS_LOGIN_MAX_ATTEMPTS` greift eine Backoff-Sperre (HTTP `429` mit `Retry-After`,
  FTP `530`).
- **Keine Benutzer-Enumeration:** Auch bei unbekanntem Benutzernamen wird ein
  Argon2-Hash-Vergleich ausgeführt – die Antwortzeit verrät nicht, ob ein Konto existiert.
- **Passwortrichtlinie:** Mindestlänge 8 Zeichen (Selbständerung und Admin-Reset).
- **Letzter-Admin-Schutz:** Der letzte aktive Administrator kann weder deaktiviert noch
  gelöscht werden (`409`).

**Härtungsempfehlungen:** langes, zufälliges `IFS_JWT_SECRET`; starke Passwörter;
regelmäßige Rotation; in Ausbaustufe OIDC/SSO und MFA für Admins.

---

## 5. Autorisierung

Zentrales Modul `src/ifs/core/authz.py` (Policy Decision Point). Beide Adapter rufen es
auf.

- **Deny by default**: ohne passendes Recht wird abgelehnt.
- **Besitzer** eines Eintrags hat implizit alle Rechte.
- **Per-User-Wurzel**: Jeder Benutzer besitzt ein eigenes Home (`parent_id IS NULL`,
  `owner_id = Benutzer`). Systemadmins haben **keinen** Dateizugriff auf fremde Wurzeln;
  systemweite Rollen (auch `admin`) gewähren nur Systemfunktionen, keine Dateirechte.
  Zugriff entsteht ausschließlich über Besitz, ACLs und Ressourcenrollen, dadurch sind die
  Homes voneinander isoliert.
- **Admin-Grenzen:** Auch die API-Pfade sind so begrenzt, dass Admins die Isolation nicht
  aushebeln können: Ressourcenrollen dürfen nur mit `admin` am Eintrag vergeben werden,
  fremde Freigaben dürfen Admins nur **widerrufen**, nicht ändern. Bewusste Break-Glass-
  Funktionen sind der Widerruf fremder Links und `delete_user(..., transfer_to)`
  (Besitzübernahme beim Löschen eines Kontos).
- **Vererbung**: Rechte werden additiv entlang des Pfades gesammelt; ein Recht auf einem
  Ordner gilt für alle Nachfahren.
- **Bitmaske**: `read=1`, `write=2`, `delete=4`, `share=8`, `admin=16`.
- **Rollen:** benannte Rechtebündel – systemweit (`admin`, `auditor`, `user`, nur
  Systemfunktionen) oder pro Eintrag (`viewer`, `collaborator`, `editor`, `sharer`,
  `manager`). Ressourcenrollen **ergänzen** die ACLs.
- Die Prüfung erfolgt **serverseitig bei jeder Operation**, auch bei FTP.

---

## 6. Virtuelles Dateisystem & Pfadsicherheit

- Es gibt **keine** OS-Pfade. Pfade sind virtuelle Posix-Pfade.
- `..` wird normalisiert bzw. am Wurzelknoten geklammert; `normalize_path` lehnt
  Traversal ab.
- Keine symbolischen Links, kein `chmod`, kein direkter Dateisystemzugriff aus FTP.
- S3-Schlüssel enthalten keine Nutzereingaben und keine Hierarchie.
- **Header-Sicherheit:** Dateinamen werden auf Steuerzeichen (inkl. CR/LF) geprüft;
  `Content-Disposition` wird RFC-6266-konform kodiert (`filename*`) und MIME-Typen werden
  gegen ein striktes Muster validiert und auf Kleinschreibung normalisiert – keine
  Header-/Zeilen-Injection über Namen oder MIME, keine Case-Umgehung der Aktiv-Inhalts-Härtung.

---

## 7. Upload-Sicherheit

1. **Größenlimit** über `IFS_MAX_UPLOAD_SIZE`; wird **vor** dem Schreiben geprüft
   (`Content-Length`/angekündigte Größe bzw. je Chunk) – Antwort `413`, ohne dass
   über-limitäre Bytes auf der Platte landen. Im FTP-Pfad bricht der Transfer beim
   Überschreiten ab; zusätzlich wird vor dem S3-Upload die finale Spool-Größe geprüft.
2. **Gesamt-Quota** über `IFS_DEFAULT_QUOTA_BYTES` pro Besitzer, geprüft bei jedem Schreiben.
3. SHA-256 kann vom Client mitgegeben und serverseitig geprüft werden.
4. Resumable Sessions spoolen in ein dediziertes Verzeichnis; abgelaufene Sessions
   räumt der Worker ab.
5. Der Eintrag wird erst nach erfolgreicher Integritätsprüfung sichtbar (atomare
   Metadaten-Transaktion).
6. **Ausbaustufe:** Virenscan (ClamAV) vor Freigabe über die Event-Outbox.

---

## 8. FTP-spezifische Härtung

| Maßnahme | Beschreibung |
|---|---|
| TLS erzwungen | `AUTH TLS` Pflicht, `PROT P` |
| Passive-Ports begrenzt | `IFS_FTP_PASSIVE_PORTS` eng fassen |
| PORT-Bounce | pyftpdlib validiert Active-Mode-Ziele |
| Masquerade | korrekte externe Adresse, keine internen IPs preisgeben |
| Rechteprüfung | je Kommando über den Kern |
| Audit | Login/CWD/Transfer protokolliert |

---

## 9. Geheimnisse und Verschlüsselung ruhender Daten

- **Secrets** (JWT, DB, S3) über Umgebungsvariablen oder Secret-Manager; nicht ins
  Repository.
- **Erzwungene Härtung:** In `IFS_ENVIRONMENT=prod` verweigert der Start bei schwachem
  `IFS_JWT_SECRET` (< 32 Zeichen), `IFS_ADMIN_PASSWORD` (zu kurz / `admin`) oder
  `IFS_S3_SECRET_KEY` (Default). Ohne gesetztes `IFS_ADMIN_PASSWORD` wird **kein**
  Default-Admin angelegt (statt `admin`/`admin`).
- **S3**: serverseitige Verschlüsselung über `IFS_S3_SSE` (`AES256` oder `aws:kms`).
- **PostgreSQL**: verschlüsselte Volumes bzw. Managed-Verschlüsselung.
- **TLS-Zertifikate**: geschützte Ablage, kontrollierte Erneuerung.

---

## 10. Audit

- Tabelle `audit_log`, append-only, Schreibzugriff nur über den Core.
- Erfasst u. a.: `auth.login`, `auth.login.failed`, `folder.create`, `entry.rename`,
  `entry.move`, `entry.copy`, `entry.delete`, `transfer.download`, `upload.*`,
  `share.*`.
- Felder: Akteur, Aktion, Ziel, Protokoll (`http`/`ftp`), IP, Ergebnis, Details.
- Abruf: `GET /api/audit` (Admin).

---

## 11. Bekannte Lücken / Ausbaustufen

- Kein MFA, kein OIDC/SSO (in der Architektur vorgesehen).
- Kein Virenscan und keine Inhaltsprüfung.
- Keine Ende-zu-Ende-Verschlüsselung (Server sieht Klartext).
- Keine Bandbreiten-Drosselung; Login-Rate-Limit ist vorhanden, allgemeine Request-Limits nicht.
- Presigned-URLs sind implementiert, aber im Standardpfad nicht aktiv.
- **Listing:** Wer `read` auf einem Ordner hat, sieht die **Namen** aller enthaltenen
  Einträge. Der Zugriff auf Inhalte/Metadaten wird weiterhin je Eintrag geprüft; ein
  feingranulares Filtern des Listings nach Rechten ist eine geplante Härtung.
- ACLs lassen sich über die API setzen, aber (noch) nicht einzeln löschen.

## 12. Härtungs-Checkliste für den Betrieb

- [ ] `IFS_JWT_SECRET` und Admin-Passwort geändert
- [ ] HTTPS mit gültigem Zertifikat, TLS ≥ 1.2
- [ ] FTPS-Zertifikat gültig, Passive-Range beschränkt und per Firewall geschützt
- [ ] `IFS_AUTO_CREATE_SCHEMA=false`, Migrationen im Einsatz
- [ ] `IFS_S3_SSE` aktiv, DB-Volume verschlüsselt
- [ ] Secrets nicht im Klartext im Repository
- [ ] Backups getestet (Restore-Probe)
- [ ] Audit-Log gesichert und zugriffsgeschützt
- [ ] Admin-Konten eingeschränkt, Benutzer deaktivierbar

Weiter: [Betrieb](betrieb.md) · [Konfiguration](konfiguration.md)

---

## 13. Nachträgliche Härtungsmaßnahmen

- **Proxy-Vertrauen:** `X-Forwarded-For` wird nur von konfigurierten
  `IFS_TRUSTED_PROXIES` ausgewertet – die Audit-IP ist nicht mehr fälschbar.
- **Info-Endpunkte:** `/docs`, `/redoc`, `/openapi.json` sind standardmäßig deaktiviert
  (`IFS_EXPOSE_DOCS`); `/metrics` erfordert Adminrechte (`IFS_METRICS_PUBLIC` als Opt-in);
  `/healthz` liefert keine internen Zähler mehr.
- **Robuste Eingaben:** Ungültige `Range`-/`Upload-Offset`-/Pfadwerte liefern `400`/`416`
  statt `500` (zusätzlicher `ValueError`-Handler).
- **Plausibilitätsprüfungen:** ACL- und Rollen-Zuweisungen prüfen die Existenz des
  Principals; `copy` verhindert Kopien eines Ordners in sich selbst.
- **Freigaben:** Werden bei Deaktivierung/Löschung des Erstellers automatisch widerrufen;
  Tokens erscheinen nicht mehr im Audit-Log und werden in Access-Logs redigiert.
- **FTP:** Lese-/Metadatenoperationen prüfen zusätzlich serverseitig die Rechte
  (Defense-in-Depth).
- **Deploy-Tooling:** Host-Key-Prüfung (RejectPolicy, `IFS_DEPLOY_HOSTKEY_POLICY=auto` als
  Opt-in), Key-Auth empfohlen; Zugangsdaten nur aus Umgebungsvariablen.
- **Frontend:** Security-Header (CSP, `X-Frame-Options`, `Referrer-Policy`,
  `X-Content-Type-Options`, HSTS bei HTTPS); Token im `sessionStorage` statt `localStorage`
  (verbleibendes XSS-Risiko dokumentiert).

---

## 14. Maßnahmen aus dem Sicherheits-Review

- **H1 – Papierkorb-Wiederherstellung:** Nur **direkt** gelöschte (oberste) Einträge sind
  wiederherstellbar; zusätzlich ist `write` auf das **Zielverzeichnis** erforderlich. Das
  ungefragte Reaktivieren fremder Vorfahren entfällt (kein `_ensure_live_chain` mehr).
- **M1 – Aktive Inhalte:** `text/html`, `application/xhtml+xml`, `image/svg+xml` und
  XML/XSLT werden über den generischen Content-Endpunkt nur noch als **Attachment** mit
  `Content-Security-Policy: sandbox` ausgeliefert; die Vorschau sandboxt weiterhin gezielt.
- **M2 – Freigabe-Passwörter:** Übergabe per Header `X-Share-Password` (Query veraltet),
  Rate-Limit/Lockout auf Fehlversuche; Downloads werden **atomar** gezählt mit Limitprüfung.
- **N1 – Scoped Tokens:** Vorschau-/Archiv-Tokens tragen `tv` und werden nach
  Passwortänderung/-reset sofort ungültig.
- **N2 – FTP:** `utime`, `chmod`, `exists`, `isdir`, `isfile` prüfen ebenfalls Rechte
  (Defense-in-Depth).
- **N3 – Eindeutigkeit:** funktionaler Unique-Index auf `(parent_id, lower(name))` (nur
  aktive Einträge) verhindert Namensduplikate durch parallele Requests; Verletzungen → `409`.
- **N4:** `list_shares` nutzt `is_system_admin` (Rolle `admin` gleichgestellt).
- **N5 – TOCTOU:** `PATCH`/`complete` eines Uploads prüfen `write` am Zielordner erneut.
- **N6:** `max_downloads` wird atomar durchgesetzt (siehe M2).
- **M1 (Folge-Review) – Kopie/Share:** `POST /api/entries/{id}/copy` verlangt neben
  `read` auf der Quelle zusätzlich das `share`-Recht. Eine Kopie gehört dem Kopierenden
  und ist damit unabhängig verteilbar – ohne diese Prüfung ließe sich das Verbot,
  Freigabelinks anzulegen, durch Kopieren umgehen.
- **M2 (Folge-Review) – Share-Upload:** Öffentliche Drop-Link-Uploads unterliegen einem
  harten Größenlimit (`IFS_SHARE_UPLOAD_MAX_SIZE`, Default 100 MiB) und einem
  Rate-Limit (`IFS_SHARE_UPLOAD_MAX_REQUESTS`/`IFS_SHARE_UPLOAD_WINDOW_SECONDS`). Beide
  greifen unabhängig von `IFS_MAX_UPLOAD_SIZE=0`.
- **N1 (Folge-Review) – Archiv-Jobs:** `GET`/`DELETE /api/archive/jobs/{token}` prüfen den
  Token vollständig (Signatur, Zweck, `token_version`) – nach Passwortänderung/Deaktivierung
  sofort `401`, kein Job `404`.
- **N2 (Folge-Review) – Freigabe-Metadaten:** `GET /api/shares/{token}` verlangt bei
  passwortgeschützten Freigaben das Passwort (Header) und trägt `Cache-Control: no-store`.
- **N3 (Folge-Review) – Limiter-Speicher:** Login- und Share-Upload-Limiter begrenzen die
  Zahl der Einträge (`max_entries`) und räumen abgelaufene/älteste Einträge auf.
- **N4 (Folge-Review) – DB-Zugangsdaten:** Der Prod-Start verweigert Default-Credentials in
  `IFS_DATABASE_URL`; `docker-compose` leitet das Passwort aus `IFS_DB_PASSWORD` ab.
- **N5 (Folge-Review) – HSTS:** `Strict-Transport-Security` wird jetzt auch gesetzt, wenn
  `X-Forwarded-Proto: https` von einem konfigurierten vertrauenswürdigen Proxy kommt.
- **N6 (Folge-Review) – Deploy:** Secret-Dateien (`seaweedfs/s3.json`, `Caddyfile`, `.env`)
  sind nur für den Eigentümer lesbar; das Storage-UI-Passwort wird nicht mehr ausgegeben.
- **N7 (Folge-Review) – Aufräumen/SSRF:** `delete_user` entfernt Rollenzuweisungen; der
  SeaweedFS-Status-Abruf erlaubt nur `http`/`https` (kein `file://` u. Ä.).
- **Betriebliches:** Der Objektspeicher wird extern betrieben (kein lokaler Storage-Dienst);
  der In-Memory-Login-Limiter ist pro Prozess (dokumentiert) – für mehrere API-Worker ist
  ein gemeinsamer Store nötig.

---

## 15. Maßnahmen aus dem zweiten Sicherheits-Review

- **MIME-Normalisierung:** `safe_mime` normalisiert MIME-Typen auf Kleinschreibung; die
  Erkennung aktiver Inhalte (`INLINE_RISKY_TYPES`, `SANDBOXED_TYPES`) vergleicht zusätzlich
  case-insensitiv. Die Attachment-/Sandbox-Härtung für HTML, SVG, XHTML und XML ist damit
  nicht mehr über Schreibweisen wie `TeXt/HtMl` umgehbar.
- **FTPS-Größenlimit:** `IFS_MAX_UPLOAD_SIZE` wird jetzt auch im FTP-Pfad durchgesetzt –
  beim Schreiben in den Spool (Abbruch des Transfers) und zusätzlich in
  `finalize_received` vor dem S3-Upload.
- **Vorabprüfungen vor dem S3-Upload:** `namespace.ensure_writable` prüft Name, Zieltyp,
  Namenskonflikt und Quota, **bevor** ein Blob hochgeladen wird (einfacher Upload,
  resumable Complete, Share-Upload, FTP). Die SHA-256-Prüfung resumable Uploads erfolgt
  ebenfalls vor dem Upload. Absehbare Fehler erzeugen damit keine verwaisten Objekte mehr.
- **Blob-GC ohne TOCTOU:** Der Worker sperrt Kandidaten (`FOR UPDATE SKIP LOCKED`), löscht
  DB-seitig und entfernt die S3-Objekte erst nach dem Commit. Ein paralleler Dedup-Upload
  kann dadurch nie auf ein bereits gelöschtes Objekt verweisen.
- **Resumable Uploads:** `PATCH` und `complete` sperren die Upload-Session-Zeile
  (`FOR UPDATE`) – parallele Chunks mit gleichem Offset können das Größenlimit nicht mehr
  unterlaufen oder den Inhalt durchmischen.
- **FTP-Sitzungen:** Berechtigungsprüfung und Dateisystem-Lookup prüfen `is_active` bei
  jedem Kommando; eine Deaktivierung wirkt damit auch auf bereits verbundene Clients.
- **Copy und Quota:** `copy` zählt auf die Quota des Kopierenden.
- **Log-Redaktion:** Der Redaktionsfilter erfasst auch mehrsegmentige Token-Pfade wie
  `/api/archive/jobs/{token}`.
- **Audit:** Share-Uploads protokollieren nur noch ein Token-Präfix statt des vollständigen
  Freigabe-Tokens.
- **Eingabelimits:** Login- und Passwortfelder sind längenseitig begrenzt (Body-/CPU-Schutz
  vor der Authentifizierung).
- **Trash/Namespace:** Papierkorb- und Wiederherstellungs-Traversierung ist iterativ statt
  rekursiv (kein `RecursionError` bei sehr tiefen Bäumen).

---

## 16. Maßnahmen aus dem dritten Sicherheits-Review

- **F1 – Share-Upload und `write`-Recht:** `allow_upload` erlaubt anonymes Schreiben in
  einen Ordner und setzt daher nun voraus, dass der Ersteller dort selbst `write` hat –
  beim Anlegen der Freigabe und erneut bei jedem PATCH, das den Upload scharfschaltet
  (reines Abschalten bleibt ohne `write` möglich). Der Upload-Endpunkt prüft das
  `write`-Recht des Erstellers außerdem bei **jedem** anonymen Upload erneut; wird es
  ihm später entzogen oder wird er deaktiviert/gelöscht, ist der Drop-Link sofort
  gesperrt (`403`). Ein Nutzer mit `share`, aber ohne `write` (z. B. Rolle `sharer`)
  kann damit keine Schreibrechte umgehen.
- **F1 (Teil B) – Überschreiben als Freigabe-Eigenschaft:** Das bisherige
  Request-Flag `?overwrite=true` konnte fremde Dateien im Drop-Ordner ersetzen. Das
  Überschreiben ist jetzt eine Eigenschaft der Freigabe (`shares.overwrite`, Default
  `false`, API-Feld, Migration `0005_share_overwrite.sql`); anonyme Uploads weichen ohne
  diese Freigabe bei Namenskonflikten aus. Scharfschalten erfordert ebenfalls `write`
  am Eintrag.
- **F2 – Verschieben verlangt `read`:** Da `move` die additive Rechtevererbung über den
  Pfad ändert, verlangen HTTP `POST /api/entries/{id}/move`, `POST /api/bulk/move` und
  FTP `RNFR`/`RNTO` zusätzlich zum `write` auf Quelle und Ziel nun `read` auf der Quelle.
  Ein write-only-Eintrag lässt sich damit nicht mehr in einen lesbaren Ordner
  verschieben und dort lesen. Dasselbe gilt für das **Wiederherstellen** aus dem
  Papierkorb (`POST /api/trash/{entry_id}/restore`): Es verlangt nun zusätzlich `read`
  auf dem Eintrag, sonst ließe sich ein delete-only-Eintrag von dort in einen lesbaren
  Zielordner holen (Nachtrag aus der Verifikation des dritten Reviews).
- **F3 – Quota beim Wiederherstellen:** `namespace.restore` prüft vor dem Umsetzen die
  Quota des **betroffenen Besitzers** (`entry.owner_id`, sonst der handelnde Akteur).
  Da gelöschte Einträge nicht auf die Quota zählen, ließ sich der Papierkorb sonst als
  Puffer nutzen, um die Quota dauerhaft zu überschreiten (`413`).
- **F4 – Zyklus-Schutz:** `namespace.move` sperrt beide beteiligten Einträge
  (`SELECT … FOR UPDATE`, auf SQLite ein No-op) vor der Zyklusprüfung, damit parallele
  Moves (A→B und B→A) unter PostgreSQL keinen Verzeichniszyklus erzeugen können.
  Defense-in-Depth brechen `path_of`, `_is_descendant` und `authz.effective_perms` bei
  einer Wiederholung mit einem `seen`-Set ab, statt in eine Endlosschleife zu laufen.
- **H2 – Dev-Zertifikat:** `scripts/gen-dev-cert.py` setzt nach dem Schreiben die Rechte
  des privaten FTPS-Schlüssels auf `0600` (Best-Effort; unter Windows nur eingeschränkt
  wirksam).
- **H3 – Versionsendpunkt:** `/version` liefert keine Umgebungsangabe
  (`environment`) mehr – der unauthentifizierte Endpunkt gibt damit keine
  Betriebsinformationen preis.
- **H4 – Eingabelimits:** Die Rechte-Listen in `AclCreate`, `RoleCreate` und
  `RoleUpdate` sind auf höchstens fünf Einträge begrenzt (Body-/CPU-Schutz vor der
  Bitmasken-Auswertung).
- **H5 – Proxy-Betrieb:** Ohne gesetztes `IFS_TRUSTED_PROXIES` sehen alle Clients wie
  ein einziger aus; Rate-Limit und Login-Sperre wirken dann global statt pro Client.
  `.env.example` und [Konfiguration](konfiguration.md) warnen nun deutlich davor, die
  Proxy-Netze zu setzen.

---

## 17. Maßnahmen aus dem vierten Sicherheits-Review

- **S1 – `read`-Pflicht beim Erstellen von Freigaben:** `POST /api/shares` verlangt jetzt
  `read` **und** `share` am Eintrag. Zuvor konnte ein Principal mit ACL `share` (ohne
  `read`) einen öffentlichen Link anlegen und den Inhalt darüber selbst abrufen – `share`
  wirkte damit faktisch als `read`-Recht. Wer Inhalte nicht lesen darf, darf sie auch
  nicht veröffentlichen.
- **S2 – Admins umgehen die Per-User-Isolation nicht mehr per API:**
  `POST /api/roles/{id}/assignments` mit `entry_id` verlangt nun `admin` am Eintrag; ein
  Systemadmin kann sich keine Ressourcenrolle an fremden Wurzeln mehr selbst zuweisen.
  `PATCH /api/shares/{token}` erlaubt Systemadmins ebenfalls nicht mehr (ein PATCH hätte
  z. B. den Passwortschutz fremder Links entfernen können); das **Widerrufen** fremder
  Freigaben bleibt als Missbrauchsschutz erlaubt. Die Share-Übersicht ist für Admins
  weiterhin sichtbar – nicht passwortgeschützte Links sind ohnehin für jeden mit Token
  abrufbar. Bewusste Break-Glass-Funktion bleibt `delete_user(..., transfer_to)`
  (Besitzübernahme beim Löschen eines Kontos).
- **S3 – FTP-Vorladen prüft `read`:** `IFSFilesystem._open_writer` verlangt bei
  `APPE`/`REST` (`a`/`r+`) zusätzlich `read` auf der bestehenden Datei, bevor deren Inhalt
  in den Spool kopiert wird. In der Per-User-Architektur sind fremde Bäume per FTP-Pfad
  normalerweise nicht erreichbar – die Prüfung ist Defense-in-Depth. Schlägt das Vorladen
  fehl, wird der Spool aufgeräumt.
- **S4 – Eingabelimits:** `POST /api/shares/bulk/revoke` begrenzt Tokens auf 64 Zeichen
  pro Eintrag; `POST /api/uploads` akzeptiert `sha256` nur noch als 64 Hex-Zeichen und
  begrenzt `mime` auf 255 Zeichen (Body-/CPU-Schutz vor der Prüfung).
