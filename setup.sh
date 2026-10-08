#!/bin/sh
# Einstellungen ändern ohne Neuinstallation:  ~/imac-dashboard/setup.sh
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
python3 "$APP/setup.py" --menu "$@" || exit 1
# Server neu starten (kiosk.sh startet ihn sofort wieder), die Anzeige holt sich die neuen Werte selbst
pkill -f "$DASH_HOME/app/server.py" 2>/dev/null && echo "Gespeichert. Die Anzeige ist in etwa einer Minute aktuell."
