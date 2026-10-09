#!/usr/bin/env bash
# Selbst-Update: vergleicht VERSION mit GitHub, tauscht app/ aus, startet den
# Server neu und rollt zurück, falls die neue Version nicht hochkommt.
# Die Seite lädt sich von selbst neu, sobald der Server eine neue Version meldet.
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
APP="$DASH_HOME/app"
PORT="${DASH_PORT:-8787}"
. "$DASH_HOME/repo.env" 2>/dev/null || { echo "repo.env fehlt"; exit 1; }
ts() { date '+%F %T'; }
# Ergebnis der letzten Prüfung für die Ecke der Anzeige und das Menü "Dashboard aktualisieren"
RESULT="geprüft"
note() { printf '%s\t%s\n' "$(date +%s)" "$RESULT" > "$DASH_HOME/.update-status" 2>/dev/null; }

fetch() {  # fetch URL DATEI
  if command -v curl >/dev/null; then curl -fsSL --retry 2 -m 120 -o "$2" "$1"; else wget -q -T 120 -O "$2" "$1"; fi
}
healthy_version() {
  python3 -c "import json,urllib.request as u; print(json.load(u.urlopen('http://127.0.0.1:$PORT/api/all', timeout=5))['version'])" 2>/dev/null
}
page_version() {  # Version, die die Seite im Browser zuletzt (< 3 min) als "läuft" gemeldet hat
  python3 -c "
import json, time, urllib.request as u
p = json.load(u.urlopen('http://127.0.0.1:$PORT/api/all', timeout=5)).get('page') or {}
print(p.get('version') or '' if time.time() - p.get('at', 0) < 180 else '')" 2>/dev/null
}
rollback() {
  echo "$(ts) $1 – zurück auf $LOCAL"
  rm -rf "$DASH_HOME/app.failed"; mv "$APP" "$DASH_HOME/app.failed"; mv "$DASH_HOME/app.prev" "$APP"
  pkill -f "$DASH_HOME/app/server.py"
  # Eine kaputte Seite kann sich nicht selbst neu laden: Browser beenden, kiosk.sh öffnet ihn neu.
  [ -n "${BROWSER_UP:-}" ] && { touch "$DASH_HOME/.restart-browser"; pkill -f "$DASH_HOME/browser-profile"; }
  RESULT="Update auf $REMOTE fehlgeschlagen, alte Version läuft"
  exit 1
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"; note' EXIT
fetch "https://raw.githubusercontent.com/$REPO/$BRANCH/VERSION?$(date +%s)" "$TMP/VERSION" || { RESULT="GitHub nicht erreichbar"; echo "$(ts) $RESULT"; exit 0; }
REMOTE="$(tr -d '[:space:]' <"$TMP/VERSION")"
LOCAL="$(tr -d '[:space:]' 2>/dev/null <"$APP/VERSION")"
[ -n "$REMOTE" ] && [ "$REMOTE" != "$LOCAL" ] || { RESULT="aktuell"; exit 0; }
[ "$(tr -d '[:space:]' 2>/dev/null <"$DASH_HOME/app.failed/VERSION")" = "$REMOTE" ] && { RESULT="Version $REMOTE ausgelassen (war fehlerhaft)"; exit 0; }  # schon gescheitert

echo "$(ts) Update $LOCAL -> $REMOTE"
fetch "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" "$TMP/src.tgz" || { RESULT="Download fehlgeschlagen"; echo "$(ts) $RESULT"; exit 1; }
mkdir "$TMP/src" && tar -xzf "$TMP/src.tgz" -C "$TMP/src" --strip-components=1 || exit 1
[ -f "$TMP/src/server.py" ] && [ -f "$TMP/src/web/index.html" ] || { echo "$(ts) Paket unvollständig"; exit 1; }
python3 -m py_compile "$TMP/src/server.py" || { echo "$(ts) server.py defekt, Update abgebrochen"; exit 1; }
chmod +x "$TMP/src"/*.sh

# Läuft gerade ein Browser mit der Seite? Dann muss auch die neue Seite sich melden.
BROWSER_UP=""; [ -n "$(page_version)" ] && BROWSER_UP=1

rm -rf "$DASH_HOME/app.prev"
mv "$APP" "$DASH_HOME/app.prev" && mv "$TMP/src" "$APP"
pkill -f "$DASH_HOME/app/server.py"   # kiosk.sh startet ihn mit dem neuen Code neu

UP=""
for _ in $(seq 45); do
  sleep 2
  [ "$(healthy_version)" = "$REMOTE" ] && { UP=1; break; }
done
[ -n "$UP" ] || rollback "Neuer Server startet nicht"

if [ -n "$BROWSER_UP" ]; then
  # Die Seite fragt jede Minute nach, sieht die neue Version und lädt neu.
  for _ in $(seq 60); do
    sleep 3
    [ "$(page_version)" = "$REMOTE" ] && { RESULT="aktualisiert auf $REMOTE"; echo "$(ts) Update auf $REMOTE ok (Seite läuft)"; exit 0; }
  done
  rollback "Neue Seite meldet sich nicht (HTML/CSS/JS kaputt?)"
fi
echo "$(ts) Update auf $REMOTE ok (kein Browser offen, Seite ungeprüft)"
RESULT="aktualisiert auf $REMOTE"
exit 0
