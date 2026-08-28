#!/usr/bin/env python3
"""
Claude Code Session Dashboard

  python3 dash.py            einmal bauen und oeffnen
  python3 dash.py --serve    Server auf http://localhost:8787 (empfohlen)
  python3 dash.py --quiet    nur bauen

Liest ausschliesslich lokale Dateien unter ~/.claude. Nichts verlaesst den Rechner.
"""
import os, sys, json, html, subprocess, datetime, re, shlex, threading, time, secrets, shutil

ROOT   = os.path.expanduser("~/.claude/projects")
DATEN  = os.path.expanduser("~/.claude/dashboard")
PAPIER = os.path.expanduser("~/.claude/projects/_papierkorb")
OUT    = os.path.join(DATEN, "index.html")
CACHE  = os.path.join(DATEN, "cache.json")
CACHE_V = 3
STATE  = os.path.join(DATEN, "state.json")
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
TOKEN  = secrets.token_urlsafe(32)
SPERRE = threading.RLock()
NUR_LESEN = False


def warn(text):
    """Fehler sichtbar machen statt verschlucken."""
    print("WARN %s" % text, file=sys.stderr, flush=True)
HOOK   = os.path.expanduser("~/.claude/hooks/stop-notify.py")

# Anrede: ueber die Umgebungsvariable CLAUDE_DASH_NAME setzbar
NAME  = os.environ.get("CLAUDE_DASH_NAME") or os.environ.get("USER") or "du"
DEMO  = False

HEAD_BYTES = 262144
TAIL_BYTES = 1048576

# ----------------------------------------------------------------------------
# PREISE: USD je 1 Mio. Token. Listenpreise, hier anpassen wenn sie sich aendern.
# Bei einem Abo rechnet Anthropic anders ab, diese Zahlen sind der Gegenwert
# der verbrauchten Token, nicht zwingend deine Rechnung.
# ----------------------------------------------------------------------------
PREISE = {
    "opus":   {"in": 15.00, "out": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
    "sonnet": {"in":  3.00, "out": 15.00, "cache_read": 0.30,  "cache_write":  3.75},
    "haiku":  {"in":  0.80, "out":  4.00, "cache_read": 0.08,  "cache_write":  1.00},
    "fable":  {"in":  3.00, "out": 15.00, "cache_read": 0.30,  "cache_write":  3.75},
}
STANDARD = PREISE["sonnet"]


def preis_fuer(modell):
    m = (modell or "").lower()
    for name, p in PREISE.items():
        if name in m:
            return p
    return STANDARD


# ---------------------------------------------------------------- zustand
def _lade(pfad, standard):
    try:
        with open(pfad, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return standard


def _sichere(pfad, daten):
    try:
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(daten, fh)
        os.replace(tmp, pfad)
    except Exception as ex:
        warn("Zustand konnte nicht geschrieben werden (%s): %s" % (pfad, ex))


def zustand_laden():
    z = _lade(STATE, {})
    z.setdefault("erledigt", [])
    z.setdefault("eskaliert", {})
    z.setdefault("budget_tag", 0.0)
    z.setdefault("budget_woche", 0.0)
    return z


def budget_setzen(tag, woche):
  with SPERRE:
    z = zustand_laden()
    try:
        z["budget_tag"] = max(0.0, float(tag))
        z["budget_woche"] = max(0.0, float(woche))
    except (TypeError, ValueError):
        return {"ok": False, "fehler": "keine Zahl"}
    _sichere(STATE, z)
    return {"ok": True, "tag": z["budget_tag"], "woche": z["budget_woche"]}


# ---------------------------------------------------------------- parsing
def _loads(line):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _text(msg):
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    out = []
    if isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                out.append(p.get("text", ""))
    return "".join(out)


def _clean(s, limit=260):
    s = re.sub(r"```.*?```", " ", s or "", flags=re.S)
    s = re.sub(r"<[^>]{1,80}>", " ", s)
    s = re.sub(r"[#*_`>]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


# ---------------------------------------------------------------- verbrauch
def _ortstag(ts):
    """ISO-Zeitstempel in UTC auf das lokale Datum umrechnen."""
    if not ts or len(ts) < 10:
        return None
    try:
        d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.astimezone().date().isoformat()
    except Exception:
        return ts[:10]


def verbrauch(path, cache):
    """Token je Modell, fortlaufend gezaehlt. Nur neue Bytes werden gelesen."""
    size = os.path.getsize(path)
    alt = cache.get(path)
    if (alt and alt.get("v") == CACHE_V and alt.get("size", 0) <= size
            and alt.get("offset", 0) <= size):
        start = alt["offset"]
        summe = {k: dict(v) for k, v in alt["modelle"].items()}
        eingaben = alt.get("eingaben", 0)
        taeglich = dict(alt.get("taeglich") or {})
    else:
        start, summe, eingaben, taeglich = 0, {}, 0, {}

    if start >= size:
        cache[path] = {"v": CACHE_V, "size": size, "offset": start, "modelle": summe,
                       "eingaben": eingaben, "taeglich": taeglich}
        return summe, eingaben, taeglich

    with open(path, "rb") as fh:
        fh.seek(start)
        roh = fh.read()

    letzter_umbruch = roh.rfind(b"\n")
    if letzter_umbruch == -1:
        cache[path] = {"v": CACHE_V, "size": size, "offset": start, "modelle": summe,
                       "eingaben": eingaben, "taeglich": taeglich}
        return summe, eingaben, taeglich
    verwertbar = roh[:letzter_umbruch + 1]
    neuer_offset = start + letzter_umbruch + 1

    for zeile in verwertbar.split(b"\n"):
        if b'"promptSource":"typed"' in zeile:
            eingaben += 1
        if b'"usage"' not in zeile:
            continue
        o = _loads(zeile.decode("utf-8", "ignore"))
        if not o:
            continue
        msg = o.get("message") or {}
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        modell = msg.get("model") or "unbekannt"
        z = summe.setdefault(modell, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
        ein = u.get("input_tokens") or 0
        aus = u.get("output_tokens") or 0
        cr = u.get("cache_read_input_tokens") or 0
        cw = u.get("cache_creation_input_tokens") or 0
        z["in"] += ein; z["out"] += aus; z["cache_read"] += cr; z["cache_write"] += cw

        tag = _ortstag(o.get("timestamp"))
        if tag:
            e = taeglich.setdefault(tag, {"token": 0, "kosten": 0.0, "antworten": 0})
            e["token"] += ein + aus + cr + cw
            pr = preis_fuer(modell)
            e["kosten"] += (ein/1e6)*pr["in"] + (aus/1e6)*pr["out"] \
                         + (cr/1e6)*pr["cache_read"] + (cw/1e6)*pr["cache_write"]
            e["antworten"] += 1

    cache[path] = {"v": CACHE_V, "size": size, "offset": neuer_offset, "modelle": summe,
                   "eingaben": eingaben, "taeglich": taeglich}
    return summe, eingaben, taeglich


def kosten(modelle):
    gesamt = 0.0
    for modell, z in (modelle or {}).items():
        p = preis_fuer(modell)
        gesamt += (z["in"] / 1e6) * p["in"]
        gesamt += (z["out"] / 1e6) * p["out"]
        gesamt += (z["cache_read"] / 1e6) * p["cache_read"]
        gesamt += (z["cache_write"] / 1e6) * p["cache_write"]
    return gesamt


def token_summe(modelle):
    return sum(z["in"] + z["out"] + z["cache_read"] + z["cache_write"]
               for z in (modelle or {}).values())


# ---------------------------------------------------------------- sitzung
def read_session(path, cache):
    size = os.path.getsize(path)
    info = {"file": path, "size": size, "id": os.path.basename(path)[:-6],
            "cwd": None, "branch": None, "title": None,
            "first_prompt": None, "first_ts": None,
            "last_ts": None, "last_user": None, "last_asst": None,
            "last_role": None, "version": None}

    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        head = fh.read(HEAD_BYTES)
    for line in head.split("\n"):
        o = _loads(line)
        if not o:
            continue
        for k, f in (("cwd", "cwd"), ("gitBranch", "branch"),
                     ("aiTitle", "title"), ("version", "version")):
            if o.get(k) and not info[f]:
                info[f] = o[k]
        if o.get("sessionId"):
            info["id"] = o["sessionId"]
        if not info["first_ts"] and o.get("timestamp"):
            info["first_ts"] = o["timestamp"]
        if not info["first_prompt"] and o.get("promptSource") == "typed":
            t = _text(o.get("message") or {})
            if t:
                info["first_prompt"] = _clean(t, 130)

    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(-TAIL_BYTES, os.SEEK_END)
            raw = fh.read()
            raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""
        else:
            raw = fh.read()
    for line in raw.decode("utf-8", "ignore").split("\n"):
        o = _loads(line)
        if not o:
            continue
        if o.get("aiTitle"):
            info["title"] = o["aiTitle"]
        if o.get("timestamp"):
            info["last_ts"] = o["timestamp"]
        msg = o.get("message") or {}
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        t = _text(msg)
        if not t.strip():
            continue
        if role == "user":
            if o.get("isMeta") or t.startswith("<"):
                continue
            info["last_user"] = _clean(t)
            info["last_role"] = "user"
        else:
            info["last_asst"] = _clean(t)
            info["last_role"] = "assistant"

    info["mtime"] = os.path.getmtime(path)
    modelle, eingaben, taeglich = verbrauch(path, cache)
    info["modelle"] = modelle
    info["prompts"] = eingaben
    info["kosten"] = kosten(modelle)
    info["token"] = token_summe(modelle)
    info["taeglich"] = taeglich
    try:
        a = datetime.datetime.fromisoformat((info["first_ts"] or "").replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat((info["last_ts"] or "").replace("Z", "+00:00"))
        info["dauer"] = max(0.0, (b - a).total_seconds())
    except Exception:
        info["dauer"] = 0.0
    return info


def rel(ts):
    d = datetime.datetime.now().timestamp() - ts
    if d < 90:      return "gerade eben"
    if d < 3600:    return "vor %d Min" % (d // 60)
    if d < 7200:    return "vor 1 Std"
    if d < 86400:   return "vor %d Std" % (d // 3600)
    if d < 172800:  return "gestern"
    return "vor %d Tagen" % (d // 86400)


def kurz(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p and p.startswith(home) else (p or "unbekannt")


def status(inf):
    alt = datetime.datetime.now().timestamp() - inf["mtime"]
    if alt < 120:
        return "live", "LAEUFT GERADE", 2
    if alt > 604800:
        return "kalt", "KALT", 3
    if inf["last_role"] == "user":
        return "tot", "ABGEBROCHEN", 0
    if inf["last_role"] == "assistant":
        return "warte", "WARTET AUF ANTWORT", 1
    return "kalt", "OHNE VERLAUF", 3


DEMO_TITEL = [
    "Landingpage Relaunch", "Rechnungsmodul Fehlersuche", "API-Anbindung Zahlungen",
    "Datenbank-Migration", "Bildoptimierung Startseite", "Mobile Navigation",
    "Suchfunktion einbauen", "Formularvalidierung", "Cache-Strategie",
    "Rollen und Rechte", "Export als PDF", "Testabdeckung erhoehen",
    "Deploy-Pipeline", "Fehlerprotokoll auswerten",
]
DEMO_PROJEKT = ["webshop", "portal", "api-service", "landing", "intern-tools"]
DEMO_TEXT = [
    "Der Aufbau steht. Ich habe die Struktur angelegt und die erste Version gebaut.",
    "Fehler gefunden: Der Wert wurde zweimal gesetzt. Behoben und geprueft.",
    "Zwei Wege moeglich. Ich empfehle den ersten, weil er ohne Umbau auskommt.",
    "Fertig und getestet. Die Aenderung liegt in einer Datei.",
]


def _anonym(s, i):
    s["title"] = DEMO_TITEL[i % len(DEMO_TITEL)]
    s["first_prompt"] = "Bitte einmal ansehen und umsetzen"
    s["last_asst"] = DEMO_TEXT[i % len(DEMO_TEXT)]
    s["last_user"] = "ok mach weiter"
    s["cwd"] = "/Users/demo/projekte/" + DEMO_PROJEKT[i % len(DEMO_PROJEKT)]
    return s


def collect(mit_erledigt=False):
    cache = _lade(CACHE, {})
    zustand = zustand_laden()
    erledigt = set(zustand["erledigt"])
    sessions = []
    if os.path.isdir(ROOT):
        for d in os.listdir(ROOT):
            pd = os.path.join(ROOT, d)
            if not os.path.isdir(pd) or d.startswith("_"):
                continue
            for f in os.listdir(pd):
                if not f.endswith(".jsonl"):
                    continue
                p = os.path.join(pd, f)
                try:
                    if os.path.getsize(p) < 400:
                        continue
                    s = read_session(p, cache)
                    s["erledigt"] = s["id"] in erledigt
                    if s["erledigt"] and not mit_erledigt:
                        continue
                    sessions.append(s)
                except Exception as e:
                    print("uebersprungen: %s (%s)" % (f, e), file=sys.stderr)
    _sichere(CACHE, cache)
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    if DEMO:
        sessions = [_anonym(x, i) for i, x in enumerate(sessions)]
    return sessions


def tagesreihe(sessions, tage=14):
    """[(datum, token, kosten)] der letzten Tage, aus allen Sitzungen zusammengefasst."""
    zus = {}
    for s in sessions:
        for tag, e in (s.get("taeglich") or {}).items():
            a = zus.setdefault(tag, {"token": 0, "kosten": 0.0})
            a["token"] += e["token"]
            a["kosten"] += e["kosten"]
    heute = datetime.date.today()
    reihe = []
    for i in range(tage - 1, -1, -1):
        d = (heute - datetime.timedelta(days=i)).isoformat()
        e = zus.get(d, {"token": 0, "kosten": 0.0})
        reihe.append((d, e["token"], e["kosten"]))
    return reihe


def dauer_text(sek):
    if sek <= 0:
        return "-"
    st = int(sek // 3600)
    mi = int((sek % 3600) // 60)
    return ("%dh %02dm" % (st, mi)) if st else ("%dm" % mi)


def platz_gesamt():
    n = 0
    for wurzel, _, dateien in os.walk(ROOT):
        for f in dateien:
            if f.endswith(".jsonl"):
                try:
                    n += os.path.getsize(os.path.join(wurzel, f))
                except OSError:
                    pass
    return n


# ---------------------------------------------------------------- terminal
def _osa(script):
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=12)
        return r.stdout.strip(), r.returncode
    except Exception as ex:
        return str(ex), 1


def _norm(t):
    t = (t or "").strip()
    while t and (t[0] in "✳◑◐●○✦•·*" or t[0].isspace()):
        t = t[1:].lstrip()
    return t.lower()


def terminal_tabs():
    """[(fenster_id, tab_nummer, titel)] ueber alle Fenster und Tabs."""
    out, rc = _osa(
        'tell application "Terminal"\n'
        '  set aus to ""\n'
        '  repeat with w in windows\n'
        '    set i to 0\n'
        '    repeat with tb in tabs of w\n'
        '      set i to i + 1\n'
        '      try\n'
        '        set ti to custom title of tb\n'
        '      on error\n'
        '        set ti to ""\n'
        '      end try\n'
        '      set aus to aus & (id of w) & "|#|" & i & "|#|" & ti & linefeed\n'
        '    end repeat\n'
        '  end repeat\n'
        '  return aus\n'
        'end tell')
    tabs = []
    if rc == 0:
        for zeile in out.split("\n"):
            teile = zeile.split("|#|")
            if len(teile) >= 3 and teile[0].strip().isdigit() and teile[1].strip().isdigit():
                tabs.append((int(teile[0].strip()), int(teile[1].strip()),
                             "|#|".join(teile[2:]).strip()))
    return tabs


def finde_tab(s):
    """Ordnet ueber den Tab-Titel zu. Bei Mehrdeutigkeit lieber nichts als falsch."""
    titel = _norm(s.get("title") or "")
    if not titel:
        return None
    treffer = [(w, t) for w, t, wt in terminal_tabs() if _norm(wt) == titel]
    if len(treffer) != 1:
        if len(treffer) > 1:
            warn("Titel %r passt auf %d Tabs, Zuordnung uneindeutig" % (titel, len(treffer)))
        return None
    return treffer[0]


def tab_mehrdeutig(s):
    titel = _norm(s.get("title") or "")
    if not titel:
        return False
    return sum(1 for _, _, wt in terminal_tabs() if _norm(wt) == titel) > 1


def fortsetzen_befehl(s):
    """Ein einziger Ort fuer den Befehl, damit Anzeige und Ausfuehrung gleich sind."""
    cwd = s.get("cwd") or os.path.expanduser("~")
    return "cd %s && %s -r %s" % (shlex.quote(cwd), shlex.quote(CLAUDE), shlex.quote(s["id"]))


def oeffne_sitzung(s):
    treffer = finde_tab(s)
    if treffer:
        wid, tabnr = treffer
        out, rc = _osa('tell application "Terminal"\n'
                       '  set w to window id %d\n'
                       '  try\n'
                       '    set selected tab of w to tab %d of w\n'
                       '  end try\n'
                       '  set index of w to 1\n'
                       '  activate\n'
                       'end tell' % (wid, tabnr))
        if rc != 0:
            warn("Terminal-Fenster %s liess sich nicht holen: %s" % (wid, out))
            return {"ok": False, "fehler": out[:200] or "AppleScript fehlgeschlagen"}
        return {"ok": True, "modus": "vorhanden", "fenster": wid, "tab": tabnr}

    if tab_mehrdeutig(s):
        return {"ok": False, "fehler": "Mehrere Terminal-Tabs tragen denselben Titel. "
                                       "Bitte den richtigen Tab von Hand waehlen."}

    sicher = fortsetzen_befehl(s).replace("\\", "\\\\").replace('"', '\\"')
    out, rc = _osa('tell application "Terminal"\n  do script "%s"\n  activate\nend tell' % sicher)
    if rc != 0:
        warn("neues Terminal liess sich nicht oeffnen: %s" % out)
        return {"ok": False, "fehler": out[:200] or "AppleScript fehlgeschlagen"}
    return {"ok": True, "modus": "neu"}


def antworte(s, text):
    """Antwort ohne Terminal einspielen. Nur wenn kein Fenster offen ist."""
    if finde_tab(s):
        return {"ok": False, "fehler": "Zu dieser Sitzung ist ein Terminal-Tab offen. "
                                       "Antworte dort, sonst schreiben zwei Stellen gleichzeitig."}
    if tab_mehrdeutig(s):
        return {"ok": False, "fehler": "Mehrere Terminal-Tabs tragen denselben Titel. "
                                       "Die Zuordnung ist nicht eindeutig, deshalb keine Antwort von hier."}
    if not shutil.which(CLAUDE) and not os.path.exists(CLAUDE):
        return {"ok": False, "fehler": "claude nicht gefunden. Setze CLAUDE_BIN auf den vollen Pfad."}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "fehler": "leerer Text"}
    if len(text) > 4000:
        return {"ok": False, "fehler": "Text zu lang (max. 4000 Zeichen)"}
    cwd = s.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    try:
        subprocess.Popen([CLAUDE, "-r", s["id"], "-p", text],
                         cwd=cwd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return {"ok": False, "fehler": "claude nicht im PATH gefunden"}
    except Exception as ex:
        return {"ok": False, "fehler": str(ex)[:160]}
    return {"ok": True, "modus": "gesendet"}


def erledigt_setzen(sid, an=True):
    with SPERRE:
        z = zustand_laden()
        menge = set(z["erledigt"])
        menge.add(sid) if an else menge.discard(sid)
        z["erledigt"] = sorted(menge)
        _sichere(STATE, z)
    return {"ok": True, "erledigt": an}


PAPIER_INDEX = os.path.join(PAPIER, "_index.json")


def in_papierkorb(s):
    """Verschieben statt loeschen, mit Merkzettel fuer die Rueckholung."""
    with SPERRE:
        try:
            os.makedirs(PAPIER, exist_ok=True)
            ziel = os.path.join(PAPIER, os.path.basename(s["file"]))
            if os.path.exists(ziel):
                ziel = ziel[:-6] + "-" + str(int(s["mtime"])) + ".jsonl"
            os.rename(s["file"], ziel)

            index = _lade(PAPIER_INDEX, {})
            index[os.path.basename(ziel)] = {
                "sitzung": s["id"],
                "titel": s.get("title") or "",
                "herkunft": s["file"],
                "verworfen_am": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            _sichere(PAPIER_INDEX, index)

            cache = _lade(CACHE, {})
            cache.pop(s["file"], None)
            _sichere(CACHE, cache)
            return {"ok": True, "ziel": ziel}
        except Exception as ex:
            warn("Papierkorb fehlgeschlagen: %s" % ex)
            return {"ok": False, "fehler": str(ex)[:160]}


def papierkorb_inhalt():
    index = _lade(PAPIER_INDEX, {})
    eintraege = []
    for name, meta in sorted(index.items()):
        pfad = os.path.join(PAPIER, name)
        if os.path.exists(pfad):
            eintraege.append(dict(meta, datei=name))
    return eintraege


def aus_papierkorb(name):
    """Protokoll an seinen urspruenglichen Ort zurueckschieben."""
    with SPERRE:
        try:
            index = _lade(PAPIER_INDEX, {})
            meta = index.get(name)
            if not meta:
                return {"ok": False, "fehler": "kein Eintrag im Papierkorb"}
            quelle = os.path.join(PAPIER, name)
            ziel = meta.get("herkunft") or ""
            if not quelle.startswith(PAPIER) or not os.path.exists(quelle):
                return {"ok": False, "fehler": "Datei fehlt"}
            if not ziel.startswith(ROOT):
                return {"ok": False, "fehler": "Herkunft liegt ausserhalb von ~/.claude/projects"}
            if os.path.exists(ziel):
                return {"ok": False, "fehler": "am Zielort liegt bereits eine Datei"}
            os.makedirs(os.path.dirname(ziel), exist_ok=True)
            os.rename(quelle, ziel)
            index.pop(name, None)
            _sichere(PAPIER_INDEX, index)
            return {"ok": True, "ziel": ziel}
        except Exception as ex:
            warn("Rueckholen fehlgeschlagen: %s" % ex)
            return {"ok": False, "fehler": str(ex)[:160]}


def melde(titel, untertitel, text):
    def esc(x):
        return (x or "").replace("\\", "\\\\").replace('"', '\\"')[:110]
    _osa('display notification "%s" with title "%s" subtitle "%s" sound name "Tink"'
         % (esc(text), esc(titel), esc(untertitel)))


def eskalation_schleife(intervall=300, schwelle=3600):
    """Meldet nochmal, wenn etwas laenger als eine Stunde wartet."""
    while True:
        try:
            time.sleep(intervall)
            z = zustand_laden()
            jetzt = datetime.datetime.now().timestamp()
            geaendert = False
            for s in collect():
                if status(s)[0] != "warte":
                    continue
                wartet = jetzt - s["mtime"]
                if wartet < schwelle or wartet > 86400:
                    continue
                letzte = z["eskaliert"].get(s["id"], 0)
                if jetzt - letzte < schwelle:
                    continue
                melde("%d Std ohne Antwort" % int(wartet // 3600),
                      os.path.basename((s["cwd"] or "?").rstrip("/")),
                      s.get("title") or "Sitzung")
                z["eskaliert"][s["id"]] = jetzt
                geaendert = True
            if geaendert:
                _sichere(STATE, z)
        except Exception as ex:
            warn("Eskalationsschleife: %s" % ex)


# ---------------------------------------------------------------- html
CSS = """
:root{
  color-scheme:dark;
  --bg:#070B14; --flaeche:#0B111F; --karte:#101828; --karte-2:#151E31;
  --line:#1B2438; --line-2:#28344E;
  --fg:#E8ECF6; --fg-2:#9AA5BF; --dim:#6B7591;
  --tot:#FF4D4D; --tot-w:rgba(255,77,77,.13);
  --warte:#FF8C42; --warte-w:rgba(255,140,66,.13);
  --live:#3DDC84; --live-w:rgba(61,220,132,.12);
  --kalt:#4FC3F7; --kalt-w:rgba(79,195,247,.11);
  --blau:#5B8DEF; --tuerkis:#22D3EE;
  --lila:#A855F7;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
*,*::before,*::after{box-sizing:border-box}
html,body{height:100%}
body{margin:0;color:var(--fg);font-family:var(--sans);
  font-size:13px;line-height:1.5;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;
  display:flex;overflow:hidden;
  background:
    radial-gradient(1100px 620px at 12% -12%, rgba(91,141,239,.13), transparent 62%),
    radial-gradient(900px 520px at 88% -6%,  rgba(168,85,247,.11), transparent 60%),
    radial-gradient(1000px 700px at 55% 112%, rgba(34,211,238,.07), transparent 62%),
    var(--bg);
  background-attachment:fixed}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--line-2);border-radius:6px}

/* ---------------- Seitenleiste ---------------- */
.seite{width:212px;flex:none;background:rgba(11,17,31,.72);backdrop-filter:blur(14px);
  border-right:1px solid var(--line);
  display:flex;flex-direction:column;padding:16px 12px}
.logo{display:flex;align-items:center;gap:10px;padding:4px 8px 22px}
.logo .stern{width:26px;height:26px;border-radius:8px;flex:none;
  background:radial-gradient(circle at 32% 30%,#FF8C42,#FF4D4D 55%,#A855F7);
  box-shadow:0 0 16px rgba(255,77,77,.45)}
.logo b{font-size:13.5px;letter-spacing:.09em;font-weight:600}
.logo b span{color:var(--tot)}
.nav{display:flex;flex-direction:column;gap:2px}
.nav a{display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:9px;
  color:var(--fg-2);text-decoration:none;font-size:13px;cursor:pointer}
.nav a .ic{width:16px;text-align:center;opacity:.85;font-size:13px}
.nav a:hover{background:var(--karte);color:var(--fg)}
.nav a.an{background:linear-gradient(90deg,rgba(255,77,77,.14),rgba(255,77,77,0));
  color:var(--fg);box-shadow:inset 2px 0 0 var(--tot)}
.nav .neu{margin-left:auto;font-size:9px;letter-spacing:.1em;color:var(--lila);
  border:1px solid rgba(168,85,247,.4);border-radius:5px;padding:1px 5px}
.modellkarte{margin-top:auto;background:var(--karte);border:1px solid var(--line);
  border-radius:11px;padding:12px 13px}
.modellkarte .t{font-size:12.5px;font-weight:600}
.modellkarte .u{font-size:10.5px;color:var(--dim);margin-bottom:8px}
.modellkarte svg{display:block;width:100%;height:26px;margin-bottom:8px}
.modellkarte .f{display:flex;align-items:center;gap:7px;font-size:10.5px;color:var(--fg-2)}
.modellkarte .f i{width:6px;height:6px;border-radius:50%;background:var(--live);flex:none;
  box-shadow:0 0 7px var(--live)}

/* ---------------- Hauptbereich ---------------- */
.haupt{flex:1;min-width:0;display:flex;flex-direction:column}
.kopf{height:60px;flex:none;display:flex;align-items:center;gap:14px;padding:0 18px;
  border-bottom:1px solid var(--line);background:rgba(11,17,31,.6);backdrop-filter:blur(14px)}
.suche{flex:0 1 420px;display:flex;align-items:center;gap:9px;background:var(--karte);
  border:1px solid var(--line);border-radius:10px;padding:8px 12px}
.suche input{flex:1;background:transparent;border:0;outline:0;color:var(--fg);
  font-family:var(--sans);font-size:12.5px;min-width:0}
.suche input::placeholder{color:var(--dim)}
.suche .kbd{font-family:var(--mono);font-size:9.5px;color:var(--dim);
  border:1px solid var(--line-2);border-radius:5px;padding:2px 5px}
.zustandspille{display:flex;align-items:center;gap:9px;background:var(--karte);
  border:1px solid var(--line);border-radius:10px;padding:7px 13px;margin-left:auto}
.zustandspille i{width:7px;height:7px;border-radius:50%;background:var(--live);flex:none;
  box-shadow:0 0 8px var(--live)}
.zustandspille .a{font-size:12px;font-weight:600}
.zustandspille .b{font-size:10px;color:var(--dim)}
.haupt-knopf{display:flex;align-items:center;gap:8px;border:0;cursor:pointer;
  background:linear-gradient(90deg,#FF5E3A,#FF2D55);color:#fff;font-family:var(--sans);
  font-size:12.5px;font-weight:600;padding:10px 16px;border-radius:10px;
  box-shadow:0 6px 18px -6px rgba(255,45,85,.6)}
.haupt-knopf:hover{filter:brightness(1.08)}
.rundknopf{width:36px;height:36px;border-radius:10px;background:var(--karte);
  border:1px solid var(--line);color:var(--fg-2);cursor:pointer;font-size:14px}
.rundknopf:hover{color:var(--fg);border-color:var(--line-2)}
.avatar{width:36px;height:36px;border-radius:11px;flex:none;display:grid;place-items:center;
  font-size:11.5px;font-weight:700;color:#fff;position:relative;
  background:linear-gradient(140deg,#FF8C42,#A855F7)}
.avatar::after{content:"";position:absolute;right:-2px;bottom:-2px;width:9px;height:9px;
  border-radius:50%;background:var(--live);border:2px solid var(--flaeche)}

.inhalt{flex:1;min-height:0;overflow-y:auto;padding:20px 18px 78px}

/* ---------------- Begruessung + Kacheln ---------------- */
.oben{display:grid;gap:14px;grid-template-columns:1fr;margin-bottom:16px}
@media(min-width:1180px){.oben{grid-template-columns:300px repeat(4,minmax(0,1fr))}}
.gruss h1{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:-.02em}
.gruss p{margin:0;color:var(--dim);font-size:12.5px}
.kachel{background:linear-gradient(160deg,rgba(21,30,49,.92),rgba(16,24,40,.86));
  border:1px solid var(--line);border-radius:13px;
  padding:14px 15px;position:relative;overflow:hidden;min-height:96px}
.kachel .l{font-size:11.5px;color:var(--fg-2)}
.kachel .w{font-size:26px;font-weight:600;letter-spacing:-.02em;margin-top:5px}
.kachel .d{font-size:10.5px;color:var(--dim);margin-top:5px}
.kachel .d b{font-weight:600}
.kachel svg{position:absolute;right:0;bottom:0;width:62%;height:52px;opacity:.95}
.kachel .ecke{position:absolute;right:13px;top:12px;font-size:13px;opacity:.8}

/* ---------------- Verbrauchsband ---------------- */
.band{background:linear-gradient(100deg,rgba(168,85,247,.17),rgba(91,141,239,.10) 42%,rgba(16,24,40,.5));
  border:1px solid var(--line);border-radius:13px;padding:14px 16px;margin-bottom:16px;
  display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:1100px){.band{grid-template-columns:270px repeat(3,minmax(0,1fr))}}
.band .kopfteil{display:flex;align-items:center;gap:13px}
.ring{width:52px;height:52px;flex:none}
.band .kopfteil .t{font-size:13px;font-weight:600}
.band .kopfteil .u{font-size:11px;color:var(--fg-2);margin-top:2px}
.mini{background:var(--karte);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.mini .l{font-size:10.5px;color:var(--dim)}
.mini .w{font-size:14px;font-weight:600;margin-top:3px}
.mini .w small{font-size:10.5px;color:var(--fg-2);font-weight:400}
.mini .r{font-size:10px;color:var(--dim);margin-top:3px}
.mini.budget{display:flex;align-items:center;gap:11px}
.mini.budget .bt{min-width:0}
.budgetlink{font-size:10.5px;color:var(--warte);margin-top:4px;cursor:pointer}
.budgetlink:hover{text-decoration:underline}
.budgetfeld{display:flex;gap:8px;align-items:center;margin:10px 0}
.budgetfeld input{width:110px;background:var(--karte);border:1px solid var(--line-2);
  border-radius:8px;color:var(--fg);font-family:var(--sans);font-size:12px;padding:7px 9px;outline:0}
.budgetfeld input:focus{border-color:var(--warte)}
.budgetfeld label{font-size:12px;color:var(--fg-2);min-width:104px}

/* ---------------- Werkzeugleiste ---------------- */
.leiste{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.gruppe{display:flex;background:var(--karte);border:1px solid var(--line);border-radius:9px;overflow:hidden}
.gruppe button{background:transparent;border:0;color:var(--dim);cursor:pointer;padding:7px 11px;font-size:12px}
.gruppe button.an{background:var(--karte-2);color:var(--fg)}
.wahl{background:var(--karte);border:1px solid var(--line);border-radius:9px;
  padding:7px 12px;font-size:12px;color:var(--fg-2)}
.leiste .rechts{margin-left:auto;display:flex;gap:10px}

/* ---------------- Board ---------------- */
.board{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr));align-items:start}
@media(max-width:1200px){.board{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:700px){.board{grid-template-columns:minmax(0,1fr)}}
.spalte{background:rgba(11,17,31,.66);border:1px solid var(--line);border-radius:14px;
  display:flex;flex-direction:column;overflow:hidden}
.spalte.tot{box-shadow:inset 0 1px 0 rgba(255,77,77,.18)}
.sp-kopf{display:flex;align-items:center;gap:10px;padding:13px 14px;border-bottom:1px solid var(--line)}
.sp-kopf .ic{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;font-size:12px;flex:none}
.spalte.tot   .sp-kopf .ic{background:var(--tot-w);color:var(--tot)}
.spalte.warte .sp-kopf .ic{background:var(--warte-w);color:var(--warte)}
.spalte.live  .sp-kopf .ic{background:var(--live-w);color:var(--live)}
.spalte.kalt  .sp-kopf .ic{background:var(--kalt-w);color:var(--kalt)}
.sp-kopf h2{margin:0;font-size:13px;font-weight:600;flex:1;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.spalte.tot .sp-kopf h2{color:var(--tot)} .spalte.warte .sp-kopf h2{color:var(--warte)}
.spalte.live .sp-kopf h2{color:var(--live)} .spalte.kalt .sp-kopf h2{color:var(--kalt)}
.sp-kopf .anz{font-size:11px;font-weight:600;padding:2px 9px;border-radius:20px;background:var(--karte-2);color:var(--fg-2)}
.sp-kopf .falten{background:transparent;border:1px solid var(--line-2);border-radius:7px;
  color:var(--dim);cursor:pointer;width:24px;height:24px;font-size:10px;flex:none}
.sp-body{padding:11px;display:flex;flex-direction:column;gap:9px;
  max-height:calc(100vh - 430px);min-height:120px;overflow-y:auto}
.spalte.zu .sp-body,.spalte.zu .sp-fuss{display:none}
.sp-fuss{border-top:1px solid var(--line);padding:10px;text-align:center}
.sp-fuss button{background:transparent;border:0;color:var(--dim);cursor:pointer;font-size:12px;
  font-family:var(--sans);padding:4px 8px;border-radius:7px;width:100%}
.spalte.tot .sp-fuss button:hover{color:var(--tot);background:var(--tot-w)}
.spalte.warte .sp-fuss button:hover{color:var(--warte);background:var(--warte-w)}
.spalte.live .sp-fuss button:hover{color:var(--live);background:var(--live-w)}
.spalte.kalt .sp-fuss button:hover{color:var(--kalt);background:var(--kalt-w)}
.leer{color:var(--dim);font-size:11.5px;text-align:center;padding:22px 8px}

/* ---------------- Karte ---------------- */
.karte{background:rgba(16,24,40,.9);border:1px solid var(--line);border-radius:11px;
  padding:11px 12px 9px;display:flex;flex-direction:column;gap:7px;cursor:pointer;position:relative}
.karte:hover{background:var(--karte-2);border-color:var(--line-2)}
.spalte.live .karte{border-color:rgba(61,220,132,.28);
  background:linear-gradient(180deg,rgba(61,220,132,.06),var(--karte))}
.karte.aus{display:none}
.karte.markiert{border-color:var(--warte);box-shadow:0 0 0 1px var(--warte)}
.karte.oeffnet{border-color:var(--live);box-shadow:0 0 0 1px var(--live)}
.karte.fehler{border-color:var(--tot);box-shadow:0 0 0 1px var(--tot)}
.k-id{font-family:var(--mono);font-size:10px;color:var(--dim)}
.k-titel{font-size:13px;line-height:1.35;font-weight:600;margin:0;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.k-txt{font-size:11.5px;line-height:1.5;color:var(--fg-2);margin:0;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.k-txt em{font-style:normal;color:var(--warte);font-size:10px;letter-spacing:.08em}
.balken{height:3px;border-radius:2px;background:var(--line);overflow:hidden}
.balken i{display:block;height:100%;border-radius:2px;background:var(--warte)}
.spalte.tot .balken i{background:var(--tot)}
.k-unten{display:flex;align-items:center;gap:6px 8px;flex-wrap:wrap;padding-top:8px;
  border-top:1px solid var(--line);margin-top:auto;font-size:10.5px;color:var(--dim)}
.k-unten .geld{color:var(--live)}
.marke-tag{font-size:10px;padding:3px 8px;border-radius:6px;background:var(--karte-2);
  color:var(--fg-2);white-space:nowrap;max-width:52%;overflow:hidden;text-overflow:ellipsis}
.spalte.tot .marke-tag{background:var(--tot-w);color:var(--tot)}
.spalte.warte .marke-tag{background:var(--warte-w);color:var(--warte)}
.spalte.live .marke-tag{background:var(--live-w);color:var(--live)}
.spalte.kalt .marke-tag{background:var(--kalt-w);color:var(--kalt)}
.werkzeug{margin-left:auto;display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.wz{font-family:var(--sans);font-size:10.5px;color:var(--fg-2);background:transparent;
  border:1px solid var(--line-2);border-radius:7px;padding:3px 8px;cursor:pointer;white-space:nowrap}
.wz:hover{border-color:var(--warte);color:var(--warte)}
.wz.ok{border-color:var(--live);color:var(--live)}
.antwort{display:none;gap:6px;flex-direction:column}
.karte.offen .antwort{display:flex}
.antwort textarea{width:100%;background:#070910;border:1px solid var(--line-2);border-radius:8px;
  color:var(--fg);font-family:var(--sans);font-size:11.5px;padding:8px 9px;resize:vertical;
  min-height:56px;outline:0}
.antwort textarea:focus{border-color:var(--warte)}
.antwort .hinweis{font-size:10px;color:var(--dim)}

/* ---------------- Schnellaktionen + Dialog ---------------- */
.pille{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);display:flex;
  align-items:center;gap:10px;background:var(--karte);border:1px solid var(--line-2);
  border-radius:12px;padding:9px 15px;cursor:pointer;z-index:20;
  box-shadow:0 12px 34px -14px rgba(0,0,0,.9)}
.pille:hover{border-color:var(--lila)}
.pille .kbd{font-family:var(--mono);font-size:9.5px;color:var(--dim);
  border:1px solid var(--line-2);border-radius:5px;padding:2px 5px}
.fab{position:fixed;right:20px;bottom:18px;width:46px;height:46px;border-radius:15px;border:0;
  cursor:pointer;z-index:20;color:#fff;font-size:17px;
  background:radial-gradient(circle at 35% 30%,#C084FC,#7C3AED);
  box-shadow:0 12px 30px -8px rgba(124,58,237,.75)}
.blende{position:fixed;inset:0;background:rgba(2,3,6,.78);display:none;align-items:center;
  justify-content:center;z-index:60;padding:20px}
.blende.an{display:flex}
.dialog{background:var(--flaeche);border:1px solid var(--line-2);border-radius:16px;
  padding:22px;max-width:620px;width:100%;max-height:86vh;overflow-y:auto}
.dialog h3{margin:0 0 12px;font-size:15px;font-weight:600}
.dialog p{margin:0 0 10px;color:var(--fg-2);font-size:12px;line-height:1.65}
.dialog .reihe{display:flex;justify-content:space-between;gap:14px;padding:8px 0;
  border-bottom:1px solid var(--line);font-size:12px}
.dialog .reihe b{font-weight:600}
.dialog .knoepfe{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
.dialog .knopf{background:var(--karte);border:1px solid var(--line-2);border-radius:9px;
  color:var(--fg-2);padding:8px 14px;cursor:pointer;font-family:var(--sans);font-size:12px}
"""

JS = r"""
var TOKEN = document.body.getAttribute('data-token') || '';
var NUR_LESEN = document.body.getAttribute('data-nurlesen') === '1';

function hole(pfad, cb, koerper){
  if(NUR_LESEN){ alert('Statischer Schnappschuss: Aktionen brauchen den Server.'); cb({ok:false}); return; }
  fetch(pfad, {
    method: 'POST',
    headers: {'X-Dashboard-Token': TOKEN},
    body: koerper === undefined ? null : koerper
  }).then(function(r){return r.json()}).then(cb).catch(function(){cb({ok:false})});
}

// ---- Entwuerfe ueberleben jedes Neuladen ----
function entwurfSchluessel(sid){ return 'entwurf:' + sid; }
function entwurfLesen(sid){ try { return localStorage.getItem(entwurfSchluessel(sid)) || ''; } catch(e){ return ''; } }
function entwurfSchreiben(sid, t){ try { t ? localStorage.setItem(entwurfSchluessel(sid), t) : localStorage.removeItem(entwurfSchluessel(sid)); } catch(e){} }

document.querySelectorAll('.karte').forEach(function(k){
  var sid = k.getAttribute('data-sid');
  var t = k.querySelector('textarea');
  if(!t || !sid) return;
  var alt = entwurfLesen(sid);
  if(alt){ t.value = alt; k.classList.add('offen'); }
  t.addEventListener('input', function(){ entwurfSchreiben(sid, t.value); });
});

// ---- Selbst nachladen, aber nie mitten im Tippen ----
function darfNachladen(){
  var a = document.activeElement;
  if(a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT')) return false;
  if(document.querySelector('.blende.an')) return false;
  var offen = document.querySelectorAll('.karte.offen textarea');
  for(var i = 0; i < offen.length; i++){ if((offen[i].value || '').trim()) return false; }
  var q = document.getElementById('q');
  if(q && q.value.trim()) return false;
  return true;
}
if(!NUR_LESEN){
  setInterval(function(){ if(darfNachladen()) location.reload(); }, 20000);
}
function blitz(k, kl){ k.classList.add(kl); setTimeout(function(){k.classList.remove(kl)}, 1200); }
function dialog(an){ document.getElementById('blende').classList.toggle('an', an); }

document.addEventListener('click', function(e){
  var f = e.target.closest('.falten');
  if(f){ e.stopPropagation(); f.closest('.spalte').classList.toggle('zu'); return; }

  var neu = e.target.closest('[data-neu]');
  if(neu){ e.stopPropagation(); hole('/neu', function(){}); return; }

  var wz = e.target.closest('.wz');
  if(wz){
    e.stopPropagation();
    var karte = wz.closest('.karte'), sid = karte.getAttribute('data-sid');
    var art = wz.getAttribute('data-art');
    if(art === 'kopieren'){
      navigator.clipboard.writeText(karte.getAttribute('data-cmd')).then(function(){
        var a = wz.textContent; wz.textContent = 'kopiert'; wz.classList.add('ok');
        setTimeout(function(){ wz.textContent = a; wz.classList.remove('ok'); }, 1200);
      });
    } else if(art === 'erledigt'){
      hole('/erledigt?id=' + encodeURIComponent(sid), function(d){
        if(d.ok){ karte.style.transition='opacity .25s'; karte.style.opacity=0;
                  setTimeout(function(){ karte.remove(); zaehlen(); }, 250); }
        else blitz(karte,'fehler');
      });
    } else if(art === 'antworten'){
      karte.classList.toggle('offen');
      var t = karte.querySelector('textarea'); if(t && karte.classList.contains('offen')) t.focus();
    } else if(art === 'senden'){
      var t2 = karte.querySelector('textarea'); var txt = (t2.value||'').trim();
      if(!txt){ karte.classList.add('offen'); t2.focus(); return; }
      wz.textContent = 'sendet…';
      hole('/antwort?id=' + encodeURIComponent(sid), function(d){
        wz.textContent = 'senden';
        if(d.ok){ t2.value=''; entwurfSchreiben(sid, ''); karte.classList.remove('offen'); blitz(karte,'oeffnet'); }
        else { blitz(karte,'fehler'); alert(d.fehler || 'fehlgeschlagen'); }
      }, txt);
    } else if(art === 'zurueck'){
      hole('/erledigt?aus=1&id=' + encodeURIComponent(sid), function(d){
        if(d.ok){ karte.style.transition='opacity .25s'; karte.style.opacity=0;
                  setTimeout(function(){ karte.remove(); zaehlen(); }, 250); }
        else blitz(karte,'fehler');
      });
    } else if(art === 'papierkorb'){
      if(!confirm('Protokoll nach ~/.claude/projects/_papierkorb verschieben? Nichts wird geloescht.')) return;
      hole('/papierkorb?id=' + encodeURIComponent(sid), function(d){
        if(d.ok){ karte.remove(); zaehlen(); } else blitz(karte,'fehler');
      });
    }
    return;
  }

  if(e.target.closest('#b_speichern')){
    var bt = document.getElementById('b_tag').value || 0;
    var bw = document.getElementById('b_woche').value || 0;
    hole('/budget?tag=' + encodeURIComponent(bt) + '&woche=' + encodeURIComponent(bw), function(d){
      if(d.ok) location.reload(); else alert(d.fehler || 'fehlgeschlagen');
    });
    return;
  }
  if(e.target.closest('[data-dialog]')){ e.stopPropagation(); dialog(true); return; }
  if(e.target.closest('#neuladen')){ location.reload(); return; }
  if(e.target.closest('#analytics') || e.target.closest('.pille') || e.target.closest('.fab')){ dialog(true); return; }
  if(e.target.closest('#zu') || e.target.id === 'blende'){ dialog(false); return; }
  if(e.target.closest('textarea')) return;

  var k = e.target.closest('.karte');
  if(k){
    var sid2 = k.getAttribute('data-sid'); if(!sid2) return;
    k.classList.add('oeffnet');
    hole('/oeffnen?id=' + encodeURIComponent(sid2), function(d){
      setTimeout(function(){ k.classList.remove('oeffnet'); }, 900);
      if(!d.ok) blitz(k,'fehler');
    });
  }
});

function sichtbar(){ return Array.prototype.filter.call(document.querySelectorAll('.karte'), function(k){ return !k.classList.contains('aus'); }); }
function zaehlen(){
  document.querySelectorAll('.spalte').forEach(function(sp){
    var a = sp.querySelector('.anz');
    if(a) a.textContent = sp.querySelectorAll('.karte:not(.aus)').length;
  });
}
var pos = -1;
function markiere(i){
  var alle = sichtbar(); if(!alle.length) return;
  pos = Math.max(0, Math.min(alle.length - 1, i));
  alle.forEach(function(k){ k.classList.remove('markiert'); });
  alle[pos].classList.add('markiert');
  alle[pos].scrollIntoView({block:'nearest'});
}
var feld = document.getElementById('q');
if(feld){
  feld.addEventListener('input', function(){
    var q = feld.value.toLowerCase().trim();
    document.querySelectorAll('.karte').forEach(function(k){
      k.classList.toggle('aus', !!q && k.getAttribute('data-such').indexOf(q) < 0);
    });
    zaehlen(); pos = -1;
  });
  feld.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){ feld.value=''; feld.blur(); feld.dispatchEvent(new Event('input')); }
  });
}
window.addEventListener('keydown', function(e){
  if((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k'){ e.preventDefault(); feld && feld.focus(); return; }
  if((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j'){ e.preventDefault(); dialog(true); return; }
  var t = e.target.tagName;
  if(t === 'INPUT' || t === 'TEXTAREA') return;
  if(e.key === '/'){ e.preventDefault(); feld && feld.focus(); return; }
  if(e.key === 'j'){ e.preventDefault(); markiere(pos + 1); return; }
  if(e.key === 'k'){ e.preventDefault(); markiere(pos - 1); return; }
  if(e.key === 'Enter'){ var a = sichtbar()[pos]; if(a){ e.preventDefault(); a.click(); } return; }
  if(e.key === 'e'){ var b = sichtbar()[pos]; if(b){ var x = b.querySelector('[data-art=erledigt]'); if(x){ e.preventDefault(); x.click(); } } return; }
  if(e.key === 'r'){ location.reload(); return; }
  if(e.key === 'Escape'){ dialog(false); }
});
"""

SPALTEN = [("tot", "Abgebrochen", "&#9888;"), ("warte", "Wartet auf dich", "&#9203;"),
           ("live", "Läuft gerade", "&#9654;"), ("kalt", "Kalt", "&#10052;")]


def _geld(x):
    return ("%.2f" % x).replace(".", ",") + " $"


def _zahl(n):
    return "{:,}".format(int(n)).replace(",", ".")


def _mb(n):
    return "%.0f MB" % (n / 1048576.0)


_KURVE_NR = 0


def _kurve(werte, farbe, breite=210, hoehe=52, fuellung=True):
    """Verlaufskurve als SVG. Leere Reihe ergibt eine flache Linie."""
    if not werte:
        werte = [0]
    hoch = max(werte) or 1
    n = len(werte)
    schritt = breite / max(1, n - 1)
    punkte = []
    for i, w in enumerate(werte):
        x = i * schritt
        y = hoehe - 6 - (w / hoch) * (hoehe - 14)
        punkte.append("%.1f,%.1f" % (x, y))
    linie = " ".join(punkte)
    global _KURVE_NR
    _KURVE_NR += 1
    ident = "grad%d" % _KURVE_NR
    teile = ['<svg viewBox="0 0 %d %d" preserveAspectRatio="none">' % (breite, hoehe)]
    if fuellung:
        teile.append('<defs><linearGradient id="%s" x1="0" y1="0" x2="0" y2="1">'
                     '<stop offset="0" stop-color="%s" stop-opacity=".38"/>'
                     '<stop offset="1" stop-color="%s" stop-opacity="0"/>'
                     '</linearGradient></defs>' % (ident, farbe, farbe))
        teile.append('<polygon fill="url(#%s)" points="0,%d %s %d,%d"/>'
                     % (ident, hoehe, linie, breite, hoehe))
    teile.append('<polyline fill="none" stroke="%s" stroke-width="1.6" '
                 'stroke-linejoin="round" points="%s"/>' % (farbe, linie))
    teile.append('</svg>')
    return "".join(teile)


def _budgetfarbe(anteil):
    if anteil >= 1.0:
        return "#FF4D4D"
    if anteil >= .85:
        return "#FF8C42"
    return "#3DDC84"


def _ring(anteil, farbe, groesse=52):
    r = groesse / 2 - 4
    u = 2 * 3.14159 * r
    return ('<svg class="ring" viewBox="0 0 %d %d">'
            '<circle cx="%d" cy="%d" r="%.1f" fill="none" stroke="#1A1E2B" stroke-width="5"/>'
            '<circle cx="%d" cy="%d" r="%.1f" fill="none" stroke="%s" stroke-width="5" '
            'stroke-linecap="round" stroke-dasharray="%.1f %.1f" '
            'transform="rotate(-90 %d %d)"/>'
            '<text x="%d" y="%d" text-anchor="middle" dominant-baseline="central" '
            'fill="#E7EAF2" font-size="12" font-family="-apple-system,sans-serif" '
            'font-weight="600">%d%%</text></svg>'
            % (groesse, groesse, groesse//2, groesse//2, r, groesse//2, groesse//2, r,
               farbe, u * min(1.0, anteil), u, groesse//2, groesse//2,
               groesse//2, groesse//2, int(anteil * 100)))


def build(sessions, platz=0, archiv=False):
    e = html.escape
    jetzt = datetime.datetime.now()
    uhr = jetzt.strftime("%H:%M")
    stunde = jetzt.hour
    tagesgruss = "Guten Morgen" if stunde < 11 else ("Guten Tag" if stunde < 18 else "Guten Abend")

    for s in sessions:
        s["zustand"] = status(s)
    sessions.sort(key=lambda s: -s["mtime"])

    reihe = tagesreihe(sessions, 14)
    tok_reihe = [r[1] for r in reihe]
    geld_reihe = [r[2] for r in reihe]
    heute_key = datetime.date.today().isoformat()
    heute_tok = next((r[1] for r in reihe if r[0] == heute_key), 0)
    heute_geld = next((r[2] for r in reihe if r[0] == heute_key), 0.0)
    woche = sum(tok_reihe[-7:])
    vorwoche = sum(tok_reihe[:7])
    delta = ((woche - vorwoche) / vorwoche * 100.0) if vorwoche else None

    n_warte = sum(1 for s in sessions if s["zustand"][0] == "warte")
    n_live = sum(1 for s in sessions if s["zustand"][0] == "live")
    n_tot = sum(1 for s in sessions if s["zustand"][0] == "tot")
    gesamt_kosten = sum(s["kosten"] for s in sessions)
    gesamt_token = sum(s["token"] for s in sessions)
    dauern = [s["dauer"] for s in sessions if s["dauer"] > 0]
    schnitt = sum(dauern) / len(dauern) if dauern else 0

    modelle = {}
    for s in sessions:
        for m, z in (s["modelle"] or {}).items():
            a = modelle.setdefault(m, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
            for kk in a:
                a[kk] += z[kk]
    top_modell = max(modelle, key=lambda m: kosten({m: modelle[m]})) if modelle else "-"
    anteil_heute = (heute_tok / woche) if woche else 0.0

    p = []
    p.append("<!doctype html><html lang='de'><head><meta charset='utf-8'>")
    p.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    p.append("<title>Claude Sessions</title>")
    p.append("<style>%s</style></head>" % CSS)
    p.append("<body data-token='%s' data-nurlesen='%s'>"
             % (e(TOKEN if not NUR_LESEN else ""), "1" if NUR_LESEN else "0"))

    # ---------------- Seitenleiste ----------------
    p.append("<aside class='seite'>")
    p.append("<div class='logo'><span class='stern'></span>"
             "<b>CLAUDE <span>SESSIONS</span></b></div>")
    p.append("<nav class='nav'>")
    for ic, name, aktiv, ziel in [("&#9638;", "Übersicht", not archiv, "/"),
                                  ("&#9635;", "Archiv", archiv, "/?archiv=1"),
                                  ("&#9673;", "Analytics", False, None),
                                  ("&#9881;", "Einstellungen", False, None)]:
        if ziel:
            p.append("<a href='%s' class='%s'><span class='ic'>%s</span>%s</a>"
                     % (ziel, "an" if aktiv else "", ic, e(name)))
        else:
            p.append("<a class='%s' id='analytics'><span class='ic'>%s</span>%s</a>"
                     % ("an" if aktiv else "", ic, e(name)))
    p.append("</nav>")
    p.append("<div class='modellkarte'><div class='t'>%s</div>"
             "<div class='u'>meistgenutztes Modell</div>%s"
             "<div class='f'><i></i>%d Terminals offen</div></div>"
             % (e(top_modell.replace("claude-", "")),
                _kurve(tok_reihe, "#FF8C42", 170, 26),
                len(terminal_tabs())))
    p.append("</aside>")

    # ---------------- Kopf ----------------
    p.append("<div class='haupt'><header class='kopf'>")
    p.append("<div class='suche'><span style='color:var(--dim)'>&#9906;</span>"
             "<input id='q' type='text' placeholder='Sessions durchsuchen…' autocomplete='off'>"
             "<span class='kbd'>&#8984;K</span></div>")
    p.append("<div class='zustandspille'><i></i><div>"
             "<div class='a'>%d warten</div><div class='b'>%d aktiv, %d abgebrochen</div>"
             "</div></div>" % (n_warte, n_live, n_tot))
    p.append("<button class='haupt-knopf' data-neu='1'>+ Neue Session</button>")
    p.append("<button class='rundknopf' id='neuladen' title='Neu laden'>&#8635;</button>")
    p.append("<div class='avatar'>MS</div>")
    p.append("</header><div class='inhalt'>")

    # ---------------- Begruessung + Kacheln ----------------
    p.append("<div class='oben'>")
    if archiv:
        p.append("<div class='gruss'><h1>Archiv</h1>"
                 "<p>%d als erledigt abgelegt. Klick auf zurückholen bringt sie "
                 "in die Übersicht.</p></div>" % len(sessions))
    else:
        p.append("<div class='gruss'><h1>%s, %s.</h1>"
                 "<p>%d Sitzungen warten auf eine Antwort.</p></div>"
                 % (tagesgruss, e(NAME.capitalize()), n_warte))

    def kachel(label, wert, unten, farbe, werte, ecke=""):
        return ("<div class='kachel'><div class='ecke'>%s</div><div class='l'>%s</div>"
                "<div class='w'>%s</div><div class='d'>%s</div>%s</div>"
                % (ecke, e(label), e(wert), unten, _kurve(werte, farbe)))

    p.append(kachel("Sitzungen gesamt", str(len(sessions)),
                    "<b style='color:var(--warte)'>%d</b> warten auf dich" % n_warte,
                    "#FF4D4D", tok_reihe, "&#9636;"))
    p.append(kachel("Token heute", _zahl(heute_tok),
                    ("<b style='color:%s'>%+.0f%%</b> gegenüber Vorwoche" %
                     ("var(--live)" if (delta or 0) >= 0 else "var(--tot)", delta))
                    if delta is not None else "<b>%s</b> in 7 Tagen" % _zahl(woche),
                    "#3DDC84", tok_reihe, "&#9650;"))
    p.append(kachel("Gegenwert heute", _geld(heute_geld),
                    "<b>%s</b> insgesamt" % e(_geld(gesamt_kosten)),
                    "#A855F7", geld_reihe, "&#36;"))
    p.append(kachel("Durchschn. Spanne", dauer_text(schnitt),
                    "über %d Sitzungen" % len(dauern),
                    "#4FC3F7", [s["dauer"] / 3600.0 for s in reversed(sessions)][:14] or [0], "&#9201;"))
    p.append("</div>")

    # ---------------- Verbrauchsband ----------------
    z = zustand_laden()
    b_tag = float(z.get("budget_tag") or 0)
    b_woche = float(z.get("budget_woche") or 0)
    woche_geld = sum(geld_reihe[-7:])

    def budgetteil(label, verbraucht, budget, zeitraum):
        if budget <= 0:
            return ("<div class='mini'><div class='l'>%s</div>"
                    "<div class='w'>%s <small>kein Budget gesetzt</small></div>"
                    "<div class='budgetlink' data-dialog='1'>Budget festlegen &rsaquo;</div></div>"
                    % (e(label), e(_geld(verbraucht))))
        anteil = verbraucht / budget
        rest = budget - verbraucht
        farbe = _budgetfarbe(anteil)
        rest_text = ("noch %s frei" % _geld(rest)) if rest >= 0 else ("%s darüber" % _geld(-rest))
        return ("<div class='mini budget'>%s<div class='bt'>"
                "<div class='l'>%s</div>"
                "<div class='w' style='color:%s'>%s</div>"
                "<div class='r'>von %s &middot; %s</div></div></div>"
                % (_ring(min(1.0, anteil), farbe, 46), e(label), farbe,
                   e(_geld(verbraucht)), e(_geld(budget)), e(rest_text)))

    p.append("<div class='band'>")
    p.append("<div class='kopfteil'>%s<div><div class='t'>Verbrauch</div>"
             "<div class='u'>%s Token heute &middot; %s in 7 Tagen</div></div></div>"
             % (_ring(anteil_heute, "#A855F7"), e(_zahl(heute_tok)), e(_geld(woche_geld))))
    p.append(budgetteil("Heute", heute_geld, b_tag, "tag"))
    p.append(budgetteil("Diese Woche", woche_geld, b_woche, "woche"))
    p.append("<div class='mini'><div class='l'>Gesamt seit Beginn</div>"
             "<div class='w'>%s <small>%s Token</small></div>"
             "<div class='r'>%s auf der Platte</div></div>"
             % (e(_geld(gesamt_kosten)), e(_zahl(gesamt_token)), e(_mb(platz))))
    p.append("</div>")

    # ---------------- Leiste ----------------
    p.append("<div class='leiste'>")
    p.append("<div class='gruppe'><button class='an'>&#9638;</button>"
             "<button>&#9776;</button></div>")
    p.append("<span class='wahl'>Gruppieren: Status</span>")
    p.append("<span class='wahl'>Sortieren: Aktualisiert</span>")
    p.append("<div class='rechts'><span class='wahl'>Stand %s</span></div></div>" % e(uhr))

    # ---------------- Board ----------------
    if NUR_LESEN:
        p.append("<div class='band' style='display:block'>Statischer Schnappschuss. "
                 "Terminal oeffnen, Antworten und Archiv brauchen den Server: "
                 "<b>python3 dash.py --serve</b></div>")
    p.append("<div class='board'>")
    for k, label, ic in SPALTEN:
        gr = [s for s in sessions if s["zustand"][0] == k]
        p.append("<section class='spalte %s'><div class='sp-kopf'>"
                 "<span class='ic'>%s</span><h2>%s</h2>"
                 "<span class='anz'>%d</span>"
                 "<button class='falten' title='Ein- und ausklappen'>&#9650;</button></div>"
                 "<div class='sp-body'>" % (k, ic, e(label), len(gr)))
        if not gr:
            p.append("<div class='leer'>nichts hier</div>")

        for s in gr:
            titel = s["title"] or s["first_prompt"] or "ohne Titel"
            projekt = os.path.basename((s["cwd"] or "?").rstrip("/")) or "?"
            cmd = fortsetzen_befehl(s)
            if s["last_role"] == "user" and s["last_user"]:
                txt = "<em>DU&nbsp;</em>" + e(s["last_user"][:230])
            elif s["last_asst"]:
                txt = e(s["last_asst"][:230])
            elif s["last_user"]:
                txt = "<em>DU&nbsp;</em>" + e(s["last_user"][:230])
            else:
                txt = ""
            such = " ".join([titel, projekt, s["last_user"] or "",
                             s["last_asst"] or "", s["id"]]).lower()

            p.append("<article class='karte' data-sid='%s' data-cmd=\"%s\" data-such=\"%s\">"
                     % (e(s["id"]), e(cmd), e(such[:600])))
            p.append("<div class='k-id'>#%s</div>" % s["id"][:6])
            p.append("<h3 class='k-titel'>%s</h3>" % e(titel[:110]))
            if txt:
                p.append("<p class='k-txt'>%s</p>" % txt)
            if k in ("warte", "tot"):
                anteil = min(1.0, (jetzt.timestamp() - s["mtime"]) / 14400.0)
                p.append("<div class='balken' title='Wartezeit, voll nach 4 Stunden'>"
                         "<i style='width:%d%%'></i></div>" % int(anteil * 100))
            if not NUR_LESEN:
                p.append("<div class='antwort'><textarea placeholder='Antwort an diese Sitzung…'>"
                         "</textarea><div class='hinweis'>Wird ohne Terminal eingespielt. "
                         "Geht nur, wenn kein Tab dazu offen ist.</div></div>")
            p.append("<div class='k-unten'><span>%s</span><span class='geld'>%s</span>"
                     "<span class='marke-tag'>%s</span><div class='werkzeug'>"
                     % (e(rel(s["mtime"])), e(_geld(s["kosten"])), e(projekt)))
            if k in ("tot", "kalt") and not NUR_LESEN:
                p.append("<button class='wz' data-art='antworten'>antw.</button>"
                         "<button class='wz' data-art='senden'>senden</button>")
            p.append("<button class='wz' data-art='kopieren'>kopieren</button>")
            if not NUR_LESEN:
                if archiv:
                    p.append("<button class='wz' data-art='zurueck'>zurückholen</button>")
                else:
                    p.append("<button class='wz' data-art='erledigt'>erledigt</button>")
                if k == "kalt":
                    p.append("<button class='wz' data-art='papierkorb'>&#9003;</button>")
            p.append("</div></div></article>")
        p.append("</div><div class='sp-fuss'><button data-neu='1'>+ Neue Session</button>"
                 "</div></section>")
    p.append("</div>")

    p.append("</div></div>")

    # ---------------- Dialog ----------------
    p.append("<div class='blende' id='blende'><div class='dialog'>")
    p.append("<h3>Zahlen und Tasten</h3>")
    p.append("<p>Alles stammt aus den Protokollen unter ~/.claude/projects. Der Gegenwert ist "
             "aus den verbrauchten Token mit Listenpreisen gerechnet und nicht zwingend deine "
             "Rechnung. Die Preistabelle steht oben in dash.py.</p>")
    p.append("<div class='reihe'><span>Sitzungen sichtbar</span><b>%d</b></div>" % len(sessions))
    p.append("<div class='reihe'><span>Token gesamt</span><b>%s</b></div>" % e(_zahl(gesamt_token)))
    p.append("<div class='reihe'><span>Gegenwert gesamt</span><b>%s</b></div>" % e(_geld(gesamt_kosten)))
    p.append("<div class='reihe'><span>Protokolle auf der Platte</span><b>%s</b></div>" % e(_mb(platz)))
    p.append("<div class='reihe'><span>Durchschn. Spanne erste bis letzte Nachricht</span><b>%s</b></div>" % e(dauer_text(schnitt)))
    for m, z in sorted(modelle.items(), key=lambda x: -kosten({x[0]: x[1]})):
        p.append("<div class='reihe'><span>%s</span><b>%s</b></div>"
                 % (e(m), e(_geld(kosten({m: z})))))
    p.append("<p style='margin-top:14px'><b>&#8984;K</b> suchen &nbsp; <b>&#8984;J</b> dieses Fenster "
             "&nbsp; <b>j</b>/<b>k</b> wandern &nbsp; <b>Enter</b> öffnen &nbsp; "
             "<b>e</b> erledigt &nbsp; <b>r</b> neu laden</p>")
    p.append("<h3 style='margin-top:18px'>Budget</h3>")
    p.append("<p>Anthropic speichert das verbleibende Kontingent nicht auf deinem Rechner, "
             "es kommt beim Aufruf von <b>/usage</b> vom Server. Deshalb rechnet das Dashboard "
             "gegen ein Budget, das du selbst setzt. 0 schaltet die Anzeige ab.</p>")
    p.append("<div class='budgetfeld'><label>Pro Tag</label>"
             "<input id='b_tag' type='number' step='1' min='0' value='%s'><span>$</span></div>"
             % ("%.0f" % b_tag))
    p.append("<div class='budgetfeld'><label>Pro Woche</label>"
             "<input id='b_woche' type='number' step='1' min='0' value='%s'><span>$</span></div>"
             % ("%.0f" % b_woche))
    p.append("<div class='knoepfe'><button class='knopf' id='b_speichern'>Budget sichern</button>"
             "<button class='knopf' id='zu'>Schliessen</button></div>")
    p.append("</div></div>")

    p.append("<div class='pille'><span style='color:var(--lila)'>&#10022;</span>"
             "Schnellaktionen<span class='kbd'>&#8984;J</span></div>")
    p.append("<button class='fab' title='Schnellaktionen'>&#10022;</button>")

    p.append("<script>%s</script></body></html>" % JS)
    return "".join(p)
# ---------------------------------------------------------------- main
def schreiben():
    os.makedirs(DATEN, exist_ok=True)
    doc = build(collect(), platz_gesamt())
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return doc


def serve(port=8787):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    erlaubte_hosts = {"localhost:%d" % port, "127.0.0.1:%d" % port}
    erlaubte_origins = {"http://localhost:%d" % port, "http://127.0.0.1:%d" % port}

    def finde(sid):
        for x in collect(mit_erledigt=True):
            if x["id"] == sid:
                return x
        return None

    class H(BaseHTTPRequestHandler):
        server_version = "claude-dashboard"
        sys_version = ""

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _abgelehnt(self, grund, code=403):
            warn("abgewiesen: %s (%s)" % (grund, self.path))
            return self._json({"ok": False, "fehler": grund}, code)

        def _pruefen(self):
            """Gegen Zugriffe von fremden Seiten und aus anderen Programmen."""
            host = (self.headers.get("Host") or "").strip()
            if host not in erlaubte_hosts:
                return "unerwarteter Host"
            herkunft = self.headers.get("Origin")
            if herkunft and herkunft not in erlaubte_origins:
                return "unerwartete Herkunft"
            if (self.headers.get("X-Dashboard-Token") or "") != TOKEN:
                return "fehlender oder falscher Token"
            return None

        def _sid(self):
            return (parse_qs(urlparse(self.path).query).get("id") or [""])[0]

        def do_POST(self):
            weg = urlparse(self.path).path
            fehler = self._pruefen()
            if fehler:
                return self._abgelehnt(fehler)

            if weg == "/antwort":
                laenge = int(self.headers.get("Content-Length") or 0)
                if laenge > 8192:
                    return self._json({"ok": False, "fehler": "Text zu lang"}, 413)
                text = self.rfile.read(laenge).decode("utf-8", "ignore")
                s = finde(self._sid())
                if not s:
                    return self._json({"ok": False, "fehler": "unbekannte Sitzung"}, 404)
                return self._json(antworte(s, text))

            if weg == "/oeffnen":
                s = finde(self._sid())
                if not s:
                    return self._json({"ok": False, "fehler": "unbekannte Sitzung"}, 404)
                return self._json(oeffne_sitzung(s))

            if weg == "/erledigt":
                q = parse_qs(urlparse(self.path).query)
                sid = (q.get("id") or [""])[0]
                if not sid:
                    return self._json({"ok": False, "fehler": "keine id"}, 400)
                an = (q.get("aus") or ["0"])[0] != "1"
                return self._json(erledigt_setzen(sid, an))

            if weg == "/budget":
                q = parse_qs(urlparse(self.path).query)
                return self._json(budget_setzen((q.get("tag") or ["0"])[0],
                                                (q.get("woche") or ["0"])[0]))

            if weg == "/papierkorb":
                s = finde(self._sid())
                if not s:
                    return self._json({"ok": False, "fehler": "unbekannte Sitzung"}, 404)
                return self._json(in_papierkorb(s))

            if weg == "/zurueckholen":
                name = (parse_qs(urlparse(self.path).query).get("datei") or [""])[0]
                if "/" in name or "\\" in name or not name.endswith(".jsonl"):
                    return self._json({"ok": False, "fehler": "ungueltiger Name"}, 400)
                return self._json(aus_papierkorb(name))

            if weg == "/neu":
                out, rc = _osa('tell application "Terminal"\n'
                               '  do script %s\n  activate\nend tell'
                               % json.dumps(shlex.quote(CLAUDE)))
                if rc != 0:
                    return self._json({"ok": False, "fehler": out[:200]}, 500)
                return self._json({"ok": True})

            return self._json({"ok": False, "fehler": "unbekannt"}, 404)

        def do_GET(self):
            weg = urlparse(self.path).path
            if weg not in ("/", "/index.html"):
                return self._json({"ok": False, "fehler": "unbekannt"}, 404)
            host = (self.headers.get("Host") or "").strip()
            if host not in erlaubte_hosts:
                return self._abgelehnt("unerwarteter Host")

            archiv = (parse_qs(urlparse(self.path).query).get("archiv") or ["0"])[0] == "1"
            if archiv:
                liste = [x for x in collect(mit_erledigt=True) if x.get("erledigt")]
            else:
                liste = collect()
            body = build(liste, platz_gesamt(), archiv).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    threading.Thread(target=eskalation_schleife, daemon=True).start()

    url = "http://localhost:%d/" % port
    print("dashboard laeuft auf %s   (Strg+C beendet)" % url, flush=True)
    try:
        subprocess.run(["open", url], check=False)
    except Exception as ex:
        warn("Browser liess sich nicht oeffnen: %s" % ex)
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--demo" in args:
        DEMO = True
    if "--serve" not in args:
        NUR_LESEN = True
    if "--serve" in args:
        i = args.index("--serve")
        port = int(args[i + 1]) if len(args) > i + 1 and args[i + 1].isdigit() else 8787
        serve(port)
    else:
        schreiben()
        print("geschrieben: %s" % OUT)
        if "--quiet" not in args:
            subprocess.run(["open", "-a", "Google Chrome", OUT], check=False)
