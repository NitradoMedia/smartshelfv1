# POS Video Guard

Abgleich von **Kassen-Transaktionen** mit **Reolink-Video** per KI. Bei Abweichungen (z. B. Bon 5 Artikel, Video zählt anders) erscheint ein Vorfall im Dashboard — inkl. Videoausschnitt — zum Schließen als **Fehlalarm** oder Markieren als **Diebstahl**.

## Ablauf

1. Kassensystem legt CSV/JSON in `/data/pos` (oder Upload im Dashboard).
2. System liest Timestamp + Artikelanzahl.
3. Ca. **5 Sekunden vor** dem Bon-Zeitstempel wird ein Clip von der Reolink gezogen.
4. KI zählt sichtbare Artikel im Clip.
5. Weicht die Zählung vom Bon ab → Eintrag im Dashboard mit Video.

## Videoquellen

1. **Manuell im Dashboard** → „Manuell abgleichen“: Excel/CSV + Video(s) hochladen  
   - Dateiname mit Bon-ID (`B-1001.mp4`) oder Zeitstempel (`20260826_222205.mp4`)
2. **FTP** → „FTP / Videoquelle“: Host, User, Passwort, Remote-Ordner speichern  
   - „Vom FTP holen & abgleichen“ lädt passende Clips nach Zeitstempel
3. **Reolink-API/RTSP** (wenn konfiguriert)
4. Demo-Platzhalter

Priorität bei `video_source=auto`: Upload-Ordner → FTP → Reolink → Demo.

## Schnellstart (Docker)

```bash
cp .env.example .env
docker compose up --build -d
```

Dashboard: **http://localhost:8088**

### Lokal ohne Docker

```bash
cp .env.example .env
pip install -r backend/requirements.txt
./scripts/run-local.sh
```

Dann ebenfalls **http://localhost:8088** (bindet `0.0.0.0:8088`).

Demo-Modus ist standardmäßig aktiv (`DEMO_MODE=true`, `AI_BACKEND=mock`): ohne Kamera werden Platzhalter-Clips erzeugt und Demo-POS-Daten abgeglichen. Im UI **„Jetzt abgleichen“** klicken oder die Datei `data/pos/demo_transactions.csv` liegt bereits bereit.

## POS-Dateiformat

CSV (`;` oder `,`) mit Spalten-Aliassen:

| Bedeutung | mögliche Spalten |
|-----------|------------------|
| Bon-ID | `bon_id`, `id`, `transaction_id` |
| Zeit | `zeit`, `timestamp`, `datetime`, `datum` |
| Artikelanzahl | `anzahl_artikel`, `articles`, `artikel`, `anzahl` |
| Betrag (opt.) | `betrag`, `total`, `amount` |
| Kassierer (opt.) | `kassierer`, `cashier` |

Beispiel:

```csv
bon_id;zeit;anzahl_artikel;betrag;kassierer;kasse
B-1001;26.08.2026 22:22:05;5;42,90;Anna;Kasse-1
```

JSON-Arrays werden ebenfalls akzeptiert.

## Reolink anbinden

In `.env`:

```env
DEMO_MODE=false
AI_BACKEND=yolo
REOLINK_HOST=192.168.1.120
REOLINK_USER=admin
REOLINK_PASSWORD=geheim
REOLINK_CHANNEL=0
# optional:
# REOLINK_RTSP_URL=rtsp://admin:geheim@192.168.1.120:554/h264Preview_01_main
LOOKBACK_SECONDS=5
CLIP_DURATION_SECONDS=12
```

Die Kamera/NVR muss vom Docker-Host erreichbar sein (HTTP-API + ggf. RTSP Port 554). Aufnahmen werden per Reolink-Search/Download geholt; Fallback ist RTSP-Grab.

## KI-Backends

| `AI_BACKEND` | Beschreibung |
|--------------|--------------|
| `yolo` (**Standard**) | lokales **YOLOv8n** (`ultralytics`) – zählt konfigurierbare COCO-Artikelklassen in Videoframes |
| `openai` | GPT-Vision (`OPENAI_API_KEY`), oft besser für gemischte Waren |
| `mock` | nur für Demos ohne Modell |

Beim Start mit `yolo` wird `yolov8n.pt` einmalig geladen (Warmup). Weights liegen unter `data/models/`.

## Image ohne Compose

```bash
docker build -t pos-video-guard .
docker run --rm -p 8088:8000 \
  -e DEMO_MODE=true -e AI_BACKEND=mock \
  -v $(pwd)/data/pos:/data/pos \
  -v guard-data:/data \
  pos-video-guard
```

## API (Kurz)

- `GET /api/health`
- `POST /api/scan` — POS-Ordner einlesen + pending abgleichen
- `POST /api/upload-pos` — CSV/JSON hochladen
- `GET /api/incidents` — Vorfälle (`?status=open`)
- `POST /api/incidents/{id}/review` — `{ "status": "false_alarm"|"theft"|"open", "notes": "..." }`
- `GET /api/media/clip/{id}` — Videoausschnitt

## Hinweise

- Zeitstempel von Kasse und Kamera sollten synchron sein (NTP).
- `LOOKBACK_SECONDS` an euren Scan-/Bag-Workflow anpassen.
- YOLO ist ein Startpunkt; für zuverlässige Diebstahl-Erkennung Kamera so positionieren, dass Band/Tasche gut sichtbar ist, und Backend ggf. auf Vision-LLM stellen.
