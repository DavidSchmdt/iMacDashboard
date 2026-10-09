#!/usr/bin/env bash
# Startet beim XFCE-Login: Server (mit Neustart-Schleife), Update-Prüfung und
# den Browser im Kiosk-Modus. Läuft nur einmal pro Sitzung.
#   kiosk.sh               Login/Start: Dashboard soll laufen
#   kiosk.sh --keep-state  Neustart des Skripts (z. B. nach Update): "Zum Desktop" bleibt bestehen
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
LOGS="$DASH_HOME/logs"
PORT="${DASH_PORT:-8787}"
URL="http://127.0.0.1:$PORT/"
export DASH_HOME
mkdir -p "$LOGS"
[ -f "$DASH_HOME/kiosk.env" ] && . "$DASH_HOME/kiosk.env"   # z.B. DASH_BROWSER=firefox, DASH_BROWSER_FLAGS="--disable-gpu"

STOP="$DASH_HOME/.stopped"            # gesetzt = bewusst zum Desktop gewechselt, Browser nicht neu öffnen
RESTART="$DASH_HOME/.restart-browser"  # gesetzt = Browser absichtlich beendet (Update), sofort neu öffnen

# Ältere Instanz (z. B. aus der Version vor einem Update) ablösen – samt ihrer Warte-Schleifen
for p in $(pgrep -f "$APP/kiosk.sh"); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
pkill -f "sleep 21600" 2>/dev/null; pkill -f "sleep 1800" 2>/dev/null
pkill -f "$DASH_HOME/browser-profile" 2>/dev/null
pkill -f "$APP/server.py" 2>/dev/null
sleep 2

# Bewusst kein flock: Kindprozesse älterer Versionen erben die Sperre und hielten sie sonst fest.
# Eine einzige Instanz ist gesichert, weil jede neue alle älteren oben beendet.
[ "${1:-}" = "--keep-state" ] || rm -f "$STOP"
rm -f "$RESTART"
python3 -c "import hashlib,sys; print(hashlib.sha1(open(sys.argv[1],'rb').read()).hexdigest())" "$APP/kiosk.sh" > "$DASH_HOME/.kiosk-hash"
export MOZ_CRASHREPORTER_DISABLE=1   # nach Absturz kein Dialog, einfach neu starten

# Logs klein halten
for f in "$LOGS"/*.log; do [ -f "$f" ] && [ "$(wc -c <"$f")" -gt 2000000 ] && tail -c 500000 "$f" >"$f.tmp" && mv "$f.tmp" "$f"; done

# Bildschirm nie schwarz (zusätzlich zu den xfconf-Einstellungen des Installers)
xset s off 2>/dev/null; xset s noblank 2>/dev/null; xset -dpms 2>/dev/null

# Server: bei Absturz oder nach Update (update.sh beendet ihn) neu starten
pkill -f "$APP/server.py" 2>/dev/null
( while true; do
    python3 "$DASH_HOME/app/server.py" >>"$LOGS/server.log" 2>&1
    sleep 5
  done ) &

# Updates: 5 min nach Login, dann alle 30 min (nur die kleine VERSION-Datei; das Paket nur bei neuer Version)
( sleep 300
  while true; do
    "$DASH_HOME/app/update.sh" >>"$LOGS/update.log" 2>&1
    sleep 1800
  done ) &

# Nachts Bildschirm aus und kein Abdunkeln durch die Energieverwaltung: macht der Server (Display),
# damit Änderungen mit dem Auto-Update sofort greifen.

for _ in $(seq 60); do
  python3 -c "import urllib.request as u; u.urlopen('${URL}healthz', timeout=2)" 2>/dev/null && break
  sleep 1
done

pick_browser() {
  if [ -n "${DASH_BROWSER:-}" ]; then echo "$DASH_BROWSER"; return; fi
  for b in chromium chromium-browser google-chrome firefox; do command -v "$b" >/dev/null && { echo "$b"; return; }; done
}
BROWSER="$(pick_browser)"
PROFILE="$DASH_HOME/browser-profile"
mkdir -p "$PROFILE"

run_browser() {
  case "$BROWSER" in
    firefox*)
      cat >"$PROFILE/user.js" <<'EOF'
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRunURL", "");
user_pref("browser.translations.automaticallyPopup", false);
user_pref("browser.tabs.warnOnClose", false);
user_pref("app.update.auto", false);
user_pref("app.update.enabled", false);
// Stromsparen: kein weiches Scrollen, keine Oberflächen-Animationen, keine Hintergrunddienste
user_pref("general.smoothScroll", false);
user_pref("ui.prefersReducedMotion", 1);
user_pref("toolkit.cosmeticAnimations.enabled", false);
user_pref("image.animation_mode", "once");
user_pref("app.normandy.enabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("extensions.pocket.enabled", false);
user_pref("browser.newtabpage.enabled", false);
EOF
      "$BROWSER" --kiosk --no-remote --profile "$PROFILE" "$URL" ;;
    *)
      # Absturz-Hinweis "Wiederherstellen?" verhindern
      sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' \
        "$PROFILE/Default/Preferences" 2>/dev/null
      "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --no-first-run \
        --disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication \
        --password-store=basic --check-for-update-interval=31536000 \
        --disable-smooth-scrolling --force-prefers-reduced-motion --disable-sync --disable-background-networking \
        --disable-component-update --disable-default-apps --disable-breakpad \
        --overscroll-history-navigation=0 --disable-pinch ${DASH_BROWSER_FLAGS:-} --user-data-dir="$PROFILE" "$URL" ;;
  esac
}

# Optional zurück zum Dashboard nach X Minuten am Desktop (kiosk.return_after_min, 0 = nie)
return_after_min() {
  python3 -c "import json,urllib.request as u; print(int(json.load(u.urlopen('${URL}api/all', timeout=5)).get('kiosk', {}).get('return_after_min') or 0))" 2>/dev/null || echo 0
}
idle_seconds() {  # Leerlauf von Maus/Tastatur, sonst Zeit seit dem Verlassen
  if command -v xprintidle >/dev/null; then echo $(( $(xprintidle) / 1000 )); else echo $(( $(date +%s) - $(stat -c %Y "$STOP" 2>/dev/null || date +%s) )); fi
}

while true; do
  if [ -f "$STOP" ]; then
    sleep 10
    mins="$(return_after_min)"
    if [ "$mins" -gt 0 ] && [ "$(idle_seconds)" -ge $(( mins * 60 )) ]; then rm -f "$STOP"; fi
    continue
  fi
  run_browser >>"$LOGS/browser.log" 2>&1
  rc=$?
  if [ -f "$RESTART" ]; then rm -f "$RESTART"; sleep 2; continue; fi   # Update/Rollback: gleich wieder öffnen
  [ -f "$STOP" ] && continue                                           # "Zum Desktop"-Knopf oder Esc
  if [ "$rc" -eq 0 ]; then date +%s > "$STOP"; continue; fi             # bewusst geschlossen (Strg+Q, Fenster zu)
  echo "$(date '+%F %T') Browser beendet mit Code $rc – Neustart" >>"$LOGS/browser.log"
  sleep 5                                                              # Absturz: neu öffnen
done
