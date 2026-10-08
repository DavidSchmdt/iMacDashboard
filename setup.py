#!/usr/bin/env python3
"""Legt die lokale Konfiguration an (~/.config/imac-dashboard/config.json).

Ortsangaben gehören nur in diese Datei, nie ins Repo. Zwei Wege:
  * Setup-Code einfügen (privat erzeugt mit:  python3 setup.py --make-code meine-config.json)
  * Fragen beantworten: Ort (Vorschlag per Internetadresse), nächster Bahnhof aus einer Liste,
    Müllkalender über die Website der Abfallwirtschaft (Gemeinde, Straße, Hausnummer zur Auswahl)

  python3 setup.py                 nur wenn noch keine Konfiguration existiert
  python3 setup.py --reconfigure   neu einrichten
  python3 setup.py --code CODE     Setup-Code direkt übergeben
"""
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib

CONFIG_PATH = os.environ.get("DASH_CONFIG", os.path.expanduser("~/.config/imac-dashboard/config.json"))
PREFIX = "IMD1."
UA = {"User-Agent": "imac-flur-dashboard-setup/1.0"}


def make_code(cfg):
    raw = zlib.compress(json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9)
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def read_code(code):
    code = "".join(code.split())
    if not code.startswith(PREFIX):
        raise ValueError("kein gültiger Setup-Code (muss mit %s beginnen)" % PREFIX)
    body = code[len(PREFIX):]
    cfg = json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).decode("utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("Setup-Code enthält keine Einstellungen")
    return cfg


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Tty(object):
    """Fragen über /dev/tty, damit es auch bei 'curl ... | bash' funktioniert."""

    def __init__(self):
        self.inp = open("/dev/tty", "r")
        self.out = open("/dev/tty", "w")

    def ask(self, q, default=""):
        self.out.write(q + (" [%s]" % default if default else "") + ": ")
        self.out.flush()
        a = self.inp.readline().strip()
        return a or default

    def say(self, s):
        self.out.write(s + "\n")
        self.out.flush()

    def choose(self, items, label, zero="überspringen"):
        for i, it in enumerate(items, 1):
            self.say("  %d) %s" % (i, label(it)))
        while True:
            a = self.ask("Nummer (Enter = 1, 0 = %s)" % zero, "1")
            if a.isdigit() and 0 <= int(a) <= len(items):
                return items[int(a) - 1] if int(a) else None


def pick_filtered(t, items, prompt, show=lambda x: x, default=""):
    """Auswahl aus einer langen Liste: Anfang tippen, dann Nummer wählen."""
    while True:
        q = t.ask(prompt, default).lower()
        hits = [i for i in items if show(i).lower().startswith(q)] or [i for i in items if q in show(i).lower()]
        if not hits:
            t.say("  Nichts gefunden.")
            continue
        if len(hits) == 1:
            t.say("  -> " + show(hits[0]))
            return hits[0]
        hit = t.choose(hits[:12], show)
        if hit:
            return hit


def ask_location(t):
    guess = None
    try:  # grobe Position über die Internetadresse (kein GPS) – nur als Vorschlag
        g = get_json("https://ipwho.is/?fields=success,city,latitude,longitude")
        if g.get("success") and g.get("city"):
            guess = g
    except Exception:
        pass
    while True:
        q = t.ask("Ort fürs Wetter (leer = keine Wetter-Kachel)" + ("; Enter = %s (per Internet erkannt)" % guess["city"] if guess else ""),
                  guess["city"] if guess else "")
        if not q:
            return None
        try:
            res = get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": q, "count": 6, "language": "de"})).get("results") or []
        except Exception as e:
            t.say("  Suche fehlgeschlagen: %s" % e)
            continue
        if guess and q == guess["city"]:  # Treffer nahe der erkannten Position nach vorn
            res.sort(key=lambda r: (r["latitude"] - guess["latitude"]) ** 2 + (r["longitude"] - guess["longitude"]) ** 2)
        if not res:
            t.say("  Nichts gefunden, bitte anders schreiben.")
            continue
        hit = t.choose(res, lambda r: ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x))
        if hit:
            return {"name": hit["name"], "lat": round(hit["latitude"], 4), "lon": round(hit["longitude"], 4)}


def iris_eva(name):
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://iris.noncd.db.de/iris-tts/timetable/station/" + urllib.parse.quote(name), headers=UA), timeout=20) as r:
            m = re.search(r'name="([^"]+)" eva="(\d+)"', r.read().decode("utf-8"))
        return m.group(2) if m else None
    except Exception:
        return None


def nearby_train_stations(loc):
    """Bahnhöfe und Haltepunkte im Umkreis von 6 km aus OpenStreetMap (Overpass, ohne Schlüssel)."""
    import math
    q = '[out:json][timeout:20];node(around:6000,%f,%f)[railway~"^(station|halt)$"];out;' % (loc["lat"], loc["lon"])
    elements = None
    for attempt in range(2):  # Overpass ist manchmal kurz überlastet
        try:
            req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                         data=urllib.parse.urlencode({"data": q}).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                elements = json.loads(r.read().decode("utf-8")).get("elements") or []
            break
        except Exception:
            time.sleep(5)
    if elements is None:  # Ersatz: Fahrplan-Suche nach Koordinate (findet nur die allernächsten)
        try:
            elements = [{"lat": st["coordinate"]["x"], "lon": st["coordinate"]["y"], "tags": {"name": st["name"]}}
                        for st in get_json("https://transport.opendata.ch/v1/locations?" + urllib.parse.urlencode(
                            {"x": loc["lat"], "y": loc["lon"], "type": "station"})).get("stations") or []
                        if st.get("icon") == "train" and (st.get("coordinate") or {}).get("x") is not None]
        except Exception:
            return []
    found = {}
    for e in elements:
        name = (e.get("tags") or {}).get("name")
        if not name:
            continue
        dy = (e["lat"] - loc["lat"]) * 111.2
        dx = (e["lon"] - loc["lon"]) * 111.2 * math.cos(math.radians(loc["lat"]))
        d = math.hypot(dx, dy) * 1000
        if name not in found or d < found[name]["distance"]:
            found[name] = {"name": name, "distance": d}
    return sorted(found.values(), key=lambda s: s["distance"])[:9]


def similar(a, b):
    import difflib
    a, b = a.lower(), b.lower()
    return a == b or a.startswith(b) or b.startswith(a) or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def resolve_station(name):
    """Name -> IDs für transport.opendata.ch und DB IRIS (beide optional)."""
    out = {"station_name": name, "opendata_id": ""}
    variants = list(dict.fromkeys([name, re.sub(r"\bHauptbahnhof\b", "Hbf", name), re.sub(r"\bBahnhof\b", "Bf", name)]))
    for v in variants:
        try:
            res = [s for s in get_json("https://transport.opendata.ch/v1/locations?" + urllib.parse.urlencode(
                {"query": v, "type": "station"})).get("stations") or [] if s.get("id") and s.get("icon") in (None, "train")]
        except Exception:
            res = []
        res = [s for s in res if similar(s["name"], v)]  # die Suche ist unscharf: nur passende Namen
        if res:
            out.update(station_name=res[0]["name"], opendata_id=res[0]["id"])
            break
    eva = None
    for v in [out["station_name"]] + variants:
        eva = eva or iris_eva(v)
    if eva:
        out["iris_eva"] = eva
    return out


def ask_station(t, loc):
    if loc:
        t.say("  suche Bahnhöfe in der Nähe …")
    stations = nearby_train_stations(loc) if loc else []
    trains = None
    if stations:
        t.say("Bahnhöfe in der Nähe:")
        hit = t.choose(stations, lambda s: "%s (%.1f km)" % (s["name"], s["distance"] / 1000.0), "Namen selbst eingeben")
        if hit:
            trains = resolve_station(hit["name"])
    while not trains or not (trains.get("opendata_id") or trains.get("iris_eva")):
        if trains:
            t.say("  Für diesen Bahnhof gibt es keine Live-Daten, bitte anders schreiben.")
        q = t.ask("Bahnhof (leer = keine Abfahrts-Kachel)")
        if not q:
            return None
        trains = resolve_station(q)
    t.say("  -> %s" % trains["station_name"])
    lines = t.ask("Nur bestimmte Linien? z. B. 'S1 S2' (leer = alle)")
    if lines:
        trains["lines"] = lines.replace(",", " ").split()
    words = t.ask("Nach Richtung trennen? Ziele der einen Richtung, z. B. 'Nordstadt Flughafen' (leer = eine Liste)")
    if words:
        ws = words.replace(",", " ").split()
        trains["groups"] = [{"title": "Richtung " + " · ".join(ws), "match": "|".join(re.escape(w.lower()) for w in ws)},
                            {"title": t.ask("Name der Gegenrichtung", "Gegenrichtung"), "match": "."}]
    return trains


def ask_waste(t, loc):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import server
    while True:
        q = t.ask("Website eurer Abfallwirtschaft oder Link zum Müllkalender (leer = keine Müll-Kachel)")
        if not q:
            return None
        t.say("  suche Abfuhrkalender …")
        kind, url = server.find_waste_source(q)
        if kind == "ics":
            t.say("  iCal-Kalender gefunden.")
            return {"provider": "ics", "ics_url": url}
        if kind == "athos":
            break
        t.say("  Dort habe ich keinen Abfuhrkalender gefunden. Bitte den Link der Seite mit den Abfuhrterminen eingeben.")
    t.say("  Abfuhrtermine-Portal gefunden.")
    p = server.AthosPortal({"portal_url": url}).open()
    show = lambda x: x.replace("\xa0", " ")
    towns = p.options("Ort")
    ort = pick_filtered(t, towns, "Gemeinde (Anfang tippen)", show, loc["name"] if loc else "") \
        if len(towns) > 1 else (towns[0] if towns else "")
    p.choose_city(ort)
    strasse = pick_filtered(t, p.options("Strasse"), "Straße (Anfang tippen)", show)
    hn = t.ask("Hausnummer")
    boxes = p.containers()
    default = [n for n, label in boxes if re.search(r"restm.*(2|wöch)|gelb", label, re.I)] or [n for n, _ in boxes[:1]]
    t.say("Welche Abfuhren anzeigen?")
    for n, label in boxes:
        t.say("  %d) %s" % (n, label))
    nums = t.ask("Nummern mit Leerzeichen", " ".join(str(n) for n in default))
    containers = [int(n) for n in re.findall(r"\d+", nums)]
    t.say("  prüfe Adresse …")
    w = {"provider": "athos", "portal_url": url, "ort": ort, "strasse": strasse, "hausnummer": hn, "containers": containers}
    try:
        n = server.parse_ics(server.AthosPortal(w).ical())
        t.say("  ok, %d Termine gefunden." % len(n))
    except Exception as e:
        t.say("  Achtung, Test fehlgeschlagen (%s) – Einstellung wird trotzdem gespeichert." % e)
    return w


def interactive(t):
    cfg = {}
    t.say("\n1/3 Wetter")
    loc = ask_location(t)
    if loc:
        cfg["location"] = loc
    t.say("\n2/3 Abfahrten")
    trains = ask_station(t, loc)
    if trains:
        cfg["trains"] = trains
    t.say("\n3/3 Müllabfuhr")
    waste = ask_waste(t, loc)
    if waste:
        cfg["waste"] = waste
    return cfg


def write(cfg):
    d = os.path.dirname(CONFIG_PATH)
    if not os.path.isdir(d):
        os.makedirs(d, 0o700)
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    print("Einstellungen gespeichert: %s" % CONFIG_PATH)


def main(argv):
    if "--make-code" in argv:
        with open(argv[argv.index("--make-code") + 1], encoding="utf-8") as f:
            print(make_code(json.load(f)))
        return 0
    if "--code" in argv:
        write(read_code(argv[argv.index("--code") + 1]))
        return 0
    if os.path.exists(CONFIG_PATH) and "--reconfigure" not in argv:
        print("Einstellungen vorhanden: %s (neu: --reconfigure)" % CONFIG_PATH)
        return 0
    try:
        t = Tty()
    except OSError:
        print("Kein Terminal für Fragen – später einrichten mit: python3 %s --reconfigure" % os.path.abspath(__file__))
        return 0
    t.say("\nEinrichtung Flur-Dashboard. Die Angaben bleiben nur auf diesem Rechner.")
    while True:
        code = t.ask("Setup-Code einfügen, falls vorhanden (Enter = Fragen beantworten)")
        if not code:
            cfg = interactive(t)
            break
        try:
            cfg = read_code(code)
            break
        except Exception as e:
            t.say("  %s" % e)
    write(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
