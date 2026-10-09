#!/bin/sh
# "Dashboard starten": nach "Zum Desktop" wieder ins Dashboard (oder Kiosk neu starten, falls er nicht läuft)
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
rm -f "$DASH_HOME/.stopped"
if ! pgrep -f "$APP/kiosk.sh" >/dev/null 2>&1; then
  DASH_HOME="$DASH_HOME" setsid nohup "$APP/kiosk.sh" >/dev/null 2>&1 &
fi
echo "Dashboard startet …"
