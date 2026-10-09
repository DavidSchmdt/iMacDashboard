#!/usr/bin/env python3
"""Flur-Dashboard: tiny stdlib fetcher + proxy.

Serves web/ and a JSON snapshot of all sources at /api/all. Every source runs
in its own thread, caches its last good result on disk and keeps serving that
(marked stale) when the upstream fails. Python 3.6+ stdlib only.
"""
import email.utils
import hashlib
import html
import json
import os
import random
import re
import socketserver
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
HOME_DIR = os.environ.get("DASH_HOME", os.path.expanduser("~/imac-dashboard"))
CACHE_DIR = os.path.join(HOME_DIR, "cache")
IMG_DIR = os.path.join(CACHE_DIR, "img")
# Ortsangaben stehen NUR in dieser lokalen Datei (vom Installer angelegt, nie im Repo).
CONFIG_PATH = os.environ.get("DASH_CONFIG", os.path.expanduser("~/.config/imac-dashboard/config.json"))
LEGACY_CONFIG_PATH = os.path.join(HOME_DIR, "config.json")

# Neutrale Standardwerte: ohne lokale Konfiguration zeigen Ort-Kacheln "nicht eingerichtet".
DEFAULTS = {
    "port": 8787,
    "location": {"name": "", "lat": None, "lon": None},
    "trains": {
        "station_name": "",
        "opendata_id": "",   # transport.opendata.ch (Fahrplan der SBB, mit Prognosen)
        "iris_eva": "",      # DB IRIS (Deutschland); Fallback oder alleinige Quelle
        "lines": [],         # leer = alle Linien
        # Abfahrten nach Richtung trennen:
        #   "auto" = zwei Listen je Fahrtrichtung (aus der Lage des nächsten Halts berechnet)
        #   "none" = eine gemeinsame Liste
        #   [{"title": "Richtung A", "match": "regex auf Ziel"}, ...] = eigene Gruppen; Rest landet in der letzten
        "groups": "auto",
        "rename": {},        # Ziel-Anzeigenamen kürzen: {"Langer Name": "Kurz"}
    },
    "news": [
        {"name": "tagesschau", "lang": "de", "url": "https://www.tagesschau.de/index~rss2.xml"},
        {"name": "BBC World", "lang": "en", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    ],
    "waste": {
        # "ics": iCal-Adresse direkt (ics_url)
        # "athos": Abfuhrtermine-Portal mit WasteManagementServlet (portal_url, ort, strasse,
        #          hausnummer, containers = Nummern der Häkchen im Formular)
        "provider": "",
        "ics_url": "", "portal_url": "", "ort": "", "strasse": "", "hausnummer": "", "containers": [],
        # Welche Abfuhren gezeigt werden: Treffer im Termintitel -> Art
        "types": [
            {"kind": "rest", "label": "Restmüll", "match": "rest"},
            {"kind": "gelb", "label": "Gelber Sack", "match": "gelb|wertstoff|verpackung"},
            {"kind": "bio", "label": "Biotonne", "match": "bio"},
            {"kind": "papier", "label": "Papiertonne", "match": "papiertonne|altpapier|blaue tonne"},
        ],
    },
    "reddit": {
        # Kuratierter Pool für die Bild-Kachel, nur SFW, keine Politik. Gewicht = wie oft/gern gezeigt.
        "image_subs": {
            "catmemes": 1.5, "cats": 1.0, "IllegallySmolCats": 1.2, "catsareliquid": 1.0, "blep": 1.0,
            "Catloaf": 1.0, "SupermodelCats": 1.0, "StartledCats": 1.0, "Catswithjobs": 1.0, "catpics": 0.8,
        },
        "request_every_s": 75,
        # Optional, für verlässlichen SFW-Filter (over_18/spoiler/flair) und Punktzahlen:
        # Reddit-App vom Typ "script" anlegen und Client-ID/Secret hier eintragen.
        "client_id": "",
        "client_secret": "",
    },
    # "reddit" = Katzen-Memes von Reddit (Ersatz: Katzenbild-Dienste), "cats" = nur Katzenbild-Dienste
    "images": "reddit",
    # Nach "Zum Desktop" automatisch zurück ins Dashboard nach so vielen Minuten Leerlauf (0 = nie)
    "kiosk": {"return_after_min": 0},
    # from/to: Nachtzeit. mode "off": Bildschirm dann per DPMS aus (spart Strom); "dim": nur abdunkeln.
    # dim: Stärke der Abdunkelung (0 = keine, 1 = schwarz); dim_before: so viele Minuten vor "from" leicht abdunkeln
    "night": {"from": "22:30", "to": "06:00", "mode": "off", "dim": 0.35, "dim_before": 20},
    # Tagesbild: helles oder dunkles Thema; Helligkeit/Kontrast der ganzen Seite (1.0 = unverändert)
    "display": {"theme": "hell", "brightness": 1.0, "contrast": 1.0},  # theme: "hell" oder "dunkel"
    "update_check_hours": 6,
}

BLOCK_WORDS = re.compile(
    r"\b(nsfw|nsfl|spoiler|gore|nude|nudity|porn|sex|"
    r"trump|biden|harris|musk|putin|selensk\w*|zelensk\w*|netanyahu|hamas|gaza|israel|"
    r"ukrain\w*|russia\w*|election\w*|wahl\w*|democrat\w*|republican\w*|maga|afd|"
    r"politic\w*|politik\w*|president|präsident\w*|congress|senate|parliament|"
    r"war|krieg|shooting|killed|dead|death|died|abortion|immigra\w*|refugee\w*)\b",
    re.I,
)
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UA_SELF = "imac-flur-dashboard/1.0 (+private hallway display)"


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config():
    cfg = DEFAULTS
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = deep_merge(DEFAULTS, json.load(f))
    except FileNotFoundError:
        try:  # ältere Installationen
            with open(LEGACY_CONFIG_PATH, encoding="utf-8") as f:
                cfg = deep_merge(DEFAULTS, json.load(f))
        except Exception:
            log("Keine lokale Konfiguration (%s) – Ort-Kacheln bleiben leer" % CONFIG_PATH)
    except Exception as e:
        log("config.json unlesbar, nutze Standardwerte:", e)
    return cfg


def read_version():
    try:
        with open(os.path.join(APP_DIR, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "dev"


def http_get(url, ua=UA_SELF, timeout=25, data=None, headers=None, opener=None):
    h = {"User-Agent": ua, "Accept-Language": "de,en;q=0.8"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    resp = (opener.open if opener else urllib.request.urlopen)(req, timeout=timeout)
    with resp:
        return resp.read(), resp.headers


def parse_iso(s):
    """'2026-10-08T14:05:00+0200' or '...+02:00' or 'Z' -> aware datetime (3.6-safe)."""
    s = s.strip().replace("Z", "+0000")
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?([+-]\d\d):?(\d\d)$", s)
    if not m:
        raise ValueError("bad iso: " + s)
    y, mo, d, hh, mi, ss, oh, om = m.groups()
    sign = -1 if oh.startswith("-") else 1
    tz = timezone(sign * timedelta(hours=abs(int(oh)), minutes=int(om)))
    return datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0), tzinfo=tz)


def is_night(n):
    def mins(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    now = datetime.now()
    m, a, b = now.hour * 60 + now.minute, mins(n["from"]), mins(n["to"])
    return (m >= a or m < b) if a > b else (a <= m < b)


def update_status():
    try:
        with open(os.path.join(HOME_DIR, ".update-status"), encoding="utf-8") as f:
            at, result = f.read().strip().split("\t", 1)
        return {"at": int(at), "result": result}
    except (OSError, ValueError):
        return {"at": None, "result": None}


def night_paused(cfg):
    """Bildschirm nachts aus -> Abrufe pausieren; 10 min vor Ende wieder an, damit morgens alles frisch ist."""
    n = cfg["night"]
    if n.get("mode") != "off" or not is_night(n):
        return False
    h, m = n["to"].split(":")
    early = (int(h) * 60 + int(m) - 10) % 1440
    return not is_night({"from": "%02d:%02d" % (early // 60, early % 60), "to": n["to"]})


def local_tz():
    return datetime.now(timezone.utc).astimezone().tzinfo


# --------------------------------------------------------------------------
# Source framework


def friendly_error(e):
    """Fehler in einem Satz, den man im Flur versteht (die Rohmeldung steht in error/Log)."""
    import socket
    import ssl
    reason = getattr(e, "reason", None)
    if isinstance(e, urllib.error.HTTPError):
        return "Server antwortet mit Fehler %d" % e.code
    if isinstance(e, (socket.timeout, TimeoutError)) or isinstance(reason, socket.timeout) or "timed out" in str(e):
        return "Server antwortet nicht (Zeitüberschreitung)"
    if isinstance(e, ssl.SSLError) or isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(e).upper():
        return "Zertifikatsproblem (TLS) – Systemzeit und ca-certificates prüfen"
    if isinstance(reason, socket.gaierror) or "Name or service not known" in str(e) or "name resolution" in str(e):
        return "Adresse nicht gefunden – Internet/DNS prüfen"
    if isinstance(e, (ConnectionError, urllib.error.URLError)):
        return "Keine Verbindung zum Server"
    if "Adresse vom Portal nicht akzeptiert" in str(e):
        return "Adresse vom Portal nicht akzeptiert – Straße/Hausnummer prüfen (setup.sh)"
    if "kein iCal" in str(e) or "Terminliste" in str(e):
        return "Portal liefert keinen Kalender – Einstellungen prüfen (setup.sh)"
    return str(e)[:120]


class NotConfigured(Exception):
    """Quelle hat keine lokalen Einstellungen – Kachel zeigt 'nicht eingerichtet'."""


class Source(object):
    name = "source"
    interval = 600
    retry = 120

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = {"ok": False, "updated": None, "data": None, "error": None, "tried": None}
        self.cache_path = os.path.join(CACHE_DIR, self.name + ".json")
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            # Cache gilt als gut, bis ein echter Abruf scheitert; die Seite wertet zusätzlich das Alter aus
            self.state.update(data=cached["data"], updated=cached["updated"], ok=True)
        except Exception:
            pass

    def fetch(self):
        raise NotImplementedError

    def run_once(self):
        try:
            data = self.fetch()
            now = time.time()
            with self.lock:
                self.state.update(ok=True, data=data, updated=now, error=None, hint=None, tried=now)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"data": data, "updated": now}, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
            return True
        except NotConfigured:
            with self.lock:
                self.state.update(ok=True, configured=False, data=None, error=None, tried=time.time())
            return True
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            log("[%s] Fehler: %s" % (self.name, msg))
            if not isinstance(e, (urllib.error.URLError, OSError, ValueError)):
                traceback.print_exc()
            with self.lock:
                self.state.update(ok=False, error=msg[:200], hint=friendly_error(e), tried=time.time())
            return False

    def loop(self):
        time.sleep(random.uniform(0, 3))
        while True:
            # Nachtpause nur, wenn schon Daten da sind – sonst bliebe eine Kachel bis morgens leer
            if night_paused(self.cfg) and self.state.get("updated"):
                with self.lock:
                    self.state["paused"] = True
                time.sleep(60)
                continue
            self.state["paused"] = False
            ok = self.run_once()
            time.sleep(self.interval if ok else self.retry)

    def snapshot(self):
        with self.lock:
            return dict(self.state)


# --------------------------------------------------------------------------
# Weather (Open-Meteo, no key)


class Weather(Source):
    name = "weather"
    interval = 15 * 60

    def fetch(self):
        loc = self.cfg["location"]
        if loc.get("lat") is None or loc.get("lon") is None:
            raise NotConfigured()
        q = urllib.parse.urlencode({
            "latitude": loc["lat"], "longitude": loc["lon"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day,precipitation",
            "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            "timezone": "auto", "forecast_days": 4, "forecast_hours": 24,
        })
        raw, _ = http_get("https://api.open-meteo.com/v1/forecast?" + q)
        d = json.loads(raw.decode("utf-8"))
        hourly = d["hourly"]
        hours = []
        for i, t in enumerate(hourly["time"]):
            hours.append({
                "time": t, "temp": hourly["temperature_2m"][i], "code": hourly["weather_code"][i],
                "pop": hourly["precipitation_probability"][i], "day": hourly["is_day"][i],
            })
        daily = d["daily"]
        days = []
        for i, t in enumerate(daily["time"]):
            days.append({
                "date": t, "code": daily["weather_code"][i], "max": daily["temperature_2m_max"][i],
                "min": daily["temperature_2m_min"][i], "pop": daily["precipitation_probability_max"][i],
                "sunrise": daily["sunrise"][i], "sunset": daily["sunset"][i],
            })
        c = d["current"]
        return {
            "place": loc["name"],
            "now": {"temp": c["temperature_2m"], "feels": c["apparent_temperature"], "code": c["weather_code"],
                    "wind": c["wind_speed_10m"], "day": c["is_day"], "precip": c.get("precipitation")},
            "hours": hours, "days": days,
        }


# --------------------------------------------------------------------------
# Trains: transport.opendata.ch (primary), DB IRIS (fallback)


class Trains(Source):
    name = "trains"
    interval = 60
    retry = 45

    def __init__(self, cfg):
        Source.__init__(self, cfg)
        self.next_bearing = {}

    def fetch(self):
        t = self.cfg["trains"]
        if not (t.get("opendata_id") or t.get("iris_eva")):
            raise NotConfigured()
        try:
            if not t.get("opendata_id"):
                raise ValueError("keine opendata_id")
            deps = self.fetch_opendata(t)
            src = "transport.opendata.ch"
        except Exception as e:
            if not t.get("iris_eva"):
                raise
            log("[trains] opendata nicht nutzbar (%s), nutze DB IRIS" % e)
            deps = self.fetch_iris(t)
            src = "DB IRIS"
        lines = set(x.upper().replace(" ", "") for x in t["lines"])
        if lines:
            deps = [d for d in deps if d["line"].upper().replace(" ", "") in lines]
        deps.sort(key=lambda d: d["planned"])
        seen = set()  # dieselbe Fahrt steht in den Quelldaten manchmal doppelt
        deps = [d for d in deps if not ((d["line"], d["to"], d["planned"]) in seen or seen.add((d["line"], d["to"], d["planned"])))]
        deps = deps[:20]
        # Richtung der Halte merken (IRIS liefert keine Koordinaten, opendata schon)
        for d in deps:
            if d.get("bearing") is not None and d.get("next"):
                self.next_bearing[d["next"]] = d["bearing"]
            elif d.get("next") in self.next_bearing:
                d["bearing"] = self.next_bearing[d["next"]]
        dirs = assign_directions(deps, t["groups"], t["rename"])
        return {"station": t["station_name"], "source": src, "departures": deps, "dirs": dirs, "rename": t["rename"]}

    def fetch_opendata(self, t):
        url = "https://transport.opendata.ch/v1/stationboard?" + urllib.parse.urlencode(
            {"id": t["opendata_id"], "limit": 24})
        raw, _ = http_get(url)
        board = json.loads(raw.decode("utf-8"))["stationboard"]
        out = []
        for s in board:
            stop = s.get("stop") or {}
            if not stop.get("departure"):
                continue
            planned = parse_iso(stop["departure"])
            prog = (stop.get("prognosis") or {}).get("departure")
            delay = stop.get("delay")
            if prog:
                delay = int(round((parse_iso(prog) - planned).total_seconds() / 60.0))
            pl = s.get("passList") or []
            nxt = pl[1] if len(pl) > 1 else {}
            out.append({
                "line": "%s%s" % (s.get("category") or "", s.get("number") or ""),
                "to": s.get("to") or "",
                "next": ((nxt.get("station") or {}).get("name") or ""),
                "bearing": bearing((stop.get("station") or {}).get("coordinate"),
                                   (nxt.get("station") or {}).get("coordinate")),
                "planned": int(planned.timestamp()),
                "delay": delay if delay is not None else None,
                "platform": (stop.get("prognosis") or {}).get("platform") or stop.get("platform"),
                "cancelled": bool(stop.get("cancelled")),
            })
        if not out:
            raise ValueError("leere Abfahrtstafel")
        return out

    def fetch_iris(self, t):
        eva = t["iris_eva"]
        tz = local_tz()
        now = datetime.now(tz)
        stops = {}
        for h in range(3):
            slot = now + timedelta(hours=h)
            url = "https://iris.noncd.db.de/iris-tts/timetable/plan/%s/%s" % (eva, slot.strftime("%y%m%d/%H"))
            try:
                raw, _ = http_get(url)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    continue
                raise
            for s in ET.fromstring(raw).findall("s"):
                dp = s.find("dp")
                if dp is None:
                    continue
                path = (dp.get("ppth") or "").split("|")
                stops[s.get("id")] = {
                    "line": dp.get("l") or "", "to": path[-1] if path else "", "next": path[0] if path else "",
                    "pt": dp.get("pt"), "ct": None, "platform": dp.get("pp"), "cancelled": False,
                }
        raw, _ = http_get("https://iris.noncd.db.de/iris-tts/timetable/fchg/%s" % eva)
        for s in ET.fromstring(raw).findall("s"):
            dp = s.find("dp")
            if dp is None or s.get("id") not in stops:
                continue
            st = stops[s.get("id")]
            st["ct"] = dp.get("ct") or st["ct"]
            st["platform"] = dp.get("cp") or st["platform"]
            st["cancelled"] = dp.get("cs") == "c"

        def ts(v):
            return datetime.strptime(v, "%y%m%d%H%M").replace(tzinfo=tz)
        out = []
        for st in stops.values():
            if not st["pt"]:
                continue
            planned = ts(st["pt"])
            delay = int((ts(st["ct"]) - planned).total_seconds() // 60) if st["ct"] else None
            out.append({"line": st["line"], "to": st["to"], "next": st["next"], "bearing": None,
                        "planned": int(planned.timestamp()),
                        "delay": delay, "platform": st["platform"], "cancelled": st["cancelled"]})
        if not out:
            raise ValueError("IRIS: keine Abfahrten")
        return out


def bearing(a, b):
    """Kompassrichtung in Grad von Koordinate a nach b ({"x": lat, "y": lon}) oder None."""
    import math
    try:
        lat1, lon1, lat2, lon2 = (math.radians(float(v)) for v in (a["x"], a["y"], b["x"], b["y"]))
    except (TypeError, KeyError, ValueError):
        return None
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)


def display_name(to, rename):
    return rename.get(to) or re.sub(r"\s*\((D|CH|F|A|I)\)$", "", to)


def assign_directions(deps, groups, rename):
    """Setzt d["dir"] (Index) und liefert die Gruppentitel.
    "auto": Halte nach Kompassrichtung des nächsten Halts in zwei Hälften teilen (größte Lücke im Kreis)."""
    if isinstance(groups, list) and groups:
        rxs = [re.compile(g.get("match") or ".", re.I) for g in groups]
        for d in deps:
            d["dir"] = next((i for i, rx in enumerate(rxs) if rx.search(d["to"])), len(rxs) - 1)
        return [g.get("title") or "" for g in groups]
    if groups == "auto":
        angles = sorted(set(d["bearing"] for d in deps if d.get("bearing") is not None))
        side = None
        if len(angles) >= 2:
            # Die Achse wählen, die die Halte am saubersten in zwei gegenüberliegende Hälften teilt
            def spread(axis):
                cost = 0.0
                for a in angles:
                    diff = abs((a - axis + 180) % 360 - 180)
                    cost += min(diff, 180 - diff)
                return cost
            axis = min(range(0, 180, 5), key=spread)
            side = lambda a: 0 if abs((a - axis + 180) % 360 - 180) <= 90 else 1
        elif len(set(d.get("next") for d in deps if d.get("next"))) == 2:  # ohne Koordinaten nur eindeutige Fälle
            names = sorted(set(d["next"] for d in deps if d.get("next")))
            side = lambda n: 0 if n == names[0] else 1
        if side:
            for d in deps:
                key = d.get("bearing") if d.get("bearing") is not None and len(angles) >= 2 else d.get("next")
                d["dir"] = side(key) if key is not None else 1
            titles = []
            for i in (0, 1):
                ends = [display_name(d["to"], rename) for d in deps if d.get("dir") == i]
                top = sorted(set(ends), key=lambda e: (-ends.count(e), e))[:2]
                titles.append("Richtung " + " · ".join(top) if top else "")
            if all(titles):
                return titles
    for d in deps:
        d["dir"] = 0
    return [""]


# --------------------------------------------------------------------------
# News (RSS 2.0 / Atom)


def text_of(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or "").strip():
            return html.unescape(re.sub(r"\s+", " ", x.text.strip()))
    return ""


def parse_feed(raw):
    root = ET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for it in root.iter("item"):
        items.append({"title": text_of(it, "title"), "date": text_of(it, "pubDate")})
    if not items:
        for it in root.iter(atom + "entry"):
            items.append({"title": text_of(it, atom + "title"), "date": text_of(it, atom + "updated")})
    for it in items:
        try:
            it["ts"] = int(email.utils.parsedate_to_datetime(it.pop("date")).timestamp())
        except Exception:
            it["ts"] = None
    return [i for i in items if i["title"]]


class News(Source):
    name = "news"
    interval = 10 * 60

    def fetch(self):
        feeds, errors = [], []
        prev = {f["name"]: f for f in ((self.state.get("data") or {}).get("feeds") or [])}
        for f in self.cfg["news"]:
            try:
                raw, _ = http_get(f["url"])
                items = [i for i in parse_feed(raw)
                         if len(i["title"]) > 15 and i["title"].lower() != f["name"].lower()
                         and not re.search(r"\bLive(blog|stream|ticker)\b|\+\+|Podcast|in 100 Sekunden", i["title"], re.I)]
                items.sort(key=lambda i: -(i["ts"] or 0))
                items = items[:12]
                feeds.append({"name": f["name"], "lang": f["lang"], "items": items, "ok": True})
            except Exception as e:
                errors.append("%s: %s" % (f["name"], e))
                old = prev.get(f["name"])
                if old:
                    feeds.append(dict(old, ok=False))
        if not any(f["ok"] for f in feeds):
            raise ValueError("; ".join(errors) or "keine Feeds")
        return {"feeds": feeds}


# --------------------------------------------------------------------------
# Waste: iCal-Adresse oder Abfuhrtermine-Portal (athos WasteManagementServlet -> iCal)


class Waste(Source):
    name = "waste"
    interval = 12 * 3600
    retry = 10 * 60

    def fetch(self):
        w = self.cfg["waste"]
        if w.get("provider") == "ics" and w.get("ics_url"):
            raw, _ = http_get(w["ics_url"], timeout=60)
            ics = raw.decode("utf-8", "replace")
        elif w.get("provider") == "athos" and w.get("portal_url") and w.get("strasse"):
            ics = AthosPortal(w).ical()
        else:
            raise NotConfigured()
        if "BEGIN:VCALENDAR" not in ics:
            raise ValueError("kein iCal erhalten")
        types = [(t["kind"], t["label"], re.compile(t["match"], re.I)) for t in w["types"]]
        events = []
        for e in parse_ics(ics):
            for kind, label, rx in types:
                if rx.search(e["summary"]):
                    events.append({"date": e["date"], "kind": kind, "label": label})
                    break
        today = datetime.now().strftime("%Y-%m-%d")
        events = [e for e in events if e["date"] >= today]
        # Der neue Jahreskalender erscheint oft spät: bekannte Zukunftstermine behalten
        prev = (self.state.get("data") or {}).get("events") or []
        seen = set((e["date"], e["kind"]) for e in events)
        events += [e for e in prev if e["date"] >= today and (e["date"], e["kind"]) not in seen]
        events.sort(key=lambda e: e["date"])
        return {"events": events, "types": [{"kind": k, "label": l} for k, l, _ in types]}


class AthosPortal(object):
    """Klickt sich durch ein Abfuhrtermine-Formular (WasteManagementServlet) bis zum iCal-Export."""

    def __init__(self, w):
        import http.cookiejar
        self.w = w
        self.url = w["portal_url"].split("?")[0]
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @staticmethod
    def hidden(page):
        return {m.group(1): html.unescape(m.group(2) or "") for m in re.finditer(
            r'<INPUT NAME="([^"]+)" ID="[^"]*"(?: VALUE="([^"]*)")? TYPE="HIDDEN"', page, re.I)}

    def post(self, fields):
        b = uuid.uuid4().hex
        body = b"".join(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode("utf-8")
                        for k, v in fields.items()) + ("--%s--\r\n" % b).encode()
        raw, _ = http_get(self.url, data=body, timeout=90, opener=self.op,
                          headers={"Content-Type": "multipart/form-data; boundary=" + b})
        return raw.decode("utf-8", "replace")

    # Einzelschritte (auch für setup.py: Orte/Straßen/Tonnen zur Auswahl anzeigen)
    def open(self):
        raw, _ = http_get(self.url + "?SubmitAction=wasteDisposalServices&InFrameMode=TRUE", opener=self.op, timeout=60)
        self.page = raw.decode("utf-8", "replace")
        return self

    def options(self, name):
        part = self.page.split('NAME="%s"' % name)
        if len(part) < 2:
            return []
        vals = re.findall(r'<OPTION VALUE="([^"]*)"', part[1].split("</SELECT>")[0])
        return [html.unescape(v) for v in vals if v]  # \xa0 bleibt: das Portal erwartet den Wert genau so

    def containers(self):
        return [(int(n), re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", label))).strip())
                for n, label in re.findall(r'FOR="ContainerGewaehlt_(\d+)">(.*?)</LABEL>', self.page, re.S | re.I)]

    def choose_city(self, ort):
        f = self.hidden(self.page)
        f.update(SubmitAction="CITYCHANGED", Ort=ort, Strasse="")
        self.page = self.post(f)
        return self

    def ical(self):
        w = self.w
        self.open().choose_city(w["ort"])
        page = self.page
        # "2a" / "2 a" -> Hausnummer 2, Zusatz a
        m = re.match(r"\s*(\d+)\s*(.*?)\s*$", str(w["hausnummer"]))
        hn, zusatz = (m.group(1), m.group(2)) if m else (str(w["hausnummer"]).strip(), "")
        for _ in range(2):
            f = self.hidden(page)
            f.update(SubmitAction="forward", Ort=w["ort"], Strasse=w["strasse"], Hausnummer=hn,
                     Hausnummerzusatz=zusatz, BedCheckerDatenschutz="on")
            for c in w["containers"]:
                f["ContainerGewaehlt_%d" % int(c)] = "on"
            page = self.post(f)
            if self.hidden(page).get("PageName") == "Terminliste":
                break
            # Hausnummer unbekannt: das Portal bietet eine Liste an -> erste nehmen
            opts = re.findall(r'<OPTION VALUE="([^"]*)"', page.split('NAME="Hausnummernwahl"')[-1].split("</SELECT>")[0])
            if 'NAME="Hausnummernwahl"' not in page or not opts:
                raise ValueError("Adresse vom Portal nicht akzeptiert")
            hn, zusatz = html.unescape(opts[0]).replace("\xa0", " "), ""
            log("[waste] Hausnummer %s unbekannt, Portal schlägt %s vor" % (w["hausnummer"], hn))
        else:
            raise ValueError("Terminliste nicht erreicht")
        f = self.hidden(page)
        f["SubmitAction"] = "filedownload_ICAL"
        return self.post(f)


def find_waste_source(text):
    """Aus einer getippten Adresse (Domain der Abfallwirtschaft, Portal- oder iCal-Link) die Quelle finden.
    Liefert ("ics", url), ("athos", servlet_url) oder (None, None)."""
    url = text.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[9:]
    if not re.match(r"https?://", url):
        url = "https://" + url
    if re.search(r"\.ics(\?|$)", url, re.I):
        return "ics", url
    m = re.search(r"(https?://[^?#]*?/WasteManagement\w*)(/WasteManagementServlet)?", url)
    if m:
        return "athos", m.group(1) + "/WasteManagementServlet"

    def scan(page_url, depth):
        try:
            req = urllib.request.Request(page_url, headers={"User-Agent": UA_BROWSER})
            resp = urllib.request.urlopen(req, timeout=20)
            final, body = resp.geturl(), resp.read(2000000).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            final, body = e.geturl(), ""
        except Exception:
            return None
        m = re.search(r"(https?://[^?#\s\"']*?/WasteManagement\w*)(/|$)", final)
        if m:  # Domain leitet direkt aufs Portal um
            return "athos", m.group(1) + "/WasteManagementServlet"
        links = [html.unescape(l).strip() for l in re.findall(r'(?:href|src)\s*=\s*"([^"]+)"', body, re.I)]
        for l in links:
            if "WasteManagementServlet" in l or re.search(r"/WasteManagement\w+", l):
                mm = re.search(r"(https?://[^?#]*?/WasteManagement\w*)", urllib.parse.urljoin(final, l))
                if mm:
                    return "athos", mm.group(1) + "/WasteManagementServlet"
            if re.search(r"\.ics(\?|$)|^webcal:", l, re.I):
                return "ics", urllib.parse.urljoin(final, l).replace("webcal://", "https://")
        if depth > 0:
            host = urllib.parse.urlparse(final).netloc
            subs = [urllib.parse.urljoin(final, l) for l in links
                    if re.search(r"kalender|abfuhr|termin", l, re.I) and urllib.parse.urlparse(urllib.parse.urljoin(final, l)).netloc == host]
            for sub in list(dict.fromkeys(subs))[:6]:
                hit = scan(sub, depth - 1)
                if hit:
                    return hit
        return None

    return scan(url, 1) or (None, None)


def parse_ics(text):
    text = re.sub(r"\r?\n[ \t]", "", text)
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        m = re.search(r"^DTSTART[^:]*:(\d{8})", block, re.M)
        s = re.search(r"^SUMMARY[^:]*:(.*)$", block, re.M)
        if m and s:
            d = m.group(1)
            out.append({"date": "%s-%s-%s" % (d[:4], d[4:6], d[6:]), "summary": s.group(1).strip().replace("\\,", ",")})
    return out


# --------------------------------------------------------------------------
# Reddit: cat/meme images. JSON if allowed, otherwise Atom feed.


class Reddit(Source):
    """One request per `request_every_s`, round robin across the pool, per-feed cache.

    Ranking ("velocity"):
      JSON:  score / (age_h + 1.5)^1.4, normalised by the subreddit's median so that
             small and big subs compete fairly, times the pool weight.
      Atom:  no score available -> position in /hot is reddit's own score/age blend,
             so use 1/(rank+3) / (age_h + 2)^0.6 * weight.
    Max 2 posts per subreddit, SFW checks: over_18, spoiler, stickied, flair and title
    blocklist (JSON); title blocklist + blurred previews (Atom).
    """
    name = "reddit"
    interval = 10 ** 9  # driven by loop() below

    def __init__(self, cfg):
        Source.__init__(self, cfg)
        self.feeds = {}  # sub -> {"posts": [...], "at": ts}
        for sub, f in ((self.state.get("data") or {}).get("_feeds") or {}).items():
            self.feeds[sub] = f
        self.json_blocked_until = 0
        self.pause_until = 0
        self.token = (None, 0)

    def subs(self):
        r = self.cfg["reddit"]
        if self.cfg["images"] != "reddit":
            return []
        return [(s, w, "image") for s, w in r["image_subs"].items() if w > 0]

    def loop(self):
        time.sleep(5)
        every = 20 if self.oauth() else max(30, int(self.cfg["reddit"]["request_every_s"]))
        while True:
            if night_paused(self.cfg) and self.state.get("updated"):
                self.state["paused"] = True
                time.sleep(60)
                continue
            self.state["paused"] = False
            pool = self.subs()
            # stalest feed first
            pool.sort(key=lambda p: (self.feeds.get(p[0]) or {}).get("at", 0))
            if time.time() >= self.pause_until and pool:
                sub, _, kind = pool[0]
                try:
                    posts = self.fetch_sub(sub)
                    self.feeds[sub] = {"posts": posts, "at": time.time(), "kind": kind}
                except urllib.error.HTTPError as e:
                    log("[reddit] r/%s HTTP %s" % (sub, e.code))
                    if e.code == 429:  # anonym erlaubt Reddit ~1 Abruf/Minute
                        try:
                            wait = float(e.headers.get("x-ratelimit-reset") or 60)
                        except ValueError:
                            wait = 60
                        self.pause_until = time.time() + max(90, wait + 10)
                    self.feeds.setdefault(sub, {"posts": [], "kind": kind})["at"] = time.time()
                except Exception as e:
                    log("[reddit] r/%s Fehler: %s" % (sub, e))
                    self.feeds.setdefault(sub, {"posts": [], "kind": kind})["at"] = time.time()
            self.run_once()  # auch in der Zwangspause, damit Ersatz-Katzen nachrücken
            time.sleep(every)

    def oauth(self):
        r = self.cfg["reddit"]
        return bool(r.get("client_id") and r.get("client_secret"))

    def bearer(self):
        tok, exp = self.token
        if tok and time.time() < exp - 60:
            return tok
        import base64
        r = self.cfg["reddit"]
        cred = base64.b64encode(("%s:%s" % (r["client_id"], r["client_secret"])).encode()).decode()
        raw, _ = http_get("https://www.reddit.com/api/v1/access_token", data=b"grant_type=client_credentials",
                          headers={"Authorization": "Basic " + cred, "Content-Type": "application/x-www-form-urlencoded"})
        d = json.loads(raw.decode("utf-8"))
        if "access_token" not in d:
            raise ValueError("Reddit-Login abgelehnt: %s" % d)
        self.token = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
        return self.token[0]

    def fetch_sub(self, sub):
        if self.oauth():
            try:
                return self.fetch_json(sub, oauth=True)
            except urllib.error.HTTPError as e:
                if e.code not in (400, 401, 403):
                    raise
                log("[reddit] Zugangsdaten abgelehnt (HTTP %s), nutze öffentlichen Weg" % e.code)
        if time.time() > self.json_blocked_until:
            try:
                return self.fetch_json(sub)
            except urllib.error.HTTPError as e:
                log("[reddit] JSON gesperrt (HTTP %s), nutze RSS fuer 6h" % e.code)
                self.json_blocked_until = time.time() + 6 * 3600
            except ValueError:
                self.json_blocked_until = time.time() + 6 * 3600
        return self.fetch_atom(sub)

    def fetch_json(self, sub, oauth=False):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        if oauth:
            raw, _ = http_get("https://oauth.reddit.com/r/%s/hot?limit=30&raw_json=1" % sub, ua=UA_SELF, opener=op,
                              headers={"Authorization": "bearer " + self.bearer()})
        else:
            raw, _ = http_get("https://www.reddit.com/r/%s/hot.json?limit=30&raw_json=1" % sub, ua=UA_SELF, opener=op)
        children = json.loads(raw.decode("utf-8"))["data"]["children"]
        out = []
        for rank, c in enumerate(children):
            p = c["data"]
            if p.get("over_18") or p.get("spoiler") or p.get("stickied") or p.get("pinned"):
                continue
            flair = (p.get("link_flair_text") or "") + " " + (p.get("title") or "")
            if BLOCK_WORDS.search(flair):
                continue
            img = None
            url = p.get("url_overridden_by_dest") or p.get("url") or ""
            if re.search(r"\.(jpe?g|png|webp)$", url.split("?")[0], re.I) and "i.redd.it" in url:
                img = url
            out.append({"id": p["id"], "title": p["title"], "sub": p["subreddit"], "rank": rank,
                        "score": p.get("score", 0), "comments": p.get("num_comments", 0),
                        "created": p.get("created_utc"), "img": img, "via": "json"})
        return out

    def fetch_atom(self, sub):
        raw, _ = http_get("https://www.reddit.com/r/%s/hot/.rss?limit=30" % sub, ua=UA_BROWSER)
        atom = "{http://www.w3.org/2005/Atom}"
        root = ET.fromstring(raw)
        out = []
        for rank, e in enumerate(root.findall(atom + "entry")):
            title = text_of(e, atom + "title")
            content = html.unescape(text_of(e, atom + "content"))
            pid = text_of(e, atom + "id")
            if not title or BLOCK_WORDS.search(title) or "blur=" in content:
                continue
            if re.search(r"/r/[^/]+/comments/[^/]+/(tips_on|rules|weekly|daily|megathread)", content, re.I):
                continue
            img = None
            m = re.search(r'href="(https://i\.redd\.it/[^"]+\.(?:jpe?g|png|webp))"', content, re.I)
            if m:
                img = m.group(1)
            cat = e.find(atom + "category")
            published = text_of(e, atom + "published") or text_of(e, atom + "updated")
            try:
                created = parse_iso(published).timestamp()
            except ValueError:
                created = None
            out.append({"id": pid, "title": title, "sub": cat.get("term") if cat is not None else sub,
                        "rank": rank, "score": None, "comments": None, "created": created, "img": img, "via": "rss"})
        return out

    def fetch(self):
        r = self.cfg["reddit"]
        weights = {k.lower(): v for k, v in r["image_subs"].items()}
        now = time.time()
        images = []
        for sub, feed in self.feeds.items():
            posts = [p for p in feed.get("posts") or [] if p.get("created") and now - p["created"] < 3 * 86400]
            w = weights.get(sub.lower(), 0)
            if not posts or w <= 0:
                continue
            vel = []
            for p in posts:
                age = max(0.0, (now - p["created"]) / 3600.0)
                if p["score"] is not None:
                    v = p["score"] / (age + 1.5) ** 1.4
                else:
                    v = (1.0 / (p["rank"] + 3)) / (age + 2) ** 0.6
                vel.append((v, p))
            med = sorted(v for v, _ in vel)[len(vel) // 2] or 1e-9
            for v, p in vel:
                images.append(dict(p, heat=round(w * v / med, 3)))
        # Nur Kandidaten merken; geladen wird erst, wenn ein Bild wirklich gezeigt wird (ImagePicker)
        cands = pick_diverse([i for i in images if i.get("img")], 80, 10) if self.cfg["images"] == "reddit" else []
        mode = "oauth" if self.oauth() else "json" if now > self.json_blocked_until else "rss"
        return {"count": len(cands), "_candidates": cands, "_feeds": self.feeds, "mode": mode}


class ImagePicker(object):
    """Wählt das nächste Bild für die Katzen-Kachel (alle 5 min).
    Mischung: jedes dritte Bild von TheCatAPI/cataas, sonst Reddit (Memes und normale Katzen);
    fällt Reddit aus, kommen nur noch die Katzen-Dienste. Kein Bild wiederholt sich innerhalb von 3 Tagen."""
    TTL = 3 * 86400

    def __init__(self):
        self.lock = threading.Lock()
        self.path = os.path.join(CACHE_DIR, "seen-images.json")
        self.queue = []   # (key, url, source) von den Katzen-Diensten
        self.count = 0
        try:
            with open(self.path) as f:
                self.seen = json.load(f)
        except (OSError, ValueError):
            self.seen = {}

    def mark(self, key):
        now = time.time()
        self.seen[key] = now
        self.seen = {k: t for k, t in self.seen.items() if now - t < self.TTL}
        try:
            with open(self.path + ".tmp", "w") as f:
                json.dump(self.seen, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass

    def refill(self):
        try:
            raw, _ = http_get("https://api.thecatapi.com/v1/images/search?limit=10&mime_types=jpg,png")
            self.queue += [("tca:" + c["id"], c["url"], "TheCatAPI") for c in json.loads(raw.decode("utf-8")) if c.get("url")]
        except Exception as e:
            log("[cats] TheCatAPI: %s" % e)
        try:
            raw, _ = http_get("https://cataas.com/api/cats?limit=10&skip=%d" % random.randint(0, 2000))
            self.queue += [("cs:" + c["id"], "https://cataas.com/cat/%s" % c["id"], "cataas.com")
                           for c in json.loads(raw.decode("utf-8"))
                           if c.get("mimetype") in ("image/jpeg", "image/png") and not BLOCK_WORDS.search(" ".join(c.get("tags") or []))]
        except Exception as e:
            log("[cats] cataas: %s" % e)
        random.shuffle(self.queue)

    def from_service(self):
        for _ in range(12):
            if not self.queue:
                self.refill()
                if not self.queue:
                    return None
            key, url, source = self.queue.pop()
            if key in self.seen:
                continue
            local = fetch_image(url)
            self.mark(key)
            if local:
                return {"key": key, "title": "", "sub": "", "source": source, "local": local}
        return None

    def from_reddit(self, cands):
        fresh = [c for c in cands if "r:" + c["id"] not in self.seen]
        for _ in range(5):
            if not fresh:
                return None
            c = random.choice(fresh[:10])  # unter den heißesten ungesehenen zufällig – mehr Abwechslung
            fresh.remove(c)
            self.mark("r:" + c["id"])
            local = fetch_image(c["img"])
            if local:
                return {"key": "r:" + c["id"], "title": c["title"], "sub": c["sub"], "local": local}
        return None

    def next(self, cands, cats_only=False):
        with self.lock:
            self.count += 1
            order = [self.from_service] if cats_only else (
                [self.from_service, lambda: self.from_reddit(cands)] if self.count % 3 == 0
                else [lambda: self.from_reddit(cands), self.from_service])
            for get in order:
                item = get()
                if item:
                    prune_images(keep=80)
                    return item
            return None


def pick_diverse(items, n, per_sub):
    items = sorted(items, key=lambda i: -i["heat"])
    out, count, seen = [], {}, set()
    for i in items:
        key = i["title"].lower()[:60]
        if count.get(i["sub"].lower(), 0) >= per_sub or key in seen:
            continue
        count[i["sub"].lower()] = count.get(i["sub"].lower(), 0) + 1
        seen.add(key)
        out.append(i)
        if len(out) >= n:
            break
    return out


def fetch_image(url):
    name = hashlib.sha1(url.encode()).hexdigest()[:20]
    for ext in (".jpg", ".png", ".webp"):
        if os.path.exists(os.path.join(IMG_DIR, name + ext)):
            os.utime(os.path.join(IMG_DIR, name + ext))
            return name + ext
    try:
        raw, headers = http_get(url, ua=UA_BROWSER, timeout=30)
    except Exception as e:
        log("[img] %s: %s" % (url[:80], e))
        return None
    ctype = headers.get("Content-Type", "")
    ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg" if "jpeg" in ctype or "jpg" in ctype else None
    if not ext or len(raw) > 12 * 1024 * 1024 or len(raw) < 2000:
        return None
    path = os.path.join(IMG_DIR, name + ext)
    with open(path + ".tmp", "wb") as f:
        f.write(raw)
    os.replace(path + ".tmp", path)
    return name + ext


def prune_images(keep):
    try:
        files = sorted((os.path.join(IMG_DIR, f) for f in os.listdir(IMG_DIR)), key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            os.remove(f)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Bildschirm: Abdunkeln des Systems verhindern, nachts per DPMS aus (läuft im Server,
# damit Änderungen mit dem Auto-Update sofort greifen)


class Display(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.status = {"active": False, "last": None, "error": None}

    def run(self, *cmd):
        import subprocess
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            return p.returncode, p.stdout.decode("utf-8", "replace")
        except Exception as e:  # Programm fehlt o. Ä.
            return 127, str(e)

    def setup(self):
        # XFCE-Energieverwaltung: nie abdunkeln, nie ausschalten (das übernimmt die Nacht-Steuerung)
        for prop, typ, val in (("dpms-enabled", "bool", "false"), ("blank-on-ac", "int", "0"),
                               ("dpms-on-ac-sleep", "int", "0"), ("dpms-on-ac-off", "int", "0"),
                               ("brightness-on-ac", "uint", "9"), ("brightness-inactivity-on-ac", "int", "9")):
            self.run("xfconf-query", "-c", "xfce4-power-manager", "-p", "/xfce4-power-manager/" + prop,
                     "-n", "-t", typ, "-s", val)
        self.run("xset", "s", "off")
        self.run("xset", "s", "noblank")

    def loop(self):
        if not os.environ.get("DISPLAY"):
            self.status["error"] = "kein DISPLAY (Server läuft nicht in der grafischen Sitzung)"
            return
        self.status["active"] = True
        self.setup()
        last = None
        while True:
            n = self.cfg["night"]
            off = n.get("mode") == "off" and is_night(n)
            if off != last:
                if off:
                    cmds = (("xset", "+dpms"), ("xset", "dpms", "300", "300", "300"), ("xset", "dpms", "force", "off"))
                else:
                    cmds = (("xset", "dpms", "force", "on"), ("xset", "-dpms"), ("xset", "s", "reset"))
                errs = [out.strip() for rc, out in (self.run(*c) for c in cmds) if rc != 0]
                self.status.update(last="aus" if off else "an", at=time.time(), error="; ".join(errs)[:200] or None)
                log("[display] Bildschirm %s%s" % ("aus" if off else "an", (" – " + errs[0]) if errs else ""))
                last = off
            time.sleep(30)


# --------------------------------------------------------------------------
# HTTP server

SOURCES = []
CONFIG = {}
PICKER = None
DISPLAY_CTL = Display({})
VERSION = read_version()
STARTED = time.time()
PAGE = {"version": None, "at": 0}  # Lebenszeichen der Seite im Browser (für update.sh)
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "FlurDashboard"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/api/leave":
            return self.leave()
        self.send(404, b"not found", "text/plain")

    def leave(self):
        """'Zum Desktop': Browser beenden und erst nach 'Dashboard starten' oder dem nächsten Login wieder öffnen."""
        import subprocess
        with open(os.path.join(HOME_DIR, ".stopped"), "w") as f:
            f.write("%d\n" % time.time())
        self.send(200, b"ok", "text/plain")
        threading.Timer(0.5, lambda: subprocess.call(["pkill", "-f", os.path.join(HOME_DIR, "browser-profile")])).start()
        log("Zum Desktop gewechselt")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/all":
            out = {"version": VERSION, "server_time": time.time(), "started": STARTED,
                   "night": CONFIG["night"], "night_now": is_night(CONFIG["night"]), "display": CONFIG["display"],
                   "page": PAGE, "screen": DISPLAY_CTL.status, "kiosk": CONFIG["kiosk"],
                   "update": update_status(), "sources": {}}
            for s in SOURCES:
                snap = s.snapshot()
                if isinstance(snap.get("data"), dict):
                    snap["data"] = {k: v for k, v in snap["data"].items() if not k.startswith("_")}
                out["sources"][s.name] = snap
            return self.send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        if path == "/healthz":
            return self.send(200, b"ok", "text/plain")
        if path == "/api/next-image":
            reddit = next((s for s in SOURCES if isinstance(s, Reddit)), None)
            cands = ((reddit.snapshot().get("data") or {}).get("_candidates") or []) if reddit else []
            item = PICKER.next(cands, cats_only=CONFIG["images"] != "reddit")
            if not item:
                return self.send(503, b"keine Bilder", "text/plain")
            return self.send(200, json.dumps(item, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        if path == "/api/alive":
            v = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("v", [""])[0]
            PAGE.update(version=v[:40], at=time.time())
            return self.send(204, b"", "text/plain")
        if path.startswith("/img/"):
            name = os.path.basename(path)
            fp = os.path.join(IMG_DIR, name)
            if re.match(r"^[0-9a-f]{20}\.(jpg|png|webp)$", name) and os.path.exists(fp):
                with open(fp, "rb") as f:
                    return self.send(200, f.read(), TYPES[os.path.splitext(name)[1]], "max-age=86400")
            return self.send(404, b"not found", "text/plain")
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if fp.startswith(WEB_DIR + os.sep) and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return self.send(200, f.read(), TYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))
        self.send(404, b"not found", "text/plain")


class ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def write_if_changed(path, body, mode=0o755):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == body:
                return False
    except OSError:
        pass
    try:
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, mode)
        return True
    except OSError as e:
        log("Datei %s nicht angelegt: %s" % (path, e))
        return False


def ensure_shortcuts():
    """Kurzbefehle und Menü-/Desktop-Einträge anlegen – auch für ältere Installationen nach dem Auto-Update.
    ~/imac-dashboard/{setup,diagnose,update,start}.sh; Menü: Dashboard starten/aktualisieren/Einstellungen/Diagnose."""
    app = os.path.join(HOME_DIR, "app")
    for name, target in (("setup.sh", "setup.sh"), ("diagnose.sh", "diagnose.sh"),
                         ("update.sh", "update-now.sh"), ("start.sh", "start.sh")):
        write_if_changed(os.path.join(HOME_DIR, name), '#!/bin/sh\nexec "%s" "$@"\n' % os.path.join(app, target))
    entries = {
        "start": ("Dashboard starten", "Flur-Dashboard wieder anzeigen", "video-display", "start.sh", "", "false"),
        "update": ("Dashboard aktualisieren", "Nach einer neuen Version suchen", "system-software-update", "update.sh", " --pause", "true"),
        "setup": ("Dashboard-Einstellungen", "Ort, Abfahrten, Müll, Darstellung ändern", "preferences-system", "setup.sh", "", "true"),
        "diagnose": ("Dashboard-Diagnose", "Fehlerbericht zum Weiterschicken", "utilities-system-monitor", "diagnose.sh", " --pause", "true"),
    }
    if not sys.platform.startswith("linux"):
        return  # Menü-/Desktop-Einträge nur auf dem Kiosk-Rechner, nicht beim Testen auf anderen Systemen
    apps_dir = os.path.expanduser("~/.local/share/applications")
    desktop_files = {}
    for key, (name, comment, icon, script, args, term) in entries.items():
        body = ("[Desktop Entry]\nType=Application\nName=%s\nComment=%s\nIcon=%s\nExec=\"%s\"%s\nTerminal=%s\n"
                "Categories=Utility;\n" % (name, comment, icon, os.path.join(HOME_DIR, script), args, term))
        path = os.path.join(apps_dir, "imac-dashboard-%s.desktop" % key)
        write_if_changed(path, body)
        desktop_files[key] = body
    # Auf den Schreibtisch nur einmal legen (wer sie löscht, bekommt sie nicht ständig zurück)
    marker = os.path.join(HOME_DIR, ".desktop-icons")
    if os.environ.get("DISPLAY") and not os.path.exists(marker):
        rc, out = Display({}).run("xdg-user-dir", "DESKTOP")
        desk = out.strip() if rc == 0 else os.path.expanduser("~/Desktop")
        if desk and os.path.isdir(desk) and os.path.realpath(desk) != os.path.realpath(os.path.expanduser("~")):
            for key in ("start", "update"):
                path = os.path.join(desk, "Dashboard-%s.desktop" % ("starten" if key == "start" else "aktualisieren"))
                if write_if_changed(path, desktop_files[key]):
                    # XFCE fragt sonst bei jedem Start "nicht vertrauenswürdiger Starter"
                    digest = hashlib.sha256(desktop_files[key].encode("utf-8")).hexdigest()
                    Display({}).run("gio", "set", "-t", "string", path, "metadata::xfce-exe-checksum", digest)
            write_if_changed(marker, "1\n", 0o644)


def maybe_restart_kiosk():
    """Läuft noch ein kiosk.sh aus einer älteren Version, durch das neue ersetzen (kein Neuanmelden nötig).
    Der Zustand "Zum Desktop" bleibt dabei erhalten (--keep-state)."""
    import subprocess
    kiosk = os.path.join(HOME_DIR, "app", "kiosk.sh")
    if not os.environ.get("DISPLAY") or not os.path.exists(kiosk):
        return
    if subprocess.call(["pgrep", "-f", kiosk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        return  # Kiosk läuft gar nicht (z. B. von Hand gestarteter Server)
    with open(kiosk, "rb") as f:
        want = hashlib.sha1(f.read()).hexdigest()
    try:
        with open(os.path.join(HOME_DIR, ".kiosk-hash")) as f:
            running = f.read().strip()
    except OSError:
        running = ""
    attempt = os.path.join(HOME_DIR, ".kiosk-restart-attempt")
    try:
        with open(attempt) as f:
            if f.read().strip() == want:
                return  # schon versucht, keine Schleife
    except OSError:
        pass
    if running == want:
        return
    write_if_changed(attempt, want + "\n", 0o644)
    log("kiosk.sh hat sich geändert – starte es neu")
    subprocess.Popen([kiosk, "--keep-state"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def diagnose():
    """Kurzbericht zum Weiterschicken: Status, letzte Fehler, Verbindungen. Keine Zugangsdaten."""
    import platform
    import socket
    import ssl
    out = []
    p = out.append
    osname = ""
    try:
        with open("/etc/os-release") as f:
            osname = dict(l.strip().split("=", 1) for l in f if "=" in l).get("PRETTY_NAME", "").strip('"')
    except OSError:
        osname = platform.platform()
    p("Flur-Dashboard Diagnose  %s" % time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    p("Version %s · Python %s · %s" % (VERSION, platform.python_version(), osname))
    p("Konfiguration: %s (%s)" % (CONFIG_PATH, "vorhanden" if os.path.exists(CONFIG_PATH) else "FEHLT"))
    w, t = CONFIG["waste"], CONFIG["trains"]
    p("  Wetter: %s" % ("eingerichtet" if CONFIG["location"].get("lat") is not None else "nicht eingerichtet"))
    p("  Abfahrten: %s · Linien %s · Richtungen %s" % (t.get("station_name") or "nicht eingerichtet",
                                                     " ".join(t.get("lines") or []) or "alle",
                                                     t["groups"] if isinstance(t["groups"], str) else "eigene"))
    host = urllib.parse.urlparse(w.get("portal_url") or w.get("ics_url") or "").netloc
    p("  Müll: %s %s %s %s · Tonnen %s" % (w.get("provider") or "nicht eingerichtet", host, w.get("strasse", ""),
                                          w.get("hausnummer", ""), w.get("containers")))
    p("  Nacht: %s–%s Modus %s · Reddit-Zugang: %s" % (CONFIG["night"]["from"], CONFIG["night"]["to"], CONFIG["night"]["mode"],
                                                      "gesetzt" if CONFIG["reddit"].get("client_id") else "nein"))
    p("")
    p("Laufender Server:")
    try:
        snap = json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/all" % int(CONFIG["port"]), timeout=5))
        for name, st in sorted(snap["sources"].items()):
            age = "%d min alt" % ((time.time() - st["updated"]) / 60) if st.get("updated") else "noch nie geladen"
            state = ("nicht eingerichtet" if st.get("configured") is False else "Nachtpause" if st.get("paused")
                     else "ok" if st.get("ok") else "lädt noch" if not st.get("tried") else "FEHLER")
            p("  %-8s %-18s %-16s %s" % (name, state, age, st.get("hint") or st.get("error") or ""))
        pg = snap.get("page") or {}
        p("  Seite im Browser: %s" % ("meldet sich (vor %ds)" % (time.time() - pg["at"]) if pg.get("at") else "keine Meldung"))
        p("  Bildschirm-Steuerung: %s" % json.dumps(snap.get("screen"), ensure_ascii=False))
    except Exception as e:
        p("  nicht erreichbar: %s" % e)
    p("")
    p("Verbindungen:")
    hosts = ["api.open-meteo.com", "transport.opendata.ch", "iris.noncd.db.de", "www.tagesschau.de", "www.reddit.com"]
    if host:
        hosts.append(host)
    for h in hosts:
        t0 = time.time()
        try:
            socket.getaddrinfo(h, 443)
            ctx = ssl.create_default_context()
            with socket.create_connection((h, 443), timeout=10) as sock:
                with ctx.wrap_socket(sock, server_hostname=h):
                    pass
            p("  %-40s ok (%.1f s)" % (h, time.time() - t0))
        except Exception as e:
            p("  %-40s FEHLER %s – %s" % (h, friendly_error(e), e))
    p("")
    p("Müllkalender jetzt abrufen:")
    if w.get("provider"):
        ws = Waste(CONFIG)
        try:
            data = ws.fetch()
            nxt = ", ".join("%s %s" % (e["date"], e["label"]) for e in data["events"][:4])
            p("  ok – %d Termine. Nächste: %s" % (len(data["events"]), nxt or "keine"))
        except Exception as e:
            p("  FEHLER %s" % friendly_error(e))
            p("  " + "".join(traceback.format_exception_only(type(e), e)).strip())
    else:
        p("  nicht eingerichtet")
    p("")
    log_dir = os.path.join(HOME_DIR, "logs")
    for name, n in (("server.log", 12), ("update.log", 4)):
        p("Letzte Meldungen %s:" % name)
        try:
            with open(os.path.join(log_dir, name), encoding="utf-8", errors="replace") as f:
                lines = [l.rstrip() for l in f if re.search(r"Fehler|Error|Traceback|Update|zurück|HTTP", l)]
            for l in lines[-n:] or ["  (keine)"]:
                p("  " + l[:160])
        except OSError:
            p("  (keine Logdatei)")
    if os.environ.get("DISPLAY"):
        p("")
        rc, q = Display({}).run("xset", "q")
        p("Bildschirm (xset q): " + " | ".join(l.strip() for l in q.splitlines() if re.search(r"DPMS|Monitor|Standby", l)))
    print("\n".join(out))


def main():
    global CONFIG
    for d in (CACHE_DIR, IMG_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    CONFIG = load_config()
    global PICKER
    PICKER = ImagePicker()
    for cls in (Weather, Trains, News, Waste, Reddit):
        SOURCES.append(cls(CONFIG))
    if "--diagnose" in sys.argv or "--once" in sys.argv:
        diagnose()
        return
    ensure_shortcuts()
    try:
        maybe_restart_kiosk()
    except Exception as e:
        log("kiosk-Neustart nicht möglich:", e)
    for s in SOURCES:
        threading.Thread(target=s.loop, name=s.name, daemon=True).start()
    DISPLAY_CTL.cfg = CONFIG
    threading.Thread(target=DISPLAY_CTL.loop, name="display", daemon=True).start()
    port = int(os.environ.get("DASH_PORT", CONFIG["port"]))
    httpd = ThreadingServer(("127.0.0.1", port), Handler)
    log("Flur-Dashboard %s auf http://127.0.0.1:%d" % (VERSION, port))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
