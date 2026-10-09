#!/bin/sh
# "Dashboard aktualisieren": sofort nach einer neuen Version suchen (sonst automatisch alle 30 Minuten)
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
echo "Installiert: Version $(cat "$APP/VERSION" 2>/dev/null) – suche nach Updates …"
DASH_HOME="$DASH_HOME" "$APP/update.sh" 2>&1 | tee -a "$DASH_HOME/logs/update.log"
echo "Ergebnis: $(cut -f2 "$DASH_HOME/.update-status" 2>/dev/null)  ·  jetzt: Version $(cat "$DASH_HOME/app/VERSION" 2>/dev/null)"
[ "${1:-}" = "--pause" ] && { printf "Enter zum Schließen "; read -r _; }
exit 0
