#!/usr/bin/env python3
"""Legt die lokale Konfiguration an (~/.config/imac-dashboard/config.json).

Ortsangaben gehören nur in diese Datei, nie ins Repo. Zwei Wege:
  * Setup-Code einfügen (privat erzeugt mit:  python3 setup.py --make-code meine-config.json)
  * Fragen beantworten: Ort fürs Wetter, Bahnhof, optional iCal-Adresse des Müllkalenders

  python3 setup.py                 nur wenn noch keine Konfiguration existiert
  python3 setup.py --reconfigure   neu einrichten
  python3 setup.py --code CODE     Setup-Code direkt übergeben
"""
import base64
import json
import os
import re
import sys
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

    def choose(self, items, label):
        for i, it in enumerate(items, 1):
            self.say("  %d) %s" % (i, label(it)))
        while True:
            a = self.ask("Nummer (Enter = 1, 0 = überspringen)", "1")
            if a.isdigit() and 0 <= int(a) <= len(items):
                return items[int(a) - 1] if int(a) else None


def interactive(t):
    cfg = {}
    t.say("\nOrt fürs Wetter")
    while True:
        q = t.ask("Stadt oder Ortsteil (leer = keine Wetter-Kachel)")
        if not q:
            break
        try:
            res = get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": q, "count": 6, "language": "de"})).get("results") or []
        except Exception as e:
            t.say("  Suche fehlgeschlagen: %s" % e)
            continue
        if not res:
            t.say("  Nichts gefunden, bitte anders schreiben.")
            continue
        hit = t.choose(res, lambda r: ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x))
        if hit:
            cfg["location"] = {"name": hit["name"], "lat": round(hit["latitude"], 4), "lon": round(hit["longitude"], 4)}
            break

    t.say("\nBahnhof für die Abfahrten")
    while True:
        q = t.ask("Bahnhof (leer = keine Abfahrts-Kachel)")
        if not q:
            break
        trains = {}
        try:
            res = [s for s in get_json("https://transport.opendata.ch/v1/locations?" + urllib.parse.urlencode(
                {"query": q, "type": "station"})).get("stations") or [] if s.get("id")]
        except Exception:
            res = []
        hit = t.choose(res[:6], lambda s: s["name"]) if res else None
        name = hit["name"] if hit else q
        if hit:
            trains.update(station_name=hit["name"], opendata_id=hit["id"])
        try:  # DB IRIS als zweite Quelle (deutsche Bahnhöfe)
            with urllib.request.urlopen(urllib.request.Request(
                    "https://iris.noncd.db.de/iris-tts/timetable/station/" + urllib.parse.quote(name), headers=UA), timeout=20) as r:
                m = re.search(r'name="([^"]+)" eva="(\d+)"', r.read().decode("utf-8"))
            if m:
                trains.setdefault("station_name", m.group(1))
                trains["iris_eva"] = m.group(2)
        except Exception:
            pass
        if not trains:
            t.say("  Nichts gefunden, bitte anders schreiben.")
            continue
        lines = t.ask("Nur bestimmte Linien? z. B. 'S1 S2' (leer = alle)")
        if lines:
            trains["lines"] = lines.replace(",", " ").split()
        cfg["trains"] = trains
        t.say("  Richtungen gruppieren/Ziele kürzen geht später in der Datei ('groups', 'rename').")
        break

    t.say("\nMüllkalender (optional)")
    url = t.ask("iCal-Adresse (.ics) deines Abfallkalenders (leer = keine Müll-Kachel)")
    if url:
        cfg["waste"] = {"provider": "ics", "ics_url": url}
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
        code = t.ask("Setup-Code einfügen (Enter = stattdessen Fragen beantworten)")
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
