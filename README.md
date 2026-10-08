# Flur-Dashboard

Ruhige Info-Wand für einen alten Rechner im Flur (getestet für Linux Mint XFCE): Uhr, Wetter,
Abfahrten am nächsten Bahnhof (live mit Verspätungen), nächste Müllabfuhr, Nachrichten DE/EN,
Reddit-Trends und Katzen-Memes.

Eine HTML/CSS/JS-Seite plus ein kleiner Python-3-Server (nur Standardbibliothek, ab 3.6).
Keine Frameworks, kein Node, keine API-Schlüssel nötig.

**Das Repo enthält keine Ortsangaben.** Ort, Bahnhof und Müllkalender stehen nur in
`~/.config/imac-dashboard/config.json` auf dem Gerät selbst. Der Installer legt die Datei an.

## Installation

Im Terminal (als normaler Benutzer, nicht root):

```bash
curl -fsSL https://raw.githubusercontent.com/DavidSchmdt/iMacDashboard/main/install.sh | bash
```

Beim ersten Mal fragt der Installer:
- **Setup-Code:** einen privat vorbereiteten Code einfügen (siehe unten). Dann ist alles sofort fertig.
- **oder Fragen:** Ort fürs Wetter, Bahnhof (optional nur bestimmte Linien), optional die iCal-Adresse des Müllkalenders.

Das Skript
- installiert nach `~/imac-dashboard/` (Programm in `app/`, `cache/`, `logs/`),
- legt einen XFCE-Autostart an (`~/.config/autostart/imac-dashboard.desktop`),
- schaltet Bildschirmschoner, Abdunkeln und Sperre ab,
- startet sofort Server und Browser im Kiosk-Modus (Chromium, falls installiert, sonst Firefox).

Neu einrichten: `... | bash -s -- --reconfigure`

### Setup-Code (privat)

Ein Setup-Code ist die komprimierte lokale `config.json`. Er wird **nie** ins Repo gelegt:

```bash
python3 setup.py --make-code meine-config.json   # auf dem eigenen Rechner, Datei außerhalb des Repos
```

Den ausgegebenen Code (`IMD1.…`) beim Installieren einfügen.

## Einstellungen (`~/.config/imac-dashboard/config.json`)

Nur Abweichungen eintragen; Standardwerte stehen in `server.py` (`DEFAULTS`).
`config.example.json` zeigt alle Felder mit erfundenen Werten.

| Feld | Bedeutung |
|---|---|
| `location` | `name`, `lat`, `lon` fürs Wetter |
| `trains` | `station_name`, `opendata_id` (transport.opendata.ch) und/oder `iris_eva` (DB IRIS), `lines`, `groups` (Richtungen per Regex auf das Ziel), `rename` (Ziele kürzen) |
| `waste` | `provider: "ics"` mit `ics_url`, oder `provider: "athos"` (Abfuhrtermine-Portal mit WasteManagementServlet) mit `portal_url`, `ort`, `strasse`, `hausnummer`, `containers`; `types` legt fest, welche Abfuhren gezeigt werden |
| `night` | `from`, `to`, `dim` (Abdunkelung), `mode`: `"dim"` oder `"off"` (Bildschirm nachts per DPMS aus) |
| `images` | `"reddit"` (Katzen-Memes, bei Mangel aufgefüllt mit TheCatAPI/cataas.com) oder `"cats"` (nur Katzenbild-Dienste) |
| `reddit` | optional `client_id`/`client_secret` (App-Typ „script“): verlässlicher NSFW-/Spoiler-/Flair-Filter |

Nach einer Änderung den Server neu starten: `pkill -f imac-dashboard/app/server.py` (startet von selbst wieder).

## Wartung: keine

- **Updates:** 5 Minuten nach dem Login und dann alle 6 Stunden wird `VERSION` im Repo geprüft. Bei einer neuen Version wird das Paket
  geladen, geprüft und ausgetauscht. Startet der neue Server nicht, oder meldet sich die neue Seite im Browser nicht innerhalb von
  3 Minuten (JavaScript-Fehler, CSS fehlt), geht es automatisch auf die alte Version zurück, und der Browser startet neu.
- **Abstürze:** Server und Browser starten nach 5 Sekunden neu. Die Seite lädt sich alle 6 Stunden neu.
- **Ausfälle:** Jede Quelle merkt sich ihren letzten guten Stand auf der Platte. Fällt eine Quelle aus, zeigt die Kachel diesen Stand
  weiter an, und der Hinweis wechselt auf „Stand 12:40 · Quelle gestört“.

### Vor jedem Release

1. Lokal starten (`DASH_HOME=/tmp/flur python3 server.py`) und `http://127.0.0.1:8787` im Browser ansehen.
2. Erst dann `VERSION` hochzählen und pushen.

Optische Fehler bei sonst laufender Seite (z. B. verrutschtes Layout) erkennt der automatische Rollback nicht.

## Quellen

| Kachel | Quelle | Takt |
|---|---|---|
| Wetter | Open-Meteo (ohne Schlüssel) | 15 min |
| Abfahrten | transport.opendata.ch (mit Prognosen), DB IRIS als Ersatz oder alleinige Quelle | 1 min |
| Müll | iCal des Abfallkalenders (direkt oder über das Abfuhrtermine-Portal) | 12 h |
| Nachrichten | tagesschau.de RSS, BBC World RSS | 10 min |
| Reddit | kuratierte SFW-Subreddits, 1 Abruf alle 75 s im Wechsel | ~20 min je Sub |
| Katzenbild | Reddit-Katzen-Memes; unter 6 Katzenbildern wird mit TheCatAPI / cataas.com aufgefüllt | 40 s Wechsel |

**Reddit-Auswahl:** Pro Subreddit wird eine „Hitze“ berechnet, also wie schnell ein Beitrag im Verhältnis zu seinem Alter steigt.
Mit Punktzahl ist das Punkte / (Alter + 1,5 h)^1,4. Ohne Punktzahl dient die Position in „hot“ geteilt durch das Alter als Ersatz.
Danach wird mit dem Median des Subreddits normiert. Höchstens 2 Beiträge kommen aus demselben Sub.
Gefiltert werden over_18, Spoiler, angepinnte Beiträge und Titel/Flair mit NSFW- oder Politik-Stichwörtern.

## Fehlersuche per SSH

```bash
tail -50 ~/imac-dashboard/logs/server.log     # Quellen-Fehler
cat ~/imac-dashboard/logs/update.log          # Updates
python3 ~/imac-dashboard/app/server.py --once # alle Quellen einmal abrufen und Status zeigen
DISPLAY=:0 ~/imac-dashboard/app/kiosk.sh &    # Kiosk von außen neu starten
~/imac-dashboard/app/uninstall.sh             # entfernen (--all löscht auch Einstellungen)
```

## Lokal testen

```bash
DASH_HOME=/tmp/flur DASH_CONFIG=/tmp/flur/config.json python3 server.py   # dann http://127.0.0.1:8787 öffnen
```
