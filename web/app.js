/* Flur-Dashboard frontend. Plain ES2017, no build step, no libraries.
   Stromsparen: Es gibt genau einen Takt alle 10 s (an :00/:10/… ausgerichtet). Nur dann wird gezeichnet;
   keine CSS-Animationen, keine Übergänge. Ist der Bildschirm nachts aus, tut der Takt nichts außer prüfen. */
(function () {
  "use strict";

  var TICK_MS = 10 * 1000;
  var POLL_TICKS = 6;    // alle 60 s Daten vom lokalen Server
  var NEWS_TICKS = 2;    // 20 s
  var TIP_TICKS = 3;     // 30 s
  var PHOTO_TICKS = 4;   // 40 s
  var STALE_AFTER = { weather: 3600, trains: 300, news: 3600, waste: 3 * 86400, reddit: 3 * 3600 };

  var state = { snap: null, version: null, newsPage: 0, photoIdx: -1, photoFront: "a", lastOk: 0 };
  var $ = function (id) { return document.getElementById(id); };
  var WD = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
  var WD_SHORT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
  var MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
  var MON_SHORT = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hm(d) { return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function daysUntil(dateStr) {
    var p = dateStr.split("-");
    var target = new Date(+p[0], +p[1] - 1, +p[2]);
    return Math.round((target - dayStart(new Date())) / 86400000);
  }
  function isoWeek(d) {
    var t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    var day = t.getUTCDay() || 7;
    t.setUTCDate(t.getUTCDate() + 4 - day);
    var y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
    return Math.ceil(((t - y0) / 86400000 + 1) / 7);
  }
  function ago(ts) {
    var m = Math.round((Date.now() / 1000 - ts) / 60);
    if (m < 60) return "vor " + Math.max(1, m) + " Min.";
    var h = Math.round(m / 60);
    return h < 24 ? "vor " + h + " Std." : "vor " + Math.round(h / 24) + " T.";
  }

  /* ---------------- Uhr (ohne Sekunden, nur bei Minutenwechsel neu gezeichnet) ---------------- */
  var lastMinute = -1;
  function drawClock() {
    var d = new Date();
    if (d.getMinutes() === lastMinute) return false;
    lastMinute = d.getMinutes();
    $("time").innerHTML = pad(d.getHours()) + '<span class="sep">:</span>' + pad(d.getMinutes());
    $("date").innerHTML = WD[d.getDay()] + ", " + d.getDate() + ". " + MON[d.getMonth()] +
      '<span class="kw">KW ' + isoWeek(d) + "</span>";
    return true;
  }

  /* ---------------- Status / "Stand" ---------------- */
  function stamp(tileId, src, name) {
    var el = document.querySelector("#" + tileId + " .stamp");
    if (!el) return;
    if (!src || !src.updated) { el.textContent = src && src.error ? "Quelle nicht erreichbar" : "lädt …"; el.className = "stamp stale"; return; }
    var age = Date.now() / 1000 - src.updated;
    var stale = !src.ok || age > (STALE_AFTER[name] || 3600);
    var d = new Date(src.updated * 1000);
    var when = daysUntil(d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())) === 0 ? hm(d) : d.getDate() + "." + (d.getMonth() + 1) + ". " + hm(d);
    el.textContent = stale ? "Stand " + when + " · Quelle gestört" : "Stand " + when;
    el.className = stale ? "stamp stale" : "stamp";
  }
  function src(name) { return state.snap && state.snap.sources[name]; }
  // Ort-Kacheln ohne lokale Konfiguration: Hinweis statt Fehler
  function notConfigured(tileId, name) {
    var s = src(name);
    if (!s || s.configured !== false) return false;
    document.querySelector("#" + tileId + " .stamp").textContent = "";
    document.querySelector("#" + tileId + " .body").innerHTML =
      '<div class="empty">Nicht eingerichtet – siehe ~/.config/imac-dashboard/config.json</div>';
    return true;
  }
  function data(name) { var s = src(name); return s && s.data; }

  /* ---------------- Wetter-Icons (inline SVG) ---------------- */
  var C = { sun: "#ffb547", cloud: "#bdb7ad", dark: "#847e75", rain: "#7fb3e6", snow: "#efebe4", bolt: "#ffb547", moon: "#e8dcc0" };
  function sun(cx, cy, r) {
    var rays = "";
    for (var i = 0; i < 8; i++) {
      var a = i * Math.PI / 4, x1 = cx + Math.cos(a) * (r + 4), y1 = cy + Math.sin(a) * (r + 4), x2 = cx + Math.cos(a) * (r + 9), y2 = cy + Math.sin(a) * (r + 9);
      rays += '<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '"/>';
    }
    return '<g stroke="' + C.sun + '" stroke-width="3.2" stroke-linecap="round" fill="none"><circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + C.sun + '" stroke="none"/>' + rays + "</g>";
  }
  function moon(cx, cy, r) {
    return '<path fill="' + C.moon + '" d="M' + (cx + r * 0.3) + " " + (cy - r) + " A" + r + " " + r + " 0 1 0 " + (cx + r) + " " + (cy + r * 0.35) +
      " A" + (r * 0.8) + " " + (r * 0.8) + " 0 0 1 " + (cx + r * 0.3) + " " + (cy - r) + 'Z"/>';
  }
  function cloud(dx, dy, s, col) {
    return '<path transform="translate(' + dx + " " + dy + ") scale(" + s + ')" fill="' + col + '" d="M14 40 h30 a11 11 0 0 0 0-22 a15 15 0 0 0-28-4 a11 11 0 0 0-2 26z"/>';
  }
  function drops(n, col) {
    var out = "";
    for (var i = 0; i < n; i++) out += '<line x1="' + (22 + i * 9) + '" y1="42" x2="' + (19 + i * 9) + '" y2="51" stroke="' + col + '" stroke-width="3.2" stroke-linecap="round"/>';
    return out;
  }
  function flakes(n) {
    var out = "";
    for (var i = 0; i < n; i++) out += '<circle cx="' + (22 + i * 9) + '" cy="' + (44 + (i % 2) * 6) + '" r="2.6" fill="' + C.snow + '"/>';
    return out;
  }
  function wxIcon(code, isDay) {
    var orb = isDay ? sun(24, 22, 9) : moon(24, 22, 11);
    var body;
    if (code === 0) body = isDay ? sun(32, 32, 12) : moon(32, 30, 15);
    else if (code === 1 || code === 2) body = orb + cloud(10, 14, 0.85, C.cloud);
    else if (code === 3) body = cloud(-2, 2, 0.8, C.dark) + cloud(8, 12, 0.85, C.cloud);
    else if (code === 45 || code === 48) body = cloud(4, 0, 0.85, C.cloud) + '<g stroke="' + C.dark + '" stroke-width="3.2" stroke-linecap="round"><line x1="12" y1="44" x2="52" y2="44"/><line x1="18" y1="52" x2="46" y2="52"/></g>';
    else if (code >= 51 && code <= 57) body = cloud(4, 0, 0.85, C.cloud) + drops(2, C.rain);
    else if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) body = (code >= 80 ? orb : "") + cloud(4, 0, 0.85, code >= 63 && code < 80 ? C.dark : C.cloud) + drops(code === 61 || code === 80 ? 2 : 3, C.rain);
    else if ((code >= 71 && code <= 77) || code === 85 || code === 86) body = cloud(4, 0, 0.85, C.cloud) + flakes(3);
    else if (code >= 95) body = cloud(4, 0, 0.85, C.dark) + '<path fill="' + C.bolt + '" d="M32 36 l-7 12 h6 l-3 10 l10-14 h-6 l4-8z"/>';
    else body = cloud(4, 6, 0.85, C.cloud);
    return '<svg viewBox="0 0 64 64" aria-hidden="true">' + body + "</svg>";
  }
  function wxText(code) {
    if (code === 0) return "Klar";
    if (code === 1) return "Überwiegend klar";
    if (code === 2) return "Teils bewölkt";
    if (code === 3) return "Bedeckt";
    if (code === 45 || code === 48) return "Nebel";
    if (code >= 51 && code <= 57) return "Nieselregen";
    if (code >= 61 && code <= 65) return code === 65 ? "Starker Regen" : "Regen";
    if (code === 66 || code === 67) return "Gefrierender Regen";
    if (code >= 71 && code <= 77) return "Schnee";
    if (code >= 80 && code <= 82) return "Regenschauer";
    if (code === 85 || code === 86) return "Schneeschauer";
    if (code >= 95) return "Gewitter";
    return "";
  }
  function deg(v) { return Math.round(v) + "°"; }
  function popHtml(p) { return '<div class="pop' + (p >= 20 ? "" : " dry") + '">' + (p >= 20 ? p + " %" : "–") + "</div>"; }

  function renderWeather() {
    if (notConfigured("weather", "weather")) return;
    var s = src("weather"), w = data("weather");
    stamp("weather", s, "weather");
    var body = document.querySelector("#weather .body");
    if (!w) { body.innerHTML = '<div class="empty">Keine Wetterdaten.</div>'; return; }
    var now = w.now;
    var nowHour = new Date(); nowHour.setMinutes(0, 0, 0);
    var hours = w.hours.filter(function (h) { return new Date(h.time) > nowHour; });
    if (Date.now() / 1000 - s.updated > 2 * 3600) {
      // alte Messung nicht als "jetzt" ausgeben – lieber die Vorhersage für diese Stunde
      var cur = w.hours.filter(function (h) { return new Date(h.time).getTime() === nowHour.getTime(); })[0];
      if (!cur) { body.innerHTML = '<div class="empty">Keine aktuellen Wetterdaten.</div>'; return; }
      now = { temp: cur.temp, feels: cur.temp, code: cur.code, wind: now.wind, day: cur.day, forecast: true };
    }
    var picks = [];
    for (var i = 0; i < hours.length && picks.length < 6; i += 2) picks.push(hours[i]);
    var days = w.days.filter(function (d) { return daysUntil(d.date) >= 1; }).slice(0, 3);
    var sun = w.days.filter(function (d) { return daysUntil(d.date) === 0; })[0];
    var html =
      '<div class="wx-now">' + wxIcon(now.code, now.day) +
      '<div><div class="temp">' + deg(now.temp) + '</div><div class="desc">' + wxText(now.code) + "</div>" +
      '<div class="meta">' + (now.forecast ? "Vorhersage, keine Messung" : "gefühlt " + deg(now.feels) + " · Wind " + Math.round(now.wind) + " km/h") +
      (sun ? "<br>Sonne " + sun.sunrise.slice(11) + " – " + sun.sunset.slice(11) : "") + "</div></div></div>" +
      '<div class="wx-hours">' + picks.map(function (h) {
        return '<div class="h"><div class="t">' + h.time.slice(11, 13) + " Uhr</div>" + wxIcon(h.code, h.day) +
          '<div class="v">' + deg(h.temp) + "</div>" + popHtml(h.pop) + "</div>";
      }).join("") + "</div>" +
      '<div class="wx-days">' + days.map(function (d) {
        var dd = new Date(d.date + "T12:00:00");
        var name = daysUntil(d.date) === 1 ? "Morgen" : WD_SHORT[dd.getDay()] + " " + dd.getDate() + ".";
        return '<div class="d"><span class="n">' + name + "</span>" + wxIcon(d.code, 1) +
          '<span class="range"><span class="lo">' + deg(d.min) + "</span>" + deg(d.max) + "</span>" +
          '<span class="pop' + (d.pop >= 20 ? "" : " dry") + '">' + (d.pop >= 20 ? d.pop + " %" : "–") + "</span></div>";
      }).join("") + "</div>";
    body.innerHTML = html;
  }

  /* ---------------- Züge ---------------- */
  var lastTrainsHtml = "";
  function renderTrains() {
    if (!state.snap) return;
    if (notConfigured("trains", "trains")) return;
    var s = src("trains"), t = data("trains");
    stamp("trains", s, "trains");
    var body = document.querySelector("#trains .body");
    if (!t) { body.innerHTML = '<div class="empty">Keine Abfahrtsdaten.</div>'; return; }
    document.querySelector("#trains h2").textContent = t.station ? "Abfahrten ab " + t.station : "Abfahrten";
    var nowS = Date.now() / 1000;
    var deps = t.departures.map(function (d) {
      var real = d.planned + (d.delay || 0) * 60;
      return { d: d, real: real, mins: Math.floor((real - nowS) / 60) };
    }).filter(function (x) { return x.real > nowS - 30; });
    // Richtungen aus der lokalen Konfiguration; was keine Gruppe trifft, kommt in die letzte
    var cfgGroups = t.groups || [];
    var groups = cfgGroups.length ? cfgGroups.map(function (g) { return { title: g.title, rx: new RegExp(g.match, "i"), rows: [], max: 3 }; })
      : [{ title: "", rx: /./, rows: [], max: 6 }];
    deps.forEach(function (x) {
      var g = groups.filter(function (g) { return g.rx.test(x.d.to); })[0] || groups[groups.length - 1];
      if (g.rows.length < g.max) g.rows.push(x);
    });
    var rename = t.rename || {};
    // Linien abwechselnd gefüllt/umrandet, damit zwei Linien auf einen Blick unterscheidbar sind
    var lineNames = t.departures.map(function (d) { return d.line; }).filter(function (l, i, a) { return a.indexOf(l) === i; }).sort();
    var html = groups.map(function (g) {
      var firstLive = true;
      var rows = g.rows.map(function (x) {
        var d = x.d, cls = "dep";
        if (d.cancelled) cls += " cancel";
        else if (firstLive) { cls += " next"; firstLive = false; }
        var planned = new Date(d.planned * 1000);
        var late = d.delay && d.delay > 0 ? "+" + d.delay : "";
        var mins = d.cancelled ? "fällt aus" : x.mins <= 0 ? "jetzt" : x.mins + "<span class=\"ap\">′</span>";
        if (!d.cancelled && x.mins > 59) mins = hm(new Date(x.real * 1000));
        return '<div class="' + cls + '"><span class="line' + (lineNames.indexOf(d.line) % 2 ? " alt" : "") + '">' + esc(d.line) + "</span>" +
          '<span class="to">' + esc(rename[d.to] || d.to.replace(/\s*\((D|CH|F|A|I)\)$/, "")) + "</span>" +
          '<span class="clk">' + hm(planned) + (late ? '<span class="late">' + late + "</span>" : "") + "</span>" +
          '<span class="mins">' + mins + "</span></div>";
      }).join("");
      var none = s.ok && nowS - s.updated < STALE_AFTER.trains ? "Keine Abfahrten in Sicht." : "Keine aktuellen Daten.";
      return '<div class="dir">' + (g.title ? "<h3>" + esc(g.title) + "</h3>" : "") + (rows || '<div class="empty">' + none + "</div>") + "</div>";
    }).join("");
    if (html !== lastTrainsHtml) { body.innerHTML = html; lastTrainsHtml = html; }
  }

  /* ---------------- Müll ---------------- */
  var BIN_ICONS = {
    gelb: '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#f4cf45" d="M15 8 q9 6 18 0 l-2 6 q6 4 7 14 q1 14-14 14 q-15 0-14-14 q1-10 7-14z"/><path d="M19 14 q5 2 10 0" stroke="#16191c" stroke-width="2" fill="none" opacity=".5"/></svg>',
  };
  function binIcon(lid, body) {
    return '<svg viewBox="0 0 48 48" aria-hidden="true"><rect x="10" y="8" width="28" height="5" rx="1.5" fill="' + lid + '"/><path fill="' + body +
      '" d="M12 15 h24 l-2.5 25 h-19z"/><circle cx="16" cy="42" r="2.6" fill="' + lid + '"/><circle cx="32" cy="42" r="2.6" fill="' + lid + '"/></svg>';
  }
  BIN_ICONS.rest = binIcon("#8e959c", "#5d6369");
  BIN_ICONS.bio = binIcon("#9a7a55", "#6b5236");
  BIN_ICONS.papier = binIcon("#7fa2c9", "#46627f");

  function renderWaste() {
    if (!state.snap || notConfigured("waste", "waste")) return;
    var s = src("waste"), w = data("waste");
    stamp("waste", s, "waste");
    var body = document.querySelector("#waste .body");
    if (!w) { body.innerHTML = '<div class="empty">Kalender nicht erreichbar.</div>'; return; }
    var nowH = new Date().getHours();
    var rows = (w.types || []).map(function (ty) {
      var kind = ty.kind, icon = BIN_ICONS[kind] || BIN_ICONS.rest, label = esc(ty.label);
      // ab 10 Uhr am Abfuhrtag ist der Termin vorbei
      var next = w.events.filter(function (e) {
        var n = daysUntil(e.date);
        return e.kind === kind && (n > 0 || (n === 0 && nowH < 10));
      })[0];
      if (!next) return { kind: kind, n: 9999, html: '<div class="bin">' + icon + '<div class="what"><div class="name">' + label +
        '</div><div class="when">Noch kein Termin bekannt</div></div></div>' };
      var n = daysUntil(next.date);
      var p = next.date.split("-"), dt = new Date(+p[0], +p[1] - 1, +p[2]);
      var when = WD_SHORT[dt.getDay()] + ", " + dt.getDate() + ". " + MON_SHORT[dt.getMonth()];
      var soon = n === 0 || (n === 1 && nowH >= 12);
      var inTxt = n === 0 ? "Heute" : n === 1 ? "Morgen" : "in " + n + " Tagen";
      var hint = n === 0 ? "bis 6 Uhr rausstellen" : n === 1 ? "heute Abend rausstellen" : when;
      return { kind: kind, n: n, html: '<div class="bin ' + esc(kind) + (soon ? " soon" : "") + '">' + icon +
        '<div class="what"><div class="name">' + label + '</div><div class="when">' + (n <= 1 ? when + " · " + hint : when) + "</div></div>" +
        '<div class="in">' + inTxt + "</div></div>" };
    });
    rows.sort(function (a, b) { return a.n - b.n; });
    body.innerHTML = rows.map(function (r) { return r.html; }).join("");
  }

  /* ---------------- Nachrichten ---------------- */
  // Beide Feeds bleiben sichtbar; jeder zeigt so viele Schlagzeilen wie passen
  // und blättert beim nächsten Wechsel dort weiter, wo er aufgehört hat.
  var newsPos = {};
  function renderNews(advance) {
    var s = src("news"), n = data("news");
    stamp("news", s, "news");
    var body = document.querySelector("#news .body");
    if (!n || !n.feeds.length) { body.innerHTML = '<div class="empty">Keine Nachrichten.</div>'; return; }
    n.feeds.forEach(function (f) {
      var p = newsPos[f.name] || (newsPos[f.name] = { start: 0, shown: 0 });
      if (advance) { p.start += p.shown; if (p.start >= f.items.length) p.start = 0; }
    });
    var html = n.feeds.map(function (f) {
      var items = f.items.slice(newsPos[f.name].start, newsPos[f.name].start + 6);
      return '<div class="feed" data-name="' + esc(f.name) + '"><h3><span class="lang">' + esc(f.lang.toUpperCase()) + "</span>" + esc(f.name) +
        (f.ok === false ? " · alter Stand" : "") + "</h3>" +
        items.map(function (i) {
          return '<div class="headline"><div class="hl">' + esc(i.title) + (i.ts ? '<span class="ago">' + ago(i.ts) + "</span>" : "") + "</div></div>";
        }).join("") + "</div>";
    }).join("");
    swap(body, html, advance, function () {
      Array.prototype.forEach.call(body.querySelectorAll(".feed"), function (el) {
        newsPos[el.getAttribute("data-name")].shown = Math.max(1, trimToFit(el, ".headline"));
      });
    });
  }

  // Entfernt so lange das letzte Element, bis der Container nicht mehr überläuft.
  function trimToFit(box, sel) {
    var rows = box.querySelectorAll(sel), n = rows.length;
    while (n > 1 && box.scrollHeight > box.clientHeight + 1) { rows[n - 1].remove(); n--; }
    return n;
  }

  function swap(body, html, animate, after) {
    body.innerHTML = html;  // bewusst ohne Überblendung: spart Neuzeichnen
    if (after) after();
  }

  /* ---------------- Ladebildschirm-Tipps ---------------- */
  // Kuratierte Liste in tips.json, gemischt ohne Wiederholung, bis alle einmal dran waren.
  // Der Stapel überlebt Neuladen der Seite (localStorage), damit nicht immer dieselben zuerst kommen.
  var tips = [], tipStep = 0, tipsLoading = false;
  var TIP_PCT = [8, 57, 99];  // ein Schritt pro Takt; bleibt wie jeder echte Ladebalken bei 99 % hängen

  function loadTips() {
    if (tipsLoading) return;
    tipsLoading = true;
    fetch("/tips.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (list) { tips = list; nextTip(); })
      .catch(function () { /* nächster Versuch beim nächsten Daten-Takt */ })
      .then(function () { tipsLoading = false; });
  }

  function shuffled(n, avoidFirst) {
    var a = [];
    for (var i = 0; i < n; i++) a.push(i);
    for (var j = n - 1; j > 0; j--) { var k = Math.floor(Math.random() * (j + 1)), t = a[j]; a[j] = a[k]; a[k] = t; }
    if (n > 1 && a[0] === avoidFirst) { a.push(a.shift()); }
    return a;
  }

  function takeTip() {
    var deck = null;
    try { deck = JSON.parse(localStorage.getItem("tipDeck")); } catch (e) { /* egal */ }
    if (!deck || deck.n !== tips.length || !deck.order || deck.i >= deck.order.length) {
      var last = deck && deck.order ? deck.order[deck.order.length - 1] : -1;
      deck = { n: tips.length, order: shuffled(tips.length, last), i: 0 };
    }
    var idx = deck.order[deck.i++];
    try { localStorage.setItem("tipDeck", JSON.stringify(deck)); } catch (e) { /* egal */ }
    return tips[idx];
  }

  function isGerman(s) { return /[äöüß„]|\b(der|die|das|und|ist|nicht|du|dich|ein|eine|mit|wer|zu)\b/i.test(s); }

  function nextTip() {
    if (!tips.length) return;
    var tip = takeTip(), de = isGerman(tip);
    $("tip-text").textContent = tip;
    $("tip-label").textContent = de ? "Tipp" : "Tip";
    $("tip-ld").textContent = de ? "Lädt …" : "Loading …";
    tipStep = 0;
    drawLoading();
  }

  function drawLoading() {
    var pct = TIP_PCT[Math.min(tipStep, TIP_PCT.length - 1)];
    $("tip-pct").textContent = pct + " %";
    $("tip-bar").style.transform = "scaleX(" + (pct / 100) + ")";
  }

  /* ---------------- Bild ---------------- */
  function renderPhoto(advance) {
    var r = data("reddit");
    var list = ((r && r.images) || []).filter(function (i) { return i.local; });
    var tile = $("photo");
    var empty = tile.querySelector(".empty");
    if (!list.length) {
      if (!empty) { empty = document.createElement("div"); empty.className = "empty"; empty.textContent = "Noch keine Katzen geladen …"; tile.appendChild(empty); }
      return;
    }
    if (empty) empty.remove();
    if (!advance && state.photoIdx >= 0) return;
    state.photoIdx = (state.photoIdx + 1) % list.length;
    var item = list[state.photoIdx];
    // Erst fertig dekodieren, dann in einem Schritt austauschen (kein Ruckeln, kein halbes Bild)
    var img = new Image();
    img.alt = "";
    img.src = "/img/" + item.local;
    var ready = img.decode ? img.decode() : new Promise(function (ok, bad) { img.onload = ok; img.onerror = bad; });
    ready.then(function () {
      var frame = tile.querySelector(".frame");
      frame.innerHTML = "";
      frame.appendChild(img);
      tile.querySelector(".caption .title").textContent = item.title || "";
      tile.querySelector(".caption .sub").textContent = item.source || (item.sub ? "r/" + item.sub : "");
    }).catch(function () { /* kaputtes Bild: beim nächsten Bild-Takt das nächste */ });
  }

  /* ---------------- Nacht ---------------- */
  function minutesOf(s) { var p = s.split(":"); return +p[0] * 60 + +p[1]; }
  function applyNight() {
    var n = state.snap && state.snap.night;
    if (!n) return false;
    var d = new Date(), m = d.getHours() * 60 + d.getMinutes(), a = minutesOf(n.from), b = minutesOf(n.to);
    var on = a > b ? (m >= a || m < b) : (m >= a && m < b);
    if (document.body.classList.contains("night") !== on) document.body.classList.toggle("night", on);
    document.body.style.setProperty("--dim", n.dim);
    return on;
  }
  // Bildschirm nachts per DPMS aus: dann ist jedes Zeichnen verschwendet
  function screenOff() { return applyNight() && state.snap.night.mode === "off"; }

  /* ---------------- Daten holen ---------------- */
  function poll() {
    var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = ctl && setTimeout(function () { ctl.abort(); }, 15000);
    fetch("/api/all", { cache: "no-store", signal: ctl ? ctl.signal : undefined })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (snap) {
        if (state.version && snap.version !== state.version) { location.reload(); return; }
        state.version = snap.version;
        var first = !state.snap;
        state.snap = snap;
        state.lastOk = Date.now();
        try { localStorage.setItem("snap", JSON.stringify(snap)); } catch (e) { /* egal */ }
        $("offline").hidden = true;
        renderAll(first);
        heartbeat(snap.version);
      })
      .catch(function () {
        if (!state.snap) {
          try { state.snap = JSON.parse(localStorage.getItem("snap")); if (state.snap) renderAll(true); } catch (e) { /* egal */ }
        }
        $("offline").hidden = false;
      })
      .then(function () { if (timer) clearTimeout(timer); });
  }

  // Meldet dem Server "Seite läuft": CSS geladen, Uhr gezeichnet, Rendern ohne Fehler.
  // update.sh rollt zurück, wenn nach einem Update diese Meldung ausbleibt.
  function heartbeat(version) {
    var board = document.querySelector(".board");
    if (!board || getComputedStyle(board).display !== "flex" || $("time").textContent.indexOf(":") < 0) return;
    fetch("/api/alive?v=" + encodeURIComponent(version), { cache: "no-store" }).catch(function () { /* egal */ });
  }

  function renderAll(first) {
    applyNight();
    renderTrains();
    renderWaste();
    renderWeather();
    renderNews(false);
    renderPhoto(first || state.photoIdx < 0);
  }

  /* ---------------- Der eine Takt ---------------- */
  var wasOff = false;
  function tick() {
    var n = Math.floor(Date.now() / TICK_MS);
    if (screenOff()) { wasOff = true; return; }
    var woke = wasOff;
    wasOff = false;
    if (n % POLL_TICKS === 0 || woke || !state.snap) poll();
    if (!tips.length) loadTips();
    if (drawClock() || woke) renderWaste();
    renderTrains();  // Countdown; zeichnet nur, wenn sich etwas geändert hat
    if (n % NEWS_TICKS === 0) renderNews(true);
    if (n % TIP_TICKS === 0) nextTip(); else { tipStep++; drawLoading(); }
    if (n % PHOTO_TICKS === 0) renderPhoto(true);
  }
  function schedule() {  // an der nächsten vollen 10 s ausrichten, damit die Minute pünktlich umspringt
    setTimeout(function () { tick(); schedule(); }, TICK_MS - (Date.now() % TICK_MS) + 20);
  }

  drawClock();
  poll();
  loadTips();
  schedule();
})();
