# Sicherheitsrichtlinie

## Unterstützte Versionen

Wir unterstützen Sicherheitsupdates für die jeweils aktuelle Vorab-/Hauptversion.

| Version | Unterstützt |
|---|---|
| 0.1.x (Pre-Release) | ja |

## Sicherheitslücken melden

Bitte **keine** öffentlichen GitHub-Issues für Sicherheitslücken verwenden.

- Bevorzugt: GitHub **Private Vulnerability Reporting** („Report a vulnerability")
  im Repository.
- Alternativ per E-Mail an die im Repository hinterlegte Kontaktadresse
  (siehe `pyproject.toml` / Maintainer-Profil).

Bitte angeben:
- Betroffene Version/Commit,
- Reproduktionsschritte (idealerweise minimal),
- erwartetes vs. beobachtetes Verhalten,
- mögliche Auswirkung (Vertraulichkeit/Integrität/Verfügbarkeit),
- optional einen Proof-of-Concept.

Wir bestätigen den Eingang in der Regel innerhalb weniger Werktage und stimmen
Offenlegung/Veröffentlichung mit dir ab (Coordinated Disclosure).

## Umfang (Scope)

Im Fokus:
- Authentifizierung/Autorisierung (HTTP & FTPS), Rechteausweitung,
- Pfad-/Namespace-Sicherheit, Datei-Inhalte/Content-Typen,
- Freigaben (Links, Passwörter, Limits),
- Per-User-Isolation.

Nicht im Fokus:
- Angriffe, die physischen/hostseitigen Zugriff auf DB, S3 oder `.env` voraussetzen
  (bekannte, dokumentierte Design-Grenze – siehe `docs/sicherheit.md`),
- Fehlkonfigurationen des Betriebs (TLS-Terminierung, Reverse-Proxy, Firewall),
- Denial-of-Service durch absichtlich große Ressourcen ohne Betriebsgrenzen.

Details zu getroffenen Maßnahmen und Restrisiken: [`docs/sicherheit.md`](docs/sicherheit.md).
