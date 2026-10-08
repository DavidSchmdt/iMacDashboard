#!/usr/bin/env bash
# Entfernt Autostart und Programm. Einstellungen bleiben, außer mit --all.
set -u
DASH_HOME="${DASH_HOME:-$HOME/imac-dashboard}"
pkill -f "$DASH_HOME/app/kiosk.sh" 2>/dev/null
pkill -f "$DASH_HOME/app/server.py" 2>/dev/null
rm -f "$HOME/.config/autostart/imac-dashboard.desktop"
for f in light-locker xfce4-screensaver xscreensaver; do
  grep -qx 'Hidden=true' "$HOME/.config/autostart/$f.desktop" 2>/dev/null && rm -f "$HOME/.config/autostart/$f.desktop"
done
if [ "${1:-}" = "--all" ]; then rm -rf "$DASH_HOME" "$HOME/.config/imac-dashboard"; else rm -rf "$DASH_HOME/app" "$DASH_HOME/app.prev" "$DASH_HOME/app.failed"; fi
echo "Entfernt. Autologin (falls eingerichtet): sudo rm /etc/lightdm/lightdm.conf.d/70-imac-dashboard.conf"
