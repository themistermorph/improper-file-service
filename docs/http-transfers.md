# HTTP-Transfers: Durchsatz, Streaming und Tuning

Dieses Handbuch beschreibt den HTTP-Datenpfad für große Dateien: wie Downloads und
Uploads technisch ablaufen, welche Stellschrauben es gibt und wie man sie misst.
Die FTPS-Seite steht im [FTPS-Handbuch](ftps.md#7-durchsatz-und-tuning).

---

## 1. Datenpfade im Überblick

```
Download:
  Browser/Client ⇄ HTTPS ⇄ IFS-API ⇄ S3-Range-Streams ⇄ Objektspeicher
  (Web-UI: kurzlebiges Ticket, nativer Browser-Download)

Upload (klassisch):
  Client ⇄ HTTPS ⇄ IFS-API ⇄ Spool-Datei ⇄ S3 (Multipart) ⇄ Objektspeicher

Upload (parallel):
  Client ⇄ 4× HTTPS ⇄ IFS-API ⇄ 4 Part-Dateien ⇄ PartReader ⇄ S3 ⇄ Objektspeicher
```

Der Namespace (Name, Rechte, Version) bleibt in PostgreSQL; S3 enthält nur Blobs.
Die HTTP-Endpunkte prüfen AuthZ und Quota unverändert vor jedem Transfer.

---

## 2. Download

### 2.1 Nativer Browser-Download (Download-Ticket)

Die Web-UI hat früher `fetch()` + `res.blob()` verwendet. Dabei muss der Browser die
**komplette Datei im RAM** halten, bevor der Download startet – bei mehreren GB
unbrauchbar und ohne Fortschritt/Resume.

Heute fordert die UI ein kurzlebiges Ticket an:

```http
POST /api/entries/{entry_id}/download-token
Authorization: Bearer <token>

200 {"token":"…","url":"/api/download/…","expires_in":3600,"name":"big.bin","size":…}
```

Der Browser navigiert auf `url`. Der Endpunkt streamt die Datei mit
`Content-Disposition: attachment` und unterstützt `Range`; der Browser schreibt
direkt auf die Platte und kann bei Abbruch fortsetzen.

Sicherheit:

- Ticket ist an **Eintrag**, **Benutzer** und **`token_version`** gebunden.
- Ablauf über `IFS_HTTP_DOWNLOAD_TOKEN_TTL_SECONDS` (Standard 3600 s).
- Logout/Passwortänderung/Deaktivierung invalidiert das Ticket sofort.
- Access-Log redigiert das Token-Segment (`/api/download/<redacted>`).

### 2.2 Paralleles S3-Prefetch

Ein einzelner S3-Stream ist häufig der Engpass, besonders bei WAN- oder
objektspeicherseitig begrenzten Verbindungen. `content.iter_object` teilt den
angeforderten Bereich deshalb in Fenster und liest bis zu
`IFS_HTTP_DOWNLOAD_PARALLELISM` Fenster gleichzeitig mit eigenen S3-Range-Requests.
Die Ausgabe an den Client erfolgt **strikt in Dateireihenfolge**; es werden nie
Fenster vertauscht oder doppelt gesendet.

| Wert | Verhalten |
|---|---|
| `1` | klassischer einzelner S3-Stream (bisheriges Verhalten) |
| `2`–`4` | empfohlen für LAN/WAN; Default `4` |
| `> 4` | mehr S3-Last und Speicher; auf 16 begrenzt |

Speicherbedarf pro aktivem Download: `IFS_HTTP_STREAM_CHUNK_BYTES × Parallelität`
(Default 4 MiB × 4 = **16 MiB**). Zusätzlich belegt jeder Download einen
S3-Pool-Slot; `IFS_S3_MAX_POOL_CONNECTIONS` muss zu `Parallelität × gleichzeitige
Downloads` passen.

Range-Requests vom Client werden genauso behandelt: Der angeforderte
`[start, end]`-Bereich wird in Fenster geteilt; `206`/`Content-Range` bleiben
unverändert. `HEAD` löst weiterhin keinen S3-Zugriff aus.

### 2.3 Blockgröße und DB-Verbindung

`IFS_HTTP_STREAM_CHUNK_BYTES` (Default 4 MiB, Minimum 64 KiB) bestimmt die
Blockgröße beim Lesen aus S3 und beim Senden an den Client. Größere Blöcke
reduzieren `read`-/ASGI-/Socket-Operationen.

Vor dem eigentlichen Stream wird die DB-Verbindung freigegeben (der Audit-Eintrag
ist vorher committed). Ein Multi-GB-Download blockiert damit keinen
Connection-Pool-Slot über die gesamte Transferdauer.

---

## 3. Upload

### 3.1 Einfacher und sequenzieller Upload

- `PUT /api/uploads/simple`: Body wird in den Spool geschrieben, SHA-256 entsteht
  währenddessen; danach S3-Upload.
- `POST /api/uploads` + `PATCH /api/uploads/{id}`: tus-ähnlich, Chunks müssen
  lückenlos in Reihenfolge eintreffen.

Beide Pfade bleiben unverändert und eignen sich für kleine Dateien bzw. Clients,
die keine Parts unterstützen.

### 3.2 Paralleler Part-Upload

Ab 8 MiB nutzt die Web-UI automatisch Part-Sessions:

```http
POST /api/uploads
{"parent_id":"…","name":"big.bin","size":123456789,"parallel":true}

201 {"id":"…","offset":0,"status":"pending","part_size":16777216,"parallelism":4}
```

Anschließend sendet der Client `ceil(size / part_size)` Parts über
`PUT /api/uploads/{id}/parts/{n}` – **parallel, out-of-order, mit Retry**.
Jeder Part wird in eine Temp-Datei geschrieben und nach exakter Größenprüfung
atomar an seinen endgültigen Namen umbenannt. Ein Retry ist idempotent.

Serverseitige Ablage:

```
<spool>.part                 leerer Basis-Spool (Kompatibilität)
<spool>.part.parts.json      Marker: {"version":1,"part_size":…,"total":…}
<spool>.part.p1 … .pN        fertige Parts
```

`HEAD /api/uploads/{id}` liefert `Upload-Part-Size`, `Upload-Parts` und den
lückenlosen `Upload-Offset`. `POST /complete` prüft lückenlose Parts, bildet den
SHA-256 (einmal) und übergibt sie über `content.PartReader` an S3. Dabei entsteht
**keine zusätzliche Vollkopie**; der Reader liest die Part-Dateien sequenziell in
der von boto3 benötigten Reihenfolge.

Die Part-Größe wird auf 5–64 MiB geklemmt (`IFS_HTTP_UPLOAD_PART_BYTES`), weil
S3-Multipart eine Untergrenze von 5 MiB hat (außer dem letzten Part). Die vom
Server gemeldete Client-Parallelität ist `IFS_HTTP_UPLOAD_PARALLELISM` (1–8,
Default 4).

### 3.3 Grenzen

- Der Spool muss die **größte gleichzeitig laufende Datei** aufnehmen können.
- `IFS_MAX_UPLOAD_SIZE` und die Besitzer-Quota werden weiterhin geprüft.
- Abgebrochene/verwaiste Sessions entfernt der Worker nach 24 h inklusive
  Marker, Parts und Temp-Dateien.
- Upload-Sessions bleiben strikt nutzergebunden; auch Admins haben keinen
  Zugriff auf fremde Sessions.

---

## 4. Konfiguration

| Variable | Default | Untergrenze | Wirkung |
|---|---|---|---|
| `IFS_HTTP_STREAM_CHUNK_BYTES` | `4194304` | `65536` | Blockgröße Download-Stream und Prefetch-Fenster |
| `IFS_HTTP_DOWNLOAD_PARALLELISM` | `4` | `1` (max. `16`) | Parallele S3-Range-Requests je Download |
| `IFS_HTTP_DOWNLOAD_TOKEN_TTL_SECONDS` | `3600` | `30` | Gültigkeit des Download-Tickets |
| `IFS_HTTP_UPLOAD_PART_BYTES` | `16777216` | `5242880` (max. `64 MiB`) | Part-Größe paralleler Uploads |
| `IFS_HTTP_UPLOAD_PARALLELISM` | `4` | `1` (max. `8`) | Client-Parallelität für Parts |
| `IFS_S3_MAX_POOL_CONNECTIONS` | `32` | `1` | Muss zu parallelen Downloads/Uploads passen |

**Richtwerte**

| Umgebung | Download-Parallelität | Stream-Chunk | Upload-Parts |
|---|---|---|---|
| LAN, wenige Nutzer | `2`–`4` | `4194304` | `4` × 16 MiB |
| WAN, hohe Latenz | `4`–`8` | `4194304`–`8388608` | `4`–`8` × 16 MiB |
| Speicherbegrenzter Container | `2` | `1048576` | `2` × 8 MiB |
| Viele parallele Downloads | `2` | `2097152` | – |

Für `n` gleichzeitige Downloads mit Parallelität `p` sollten mindestens
`n × p` S3-Pool-Verbindungen verfügbar sein (Default-Pool: 32).

---

## 5. Messen

### 5.1 Download mit `curl`

```bash
# Direkt über die API (Bearer-Token)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://HOST:8000/api/entries/$ID/content" -o /dev/null \
  -w 'http=%{http_code} bytes=%{size_download} time=%{time_total}s rate=%{speed_download}B/s\n'
```

Für Vergleiche dieselbe Datei, denselben Client und möglichst dieselbe Uhrzeit
verwenden. Der Testsystem-Baseline (512 MiB, Server-local) lag bei
**139–156 MB/s** für den klassischen Einzel-Stream.

### 5.2 Download-Ticket

```bash
TICKET=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "http://HOST:8000/api/entries/$ID/download-token")
URL=$(echo "$TICKET" | python -c "import sys,json;print(json.load(sys.stdin)['url'])")
curl -sS "http://HOST:8000$URL" -o /dev/null -w '%{speed_download}B/s\n'
```

### 5.3 Upload

```bash
# Einfacher Upload
curl -sS -X PUT -H "Authorization: Bearer $TOKEN" \
  --data-binary @big.bin \
  "http://HOST:8000/api/uploads/simple?parent_id=$FOLDER&name=big.bin" \
  -o /dev/null -w 'http=%{http_code} time=%{time_total}s rate=%{speed_upload}B/s\n'
```

Parallele Parts: `part_size` aus der Session-Antwort verwenden, Datei mit
`dd`/`split` in Parts schneiden und mehrere `curl`-Prozesse parallel starten.
Die Web-UI macht das automatisch; für reproduzierbare Messungen eignet sich ein
kleines Python-Skript mit `ThreadPoolExecutor`.

### 5.4 S3 isoliert messen

Auf dem Testsystem (SeaweedFS im Docker-Stack):

```bash
# Ein Stream vs. parallele Range-Reads
docker compose exec -T api python - <<'PY'
# siehe deploy/bench-s3-read.sh im Testsystem
PY
```

Gemessene Richtwerte (2026-10-28, 256–512 MiB):

| Messung | Ergebnis |
|---|---|
| S3 ein Stream lesen | ~40 MB/s |
| S3 parallel 2 / 4 / 8 Streams | ~201 / 220 / 195 MB/s |
| S3 schreiben (Multipart) | ~21–28 MB/s |
| S3 CopyObject | ~31 MB/s |
| HTTP-Download klassisch | ~139–156 MB/s |
| HTTP-Upload simple (512 MiB) | ~22,4 MB/s |

> **Einordnung:** Auf diesem Testsystem ist **SeaweedFS beim Schreiben** der
> Engpass. Kein HTTP-Upload kann dauerhaft schneller sein als der
> Objektspeicher. Der Part-Modus verkürzt dort die clientseitige Wartezeit und
> hilft bei WAN-Clients, weil die Teile parallel übertragen werden; die
> S3-Schreibrate selbst bleibt begrenzt. Der Download profitiert dagegen direkt
> vom Parallel-Prefetch.

---

## 6. Betrieb und Troubleshooting

| Symptom | Ursache | Lösung |
|---|---|---|
| Download startet erst nach langer Wartezeit / Browser hängt | alter Client nutzt noch `blob()` oder sehr großer RAM-Bedarf | Web-UI aktualisieren; Download-Ticket prüfen |
| Download bricht bei großen Dateien ab | Reverse-Proxy-Timeout | `proxy_read_timeout`/`proxy_send_timeout` erhöhen (Beispiel: 3600 s) |
| Download-Ticket `401` | abgelaufen, Logout oder Passwortänderung | neues Ticket anfordern; `IFS_HTTP_DOWNLOAD_TOKEN_TTL_SECONDS` prüfen |
| Upload `413` von nginx | `client_max_body_size` zu klein | mindestens `IFS_HTTP_UPLOAD_PART_BYTES` (16 MiB) oder `0` setzen |
| Upload `409` bei Parts | Part hat abweichende Größe oder Session ist keine Part-Session | `HEAD` prüfen; `Upload-Parts`/`part_size` verwenden |
| Upload `400` bei Complete | Part fehlt oder SHA-256 stimmt nicht | fehlende Parts senden; Prüfsumme prüfen |
| Viele parallele Transfers langsam | S3-Pool zu klein | `IFS_S3_MAX_POOL_CONNECTIONS` erhöhen |
| Ein Download langsam, S3 schnell | Client-/Netzwerkstrecke begrenzt | Parallelität hilft nur bis zur Client-Bandbreite; ggf. mehrere Verbindungen im Client |

Weiter: [API-Referenz](api-referenz.md) · [Konfiguration](konfiguration.md) ·
[Tutorial 2 – Dateien über HTTP](../tutorials/02-dateien-ueber-http.md) ·
[Tutorial 4 – Resumable Uploads](../tutorials/04-resumable-uploads.md)
