# Sicherheitsrichtlinie

IFS ist ein webbasiertes Dateisystem mit HTTP(S)- und FTPS-Zugriff auf einen
S3-kompatiblen Objektspeicher. Dieses Dokument beschreibt, wie
Sicherheitslücken gemeldet werden, welche Schutzmaßnahmen implementiert sind und
welche Restrisiken bewusst akzeptiert werden. Die ausführliche technische
Begründung steht im [Sicherheitskonzept](docs/sicherheit.md); API-Details in der
[REST-API-Referenz](docs/api-referenz.md).

## Unterstützte Versionen

Sicherheitsupdates erscheinen für die jeweils aktuelle Vorab-/Hauptversion.

| Version | Unterstützt |
|---|---|
| `0.1.x` (Pre-Release) | ja |
| älter | nein |

## Sicherheitslücken melden

Bitte **keine** öffentlichen GitHub-Issues für Sicherheitslücken verwenden.

- Bevorzugt: GitHub **Private Vulnerability Reporting** („Report a
  vulnerability“) im Repository
  [`themistermorph/improper-file-service`](https://github.com/themistermorph/improper-file-service).
- Alternativ über das [Maintainer-Profil](https://github.com/themistermorph).

Bitte angeben:

- betroffene Version bzw. Commit,
- Reproduktionsschritte (idealerweise minimal),
- erwartetes vs. beobachtetes Verhalten,
- mögliche Auswirkung auf Vertraulichkeit, Integrität und Verfügbarkeit,
- optional einen Proof-of-Concept ohne echte fremde Daten.

Wir bestätigen den Eingang in der Regel innerhalb weniger Werktage, bewerten die
Meldung und stimmen Offenlegung und Veröffentlichung mit dir ab (Coordinated
Disclosure). Bitte gib uns vor einer öffentlichen Offenlegung angemessene Zeit
für Analyse und Fix; im Gegenzug nennen wir dich auf Wunsch als Melder:in.

## Umfang (Scope)

Im Fokus:

- Authentifizierung und Autorisierung über HTTP(S) und FTPS,
- Rechteausweitung, Umgehung des zentralen Policy-Entscheiders,
- Pfad-/Namespace-Sicherheit (Traversal, Symlinks, Per-User-Isolation),
- Umgang mit Datei-Inhalten, Content-Typen und aktiven Inhalten,
- Freigaben (Links, Passwörter, Ablauf, Downloadlimits, Uploads),
- Session-/Cookie-Handling, CSRF, CSP und Security-Header,
- Injection-Schwachstellen (SQL, Kommando, Template),
- Fehler, die Secrets, Tokens oder personenbezogene Daten offenlegen.

Nicht im Fokus:

- Angriffe, die physischen oder hostseitigen Zugriff auf DB, S3, `.env` oder
  Container voraussetzen (dokumentierte Design-Grenze),
- Fehlkonfigurationen des Betriebs (TLS-Terminierung, Reverse-Proxy, Firewall,
  zu großzügige `IFS_BEARER_ALLOWED_NETWORKS`),
- Denial-of-Service durch absichtlich große Ressourcen ohne Betriebsgrenzen,
- Schwachstellen in Abhängigkeiten ohne konkreten Angriffsweg auf IFS,
- fehlende Ausbaustufen (MFA, OIDC/SSO, Virenscan), solange sie dokumentiert
  sind.

## Sicherheitsarchitektur

### Transport

- **HTTPS** für Web-UI und REST-API; TLS-Terminierung am Ingress
  (Traefik/Caddy/nginx). Bei erkanntem HTTPS setzt die Anwendung
  `Strict-Transport-Security` (`max-age=31536000; includeSubDomains`).
- **FTPS** mit explizitem `AUTH TLS`; Steuer- und Datenkanal sind verschlüsselt.
  Plain-FTP wird auf Protokollebene abgelehnt.
- TLS ≥ 1.2 mit modernen Cipher-Suiten (Server-/Ingress-Defaults).

### Authentifizierung

| Kanal | Verfahren |
|---|---|
| Web-UI | Benutzername + Passwort → `HttpOnly`-Session-Cookie |
| API-Clients | Benutzername + Passwort → JWT-Bearer-Token (nur freigegebene Netze oder Systemrecht `api`) |
| FTPS | FTP-Login gegen denselben Benutzerbestand (über TLS) |

- Passwörter werden mit **Argon2id** gehasht und niemals im Klartext
  gespeichert oder geloggt.
- Fehlgeschlagene Logins werden als `auth.login.failed` auditiert; Konto- und
  IP-basierte Backoff-Sperren liefern HTTP `429` bzw. FTP `530`.
- Auch bei unbekanntem Benutzernamen wird ein Hash-Vergleich ausgeführt
  (keine Benutzer-Enumeration über Timing).
- Der letzte aktive Administrator kann weder deaktiviert noch gelöscht werden.

### Web-Session ohne JWT im JavaScript-Speicher

Die Web-UI speichert **kein** Token in `localStorage` oder `sessionStorage`.
`POST /api/auth/login` setzt stattdessen ein Cookie mit folgenden Attributen:

| Attribut | Wert |
|---|---|
| `HttpOnly` | immer |
| `SameSite` | `Strict` |
| `Path` | `/` |
| `Secure` | bei HTTPS (direkt oder über einen vertrauenswürdigen Proxy) |
| `Max-Age` | `IFS_ACCESS_TOKEN_TTL_SECONDS` (Standard 3600 s) |
| Name | `IFS_AUTH_COOKIE_NAME` (Standard `ifs_session`) |

Für Produktionsumgebungen empfiehlt sich der Name `__Host-ifs_session`: Das
Präfix erzwingt `Secure`, `Path=/` und verbietet ein `Domain`-Attribut.

Die Web-UI ruft den Login mit `?issue_token=false` auf und bekommt dann kein JWT
im Response-Body. Ein XSS kann die Sitzung daher nicht aus dem Speicher lesen.
`POST /api/auth/logout` löscht das Cookie; `GET /api/auth/session` prüft die
Sitzung ohne 401-Rauschen.

**CSRF:** Primäre Abwehr ist `SameSite=Strict`. Zusätzlich prüft die Middleware
bei `POST`/`PUT`/`PATCH`/`DELETE` mit vorhandenem Session-Cookie den
`Origin`-Header und `Sec-Fetch-Site`. Fremde Werte werden mit `403` abgelehnt.
Requests ohne diese Header (curl, Integrationsclients) bleiben zulässig; sie
können keine fremde Browser-Sitzung ausnutzen.

**Token-Invalidierung:** Access-Tokens und Cookie-Sessions tragen eine
`token_version`. Passwortänderung, Admin-Reset und Deaktivierung erhöhen sie;
alle zuvor ausgestellten Tokens sind sofort ungültig. Es gibt bewusst keine
serverseitige Session-Liste: Ein bereits kopiertes Cookie bleibt bis zum Ablauf
gültig, sofern die `token_version` nicht erhöht wird.

### Bearer-Tokens für API-Clients

Bearer bleibt für Automatisierung und Integrationen verfügbar, wird aber nur
akzeptiert, wenn mindestens eine Bedingung erfüllt ist:

1. die Client-IP liegt in `IFS_BEARER_ALLOWED_NETWORKS` (kommagetrennte
   CIDR-Liste, Standard `127.0.0.1/32,::1/128`), **oder**
2. das Konto besitzt das Systemrecht `api` (Bit 256).

Das Systemrecht `api` ist über die Benutzer-/Gruppen-Systemrechte vergebbar und
gewährt **keinen** Dateizugriff. Ein leerer Wert für
`IFS_BEARER_ALLOWED_NETWORKS` deaktiviert den IP-basierten Zugang vollständig.
Für ein Administrationsnetz ist beispielsweise `10.13.13.0/24` üblich; die
Web-UI benötigt diese Freigabe nicht, weil sie Cookies nutzt.

### Autorisierung und Isolation

- Zentraler Policy Decision Point (`src/ifs/core/authz.py`), **deny by default**,
  identisch für HTTP und FTPS.
- Jeder Benutzer besitzt eine eigene Wurzel. Systemadmins haben **keinen**
  Dateizugriff auf fremde Wurzeln; Zugriff entsteht nur über Besitz und ACLs.
- Eintragsrechte (`read`, `write`, `delete`, `share`, `publish`, `admin`) werden
  entlang des Pfades vererbt. Systemrechte (`audit`, `monitor`, `api`) sind ein
  getrennter Namensraum und landen nie in ACLs.
- Bewusste Break-Glass-Funktionen sind der Widerruf fremder Links und die
  Besitzübernahme beim Löschen eines Kontos (`transfer_to`).

### Content-Security-Policy und Security-Header

Die App-Oberfläche (`/`, `/mobile`, `/published`) wird mit einer strikten CSP
ausgeliefert:

```
default-src 'self';
script-src 'self' 'sha256-…';
script-src-attr 'none';
style-src 'self' 'sha256-…';
style-src-attr 'none';
img-src 'self' data: blob:;
media-src 'self' blob:;
frame-src 'self' blob:;
connect-src 'self';
object-src 'none';
base-uri 'none';
frame-ancestors 'self';
form-action 'self'
```

- **Kein `unsafe-inline` und kein `unsafe-hashes`.** Inline-`<script>`- und
  `<style>`-Blöcke werden über SHA-256-Hashes freigegeben; Event-Handler und
  `style=`-Attribute sind vollständig entfernt.
- Die UI nutzt zentrale Event-Delegation über `data-action`,
  `data-change` und `data-input`. Dynamische Breiten setzt sie über CSSOM
  (`element.style.width`), was CSP nicht blockiert.
- Die Hashes werden beim Start aus den tatsächlich ausgelieferten HTML-Dateien
  berechnet (`src/ifs/security_headers.py`). Nach Änderungen an Inline-Blöcken
  ist ein Neustart des API-Prozesses erforderlich.
- Aktive Inhalte aus Vorschau und Content-Endpunkten werden zusätzlich mit
  `Content-Security-Policy: sandbox; default-src 'none'; style-src 'none';
  img-src data: blob:` ausgeliefert. Dort werden weder Skripte noch
  eingebettete Styles ausgeführt.
- Weitere Header: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy: geolocation=(), microphone=(), camera=()`.
- Das Framing ist auf die eigene Origin beschränkt; Formulare dürfen nur an die
  eigene Origin senden; `base`-Tags sind deaktiviert.

### Datei- und Upload-Sicherheit

- Ausschließlich virtueller Namespace; keine OS-Pfade, `..` wird geklammert
  bzw. abgelehnt.
- Content-Addressed Storage mit SHA-256, atomare Versionierung und
  Papierkorb/Soft-Delete statt Hard-Delete.
- Größenlimits, Typ-/MIME-Behandlung und geschützte Auslieferung aktiver Typen
  (Attachment plus Sandbox-CSP).
- Resumable Uploads prüfen Rechte und Zielordner erneut (TOCTOU-Schutz).

### Audit und Betrieb

- Append-only Audit-Log für Authentifizierungs- und Mutationsereignisse mit
  Protokoll, IP und Ergebnis; Tokens werden in Logs redigiert.
- `/docs`, `/redoc` und `/openapi.json` sind standardmäßig deaktiviert
  (`IFS_EXPOSE_DOCS`); `/metrics` erfordert Adminrechte
  (`IFS_METRICS_PUBLIC` als bewusstes Opt-in).
- `X-Forwarded-For`/`X-Forwarded-Proto` werden nur von konfigurierten
  `IFS_TRUSTED_PROXIES` ausgewertet.
- Secrets kommen ausschließlich aus Umgebungsvariablen/Secret-Managern, nicht
  aus dem Repository.

## Restrisiken

Diese Punkte sind bewusst dokumentiert und keine unbeabsichtigten Lücken:

- **XSS bleibt möglich:** Die strikte CSP und `HttpOnly` verhindern das
  Auslesen der Sitzung, aber nicht jede DOM-Manipulation durch kompromittierte
  Same-Origin-Ressourcen. Trusted Types sind nicht aktiv (erfordert einen
  größeren Umbau der `innerHTML`-Nutzung).
- **Stateless Sessions:** Logout löscht nur das Cookie; ein bereits kopiertes
  Cookie bleibt bis zum Ablauf gültig, wenn die `token_version` nicht erhöht
  wird.
- **Kein MFA, kein OIDC/SSO, kein Virenscan** (in der Architektur vorgesehen).
- **Keine Ende-zu-Ende-Verschlüsselung:** Der Server sieht Klartext.
- **Betriebsgrenzen:** Schutz vor Ressourcen-Erschöpfung hängt von den
  konfigurierten Limits und der Infrastruktur ab.
- **Presigned-URLs** sind implementiert, im Standardpfad aber nicht aktiv.

## Checkliste für sicheren Betrieb

- [ ] `IFS_JWT_SECRET` (mind. 32 Zeichen) und `IFS_ADMIN_PASSWORD` geändert
- [ ] HTTPS mit gültigem Zertifikat, TLS ≥ 1.2
- [ ] In Produktion `IFS_AUTH_COOKIE_NAME=__Host-ifs_session`
- [ ] `IFS_BEARER_ALLOWED_NETWORKS` auf die tatsächlichen Admin-/API-Netze
      begrenzt (oder leer, dann nur Konten mit Systemrecht `api`)
- [ ] `IFS_TRUSTED_PROXIES` nur für die eigenen Reverse-Proxies gesetzt
- [ ] `IFS_AUTO_CREATE_SCHEMA=false`, Migrationen kontrolliert anwenden
- [ ] FTPS-Zertifikat gültig, Passive-Range beschränkt und per Firewall geschützt
- [ ] `IFS_S3_SSE` aktiv, DB-Volume und Backups verschlüsselt
- [ ] Keine Secrets im Repository oder in Logs
- [ ] Backups getestet (Restore-Probe), Audit-Log gesichert
- [ ] Admin-Konten eingeschränkt, nicht benötigte Konten deaktiviert
- [ ] `IFS_EXPOSE_DOCS=false` und `IFS_METRICS_PUBLIC=false` (Standard)

## Verifikation

Die Sicherheitsmaßnahmen sind durch automatisierte Tests abgesichert:

```bash
python -m ruff check src tests
python -m pytest -q
python scripts/check-doc-links.py
```

Relevante Testdateien: `tests/test_content_security.py` (CSP ohne
`unsafe-inline`, Hashes, keine Inline-Handler/-Styles),
`tests/test_auth_cookie.py` (Cookie-Attribute, Logout, CSRF, Bearer-Policy,
Token-Invalidierung), `tests/test_security*.py` (Hardening und Audits).

## Offenlegung und Danksagung

Wir veröffentlichen bestätigte Sicherheitslücken nach Abstimmung, in der Regel
zusammen mit einem Fix oder einer dokumentierten Gegenmaßnahme. Melder:innen
werden auf Wunsch genannt. Bitte keine fremden personenbezogenen Daten oder
Produktionszugänge in Meldungen aufnehmen.
