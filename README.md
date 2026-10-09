# Flur-Dashboard

Ruhige Info-Wand für einen alten Rechner im Flur (getestet für Linux Mint XFCE): Uhr, Wetter,
Abfahrten am nächsten Bahnhof (live mit Verspätungen), nächste Müllabfuhr, Nachrichten DE/EN,
Katzen-Memes und Ladebildschirm-Tipps im Videospiel-Stil.

Eine HTML/CSS/JS-Seite plus ein kleiner Python-3-Server (nur Standardbibliothek, ab 3.6).
Keine Frameworks, kein Node, keine API-Schlüssel nötig.

**Das Repo enthält keine Ortsangaben.** Ort, Bahnhof und Müllkalender stehen nur in
`~/.config/imac-dashboard/config.json` auf dem Gerät selbst. Der Installer legt die Datei an.

## Kurzanleitung für den Haushalt

- **Zum Desktop:** Maus bewegen → Knopf „Zum Desktop“ oben rechts (oder Taste `Esc`). Das Dashboard bleibt dann zu.
- **Zurück zum Dashboard:** Symbol „Dashboard starten“ auf dem Schreibtisch oder im Menü – oder einfach neu anmelden.
- **Aktualisieren:** geht alle 30 Minuten von selbst; sofort: Symbol „Dashboard aktualisieren“ oder `~/imac-dashboard/update.sh`.
- **Einstellungen** (Ort, Abfahrten, Müll, hell/dunkel, Nacht): Menü „Dashboard-Einstellungen“ oder `~/imac-dashboard/setup.sh`.
- **Etwas geht nicht:** Menü „Dashboard-Diagnose“ oder `~/imac-dashboard/diagnose.sh`, Ausgabe an David schicken.
- Version und letzte Update-Prüfung stehen klein unten rechts auf dem Dashboard.

## Installation

Im Terminal (als normaler Benutzer, nicht root):

```bash
curl -fsSL https://raw.githubusercontent.com/DavidSchmdt/iMacDashboard/main/install.sh | bash
```

Beim ersten Mal fragt der Installer im Terminal (kurze Antworten, meist nur eine Nummer):
1. **Ort** fürs Wetter – Vorschlag anhand der Internetadresse, sonst Ortsnamen tippen.
2. **Bahnhof** – Liste der Bahnhöfe im Umkreis von 6 km (OpenStreetMap), Nummer wählen; optional Linien
   (`S1 S2`) und Richtungen (Stichwörter der Ziele).
3. **Müllabfuhr** – Website der Abfallwirtschaft tippen (z. B. vom Abfallkalender-Flyer). Der Installer findet das
   Abfuhrtermine-Portal oder den iCal-Link selbst, zeigt Gemeinden und Straßen zur Auswahl (Anfang tippen),
   fragt die Hausnummer und welche Tonnen angezeigt werden, und prüft die Adresse gleich.

Alternativ einen privat vorbereiteten **Setup-Code** einfügen (siehe unten).

Das Skript
- installiert nach `~/imac-dashboard/` (Programm in `app/`, `cache/`, `logs/`),
- legt einen XFCE-Autostart an (`~/.config/autostart/imac-dashboard.desktop`),
- schaltet Bildschirmschoner, Abdunkeln und Sperre ab,
- startet sofort Server und Browser im Kiosk-Modus (Chromium, falls installiert, sonst Firefox).

**Später ändern ohne Neuinstallation:** `~/imac-dashboard/setup.sh` – zeigt die aktuellen Einstellungen und fragt,
was geändert werden soll (Wetter-Ort, Abfahrten/Richtungen, Müllabfuhr, Nacht & Helligkeit).

**Fehlerbericht:** `~/imac-dashboard/diagnose.sh` – Status aller Kacheln, Verbindungstest, Müllkalender-Test und letzte
Fehler, ohne Zugangsdaten. Die Ausgabe kann man so weiterschicken.

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
| `trains` | `station_name`, `opendata_id` (transport.opendata.ch) und/oder `iris_eva` (DB IRIS), `lines`, `groups`: `"auto"` (Standard: zwei Listen je Fahrtrichtung, aus der Lage des nächsten Halts berechnet), `"none"` (eine Liste) oder eigene Gruppen per Regex auf das Ziel; `rename` (Ziele kürzen) |
| `waste` | `provider: "ics"` mit `ics_url`, oder `provider: "athos"` (Abfuhrtermine-Portal mit WasteManagementServlet) mit `portal_url`, `ort`, `strasse`, `hausnummer`, `containers`; `types` legt fest, welche Abfuhren gezeigt werden |
| `night` | `from`, `to` (Standard 22:30–6:00), `mode`: `"off"` (Standard: Bildschirm nachts per DPMS aus; eine Mausbewegung weckt ihn für 5 Minuten) oder `"dim"` (nur abdunkeln); `dim` Stärke der Abdunkelung (0–0.9, Standard 0.35); `dim_before` Minuten vor `from`, in denen leicht abgedunkelt wird (Standard 20) |
| `kiosk` | `return_after_min`: nach „Zum Desktop“ so viele Minuten ohne Maus/Tastatur automatisch zurück ins Dashboard (Standard 0 = nie) |
| `display` | `theme`: `"hell"` (Standard: heller, kontrastreicher Look für den Blick im Vorbeigehen) oder `"dunkel"`; `brightness`, `contrast` der ganzen Anzeige (1.0 = normal) |
| `images` | `"reddit"` (Katzen-Memes, bei Mangel aufgefüllt mit TheCatAPI/cataas.com) oder `"cats"` (nur Katzenbild-Dienste) |
| `reddit` | optional `client_id`/`client_secret` (App-Typ „script“): verlässlicher NSFW-/Spoiler-/Flair-Filter |

Nach einer Änderung den Server neu starten: `pkill -f imac-dashboard/app/server.py` (startet von selbst wieder).

## Wartung: keine

- **Updates:** 5 Minuten nach dem Login und dann alle 30 Minuten wird `VERSION` im Repo geprüft (eine winzige Datei). Bei einer neuen Version wird das Paket
  geladen, geprüft und ausgetauscht. Startet der neue Server nicht, oder meldet sich die neue Seite im Browser nicht innerhalb von
  3 Minuten (JavaScript-Fehler, CSS fehlt), geht es automatisch auf die alte Version zurück, und der Browser startet neu.
- **Abstürze:** Server und Browser starten nach 5 Sekunden neu. Die Seite lädt sich alle 6 Stunden neu.
- **Ausfälle:** Jede Quelle merkt sich ihren letzten guten Stand auf der Platte. Fällt eine Quelle aus, zeigt die Kachel diesen Stand
  weiter an, und der Hinweis wechselt auf „Stand 12:40 · Quelle gestört“.

### Stromsparen

Die Seite zeichnet nur in einem einzigen Takt alle 10 Sekunden neu: keine CSS-Animationen, keine Übergänge, Uhr ohne
Sekunden, Bilder werden vor dem Tausch fertig dekodiert. Ist der Bildschirm nachts aus, ruht die Seite, und der Server
pausiert seine Abrufe bis 10 Minuten vor dem Morgen. Der Kiosk-Browser läuft ohne weiches Scrollen und ohne Hintergrunddienste.
Wer auf alter Hardware Grafikprobleme hat, kann in `~/imac-dashboard/kiosk.env` z. B. `DASH_BROWSER_FLAGS="--disable-gpu"` setzen.

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
| Katzenbild | Kuratierte SFW-Katzen-Subreddits (Memes und normale Katzen, 1 Abruf alle 75 s im Wechsel) und jedes dritte Bild von TheCatAPI / cataas.com; fällt Reddit aus, nur die Katzen-Dienste. Kein Bild wiederholt sich innerhalb von 3 Tagen | 5 min Wechsel |
| Tipps | `web/tips.json`: kuratierte Ladebildschirm-Tipps (DE/EN), gemischt ohne Wiederholung | 20 s Wechsel |

**Bild-Auswahl:** Der Server wählt das nächste Bild (`/api/next-image`) zufällig unter den zehn „heißesten“ noch nicht gezeigten und merkt sich gezeigte Bilder 3 Tage lang (`cache/seen-images.json`). Pro Subreddit wird eine „Hitze“ berechnet, also wie schnell ein Beitrag im Verhältnis zu seinem Alter steigt.
Mit Punktzahl ist das Punkte / (Alter + 1,5 h)^1,4. Ohne Punktzahl dient die Position in „hot“ geteilt durch das Alter als Ersatz.
Danach wird mit dem Median des Subreddits normiert. Höchstens 10 Kandidaten kommen aus demselben Sub.
Gefiltert werden over_18, Spoiler, angepinnte Beiträge und Titel/Flair mit NSFW- oder Politik-Stichwörtern.

**Tipps:** Eine Zeile pro Tipp in `web/tips.json`. Keine Namen, keine Orte. Die Kachel zeigt „Tipp“/„Lädt“ bei deutschen und
„Tip“/„Loading“ bei englischen Tipps. Jeder Tipp kommt einmal dran, bevor der Stapel neu gemischt wird.

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
