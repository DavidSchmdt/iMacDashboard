#!/usr/bin/env bash
# Startet beim XFCE-Login: Server (mit Neustart-Schleife), Update-Prüfung und
# den Browser im Kiosk-Modus. Läuft nur einmal pro Sitzung.
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
DASH_HOME="${DASH_HOME:-$(dirname "$APP")}"
LOGS="$DASH_HOME/logs"
PORT="${DASH_PORT:-8787}"
URL="http://127.0.0.1:$PORT/"
export DASH_HOME
mkdir -p "$LOGS"
[ -f "$DASH_HOME/kiosk.env" ] && . "$DASH_HOME/kiosk.env"   # z.B. DASH_BROWSER=firefox, DASH_BROWSER_FLAGS="--disable-gpu"

exec 9>"$DASH_HOME/.kiosk.lock"
if command -v flock >/dev/null && ! flock -n 9; then echo "läuft bereits"; exit 0; fi

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

# Updates: 5 min nach Login, dann alle 6 h
( sleep 300
  while true; do
    "$DASH_HOME/app/update.sh" >>"$LOGS/update.log" 2>&1
    sleep 21600
  done ) &

# Nachts Bildschirm aus, falls "night": {"mode": "off"}. Eine Mausbewegung weckt ihn;
# nach 5 Minuten ohne Bewegung schaltet DPMS ihn wieder ab. Morgens: DPMS ganz aus.
( last=""
  while true; do
    want="$(python3 -c "import json,urllib.request as u; d=json.load(u.urlopen('${URL}api/all', timeout=5)); print('off' if d['night'].get('mode') == 'off' and d.get('night_now') else 'on')" 2>/dev/null)"
    if [ -n "$want" ] && [ "$want" != "$last" ]; then
      if [ "$want" = off ]; then xset +dpms; xset dpms 300 300 300; xset dpms force off
      else xset dpms force on; xset -dpms; xset s reset; fi 2>/dev/null
      last="$want"
    fi
    sleep 60
  done ) &

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

# Browser zu? Nach 5 s wieder öffnen.
while true; do
  run_browser >>"$LOGS/browser.log" 2>&1
  sleep 5
done
