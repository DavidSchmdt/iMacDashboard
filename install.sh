#!/usr/bin/env bash
# Flur-Dashboard – Installer für Linux Mint XFCE (ohne root).
#
#   curl -fsSL https://raw.githubusercontent.com/DavidSchmdt/iMacDashboard/main/install.sh | bash
#
# Beim ersten Mal fragt der Installer nach Ort, Bahnhof und Müllkalender (oder einem Setup-Code).
# Diese Angaben landen nur in ~/.config/imac-dashboard/config.json auf diesem Rechner.
#
# Optionen (bei "| bash" so übergeben:  ... | bash -s -- --reconfigure):
#   --reconfigure    Einstellungen neu abfragen
#   --setup CODE     Setup-Code direkt übergeben (sonst wird danach gefragt)
#   --autologin      LightDM-Autologin für diesen Benutzer einrichten (fragt nach sudo)
#   --from-dir DIR   aus lokalem Ordner statt von GitHub installieren (Tests)
#   --no-start       Kiosk nach der Installation nicht sofort starten
set -euo pipefail

REPO="${DASH_REPO:-DavidSchmdt/iMacDashboard}"   # <- GitHub "benutzer/repo"
BRANCH="${DASH_BRANCH:-main}"
DASH_HOME="${DASH_HOME:-$HOME/imac-dashboard}"
APP="$DASH_HOME/app"

AUTOLOGIN=0; FROM_DIR=""; START=1; SETUP_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --autologin) AUTOLOGIN=1 ;;
    --from-dir) FROM_DIR="$2"; shift ;;
    --no-start) START=0 ;;
    --reconfigure) SETUP_ARGS=(--reconfigure) ;;
    --setup) SETUP_ARGS=(--code "$2"); shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Bitte als normaler Benutzer ausführen, nicht als root."
[ "$(uname -s)" = "Linux" ] || die "Dieses Skript ist für Linux (Mint XFCE)."

# ---- Voraussetzungen ----------------------------------------------------
command -v python3 >/dev/null || die "python3 fehlt:  sudo apt install python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 6))' || die "python3 >= 3.6 nötig."
command -v tar >/dev/null || die "tar fehlt."
if command -v curl >/dev/null; then DL="curl -fsSL --retry 3 -o"; elif command -v wget >/dev/null; then DL="wget -q -O"; else die "curl oder wget nötig."; fi
BROWSER=""
for b in chromium chromium-browser google-chrome firefox; do command -v "$b" >/dev/null && { BROWSER="$b"; break; }; done
[ -n "$BROWSER" ] || die "Kein Browser gefunden:  sudo apt install firefox"
say "Browser: $BROWSER, Python: $(python3 -V 2>&1)"

# ---- Code holen ---------------------------------------------------------
mkdir -p "$DASH_HOME/cache" "$DASH_HOME/logs"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -n "$FROM_DIR" ]; then
  say "Kopiere aus $FROM_DIR"
  mkdir -p "$TMP/src" && cp -R "$FROM_DIR"/. "$TMP/src/"
else
  say "Lade $REPO ($BRANCH)"
  $DL "$TMP/src.tgz" "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" || die "Download fehlgeschlagen."
  mkdir -p "$TMP/src" && tar -xzf "$TMP/src.tgz" -C "$TMP/src" --strip-components=1
fi
[ -f "$TMP/src/server.py" ] && [ -f "$TMP/src/web/index.html" ] || die "Paket unvollständig."
python3 -m py_compile "$TMP/src/server.py" || die "server.py lässt sich nicht kompilieren."
rm -rf "$TMP/src/.git"
if [ -d "$APP" ]; then rm -rf "$DASH_HOME/app.prev"; mv "$APP" "$DASH_HOME/app.prev"; fi
mv "$TMP/src" "$APP"
chmod +x "$APP"/*.sh

cat > "$DASH_HOME/repo.env" <<EOF
REPO="$REPO"
BRANCH="$BRANCH"
EOF
# Kurzbefehle: ~/imac-dashboard/setup.sh (Einstellungen ändern), diagnose.sh (Fehlerbericht)
for s in setup.sh diagnose.sh; do printf '#!/bin/sh\nexec "%s" "$@"\n' "$APP/$s" > "$DASH_HOME/$s"; chmod +x "$DASH_HOME/$s"; done

# Lokale Einstellungen (Ort, Bahnhof, Müll) – nur auf diesem Rechner, nie im Repo
python3 "$APP/setup.py" "${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"}" || die "Einrichtung fehlgeschlagen."

# ---- Autostart (XFCE) ---------------------------------------------------
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/imac-dashboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Flur-Dashboard
Comment=Startet Server und Kiosk-Browser
Exec=$APP/kiosk.sh
X-GNOME-Autostart-enabled=true
Hidden=false
EOF

# ---- Bildschirm nie abschalten / nicht sperren ---------------------------
if command -v xfconf-query >/dev/null; then
  set_xf() { xfconf-query -c "$1" -p "$2" -n -t "$3" -s "$4" 2>/dev/null || true; }
  set_xf xfce4-power-manager /xfce4-power-manager/dpms-enabled bool false
  set_xf xfce4-power-manager /xfce4-power-manager/blank-on-ac int 0
  set_xf xfce4-power-manager /xfce4-power-manager/dpms-on-ac-sleep int 0
  set_xf xfce4-power-manager /xfce4-power-manager/dpms-on-ac-off int 0
  set_xf xfce4-power-manager /xfce4-power-manager/inactivity-on-ac int 0
  set_xf xfce4-power-manager /xfce4-power-manager/lock-screen-suspend-hibernate bool false
  set_xf xfce4-screensaver /saver/enabled bool false
  set_xf xfce4-screensaver /lock/enabled bool false
  set_xf xfce4-session /shutdown/LockScreen bool false
  set_xf xfwm4 /general/use_compositing bool false   # spart CPU auf alter Hardware
fi
# light-locker / Bildschirmschoner-Autostarts für diesen Benutzer abschalten
for f in light-locker xfce4-screensaver xscreensaver; do
  if [ -f "/etc/xdg/autostart/$f.desktop" ]; then
    printf '[Desktop Entry]\nHidden=true\n' > "$HOME/.config/autostart/$f.desktop"
  fi
done

# ---- optional: Autologin --------------------------------------------------
if [ "$AUTOLOGIN" = 1 ]; then
  say "Richte LightDM-Autologin für $USER ein (sudo)"
  printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$USER" \
    | sudo tee /etc/lightdm/lightdm.conf.d/70-imac-dashboard.conf >/dev/null
fi

say "Installiert nach $APP (Version $(cat "$APP/VERSION"))"
echo "    Einstellungen ändern: ~/imac-dashboard/setup.sh"
echo "    Fehlerbericht:        ~/imac-dashboard/diagnose.sh"
echo "    Logs:          $DASH_HOME/logs/"
echo "    Updates:       automatisch alle 6 Stunden von github.com/$REPO"

if [ "$START" = 1 ] && [ -n "${DISPLAY:-}" ]; then
  say "Starte Kiosk …"
  pkill -f "$APP/kiosk.sh" 2>/dev/null || true
  nohup "$APP/kiosk.sh" >/dev/null 2>&1 &
elif [ "$START" = 1 ]; then
  echo "    Kein Bildschirm in dieser Sitzung (SSH?) – startet beim nächsten Login, oder:"
  echo "    DISPLAY=:0 nohup $APP/kiosk.sh >/dev/null 2>&1 &"
fi
