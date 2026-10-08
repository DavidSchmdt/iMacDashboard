#!/bin/sh
# Kurzbericht zum Weiterschicken (keine Zugangsdaten):  ~/imac-dashboard/diagnose.sh
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}" DISPLAY="${DISPLAY:-:0}" exec python3 "$APP/server.py" --diagnose
