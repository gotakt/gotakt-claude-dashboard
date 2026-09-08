#!/usr/bin/env python3
"""
Traegt die Dashboard-Hooks in ~/.claude/settings.json ein — und traegt sie
mit --entfernen wieder aus.

Grundsaetze:
  - vorhandene Einstellungen bleiben unangetastet
  - fremde Hooks bleiben erhalten, auch im selben Ereignis
  - ein zweiter Lauf traegt nichts doppelt ein
  - vor jeder Aenderung eine Sicherung
  - ist die Datei kein gueltiges JSON, wird abgebrochen statt geraten
  - geschrieben wird atomar: entweder die neue Fassung steht vollstaendig da
    oder die alte bleibt unberuehrt. Diese Datei gehoert Claude Code, nicht
    uns — eine halb geschriebene settings.json waere ein fremder Schaden.

Aufruf:
  install-hooks.py              eintragen
  install-hooks.py --entfernen  austragen
"""
import os, sys, json, shutil, shlex, datetime

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOKS    = os.path.dirname(os.path.abspath(__file__))

EREIGNISSE = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
              "PostToolUseFailure", "PermissionRequest", "Stop", "StopFailure",
              "SessionEnd"]

LIFECYCLE = os.path.join(HOOKS, "lifecycle.py")


def befehl(python, skript):
    """Der Befehl landet als Zeichenkette in settings.json und wird von einer
    Shell ausgefuehrt. Leerzeichen oder Apostrophe im Pfad wuerden ihn sonst in
    mehrere Argumente zerlegen, etwa unter /Users/x/My Projects/."""
    return "%s %s" % (shlex.quote(python), shlex.quote(os.path.join(HOOKS, skript)))


def ist_unserer(cmd):
    """Gehoert dieser Hook-Befehl zu diesem Repository?

    Erkannt wird am Skriptpfad, nicht am ganzen Befehl. Der Grund ist
    praktisch: der Interpreter steht mit im Befehl, und er kann sich zwischen
    Einbau und Ausbau geaendert haben — ein Homebrew-Update, ein anderes
    `python3` im PATH, eine virtuelle Umgebung. Ein Vergleich der kompletten
    Zeichenkette wuerde solche Eintraege stehen lassen, und genau das ist der
    Zustand, den dieses Skript beseitigen soll.

    Verglichen wird trotzdem exakt: der Befehl wird wie von einer Shell
    zerlegt, und eines der Argumente muss unser `lifecycle.py` sein — als
    ganzer Pfad, nicht als Teilzeichenkette. Ein fremder Hook kann so nicht
    versehentlich getroffen werden; traefe er zu, riefe er buchstaeblich
    unsere Datei auf und waere damit unserer.
    """
    if not isinstance(cmd, str):
        return False
    try:
        teile = shlex.split(cmd)
    except ValueError:
        # Unbalancierte Anfuehrungszeichen: fremde Schreibweise, nicht unsere.
        return False
    return LIFECYCLE in teile


def enthaelt(eintraege, cmd):
    for gruppe in eintraege or []:
        for h in (gruppe or {}).get("hooks", []) or []:
            if (h or {}).get("command") == cmd:
                return True
    return False


def sichere_schreiben(pfad, cfg):
    """Atomar schreiben: vollstaendig in eine Nachbardatei, dann umbenennen.

    Im selben Verzeichnis, weil `os.replace` nur innerhalb eines Dateisystems
    atomar ist. `fsync` davor, damit nach einem Absturz kein leerer Rumpf
    zurueckbleibt, den das Dateisystem noch nicht ausgeschrieben hatte.
    Schlaegt irgendetwas fehl, wird die Nachbardatei entfernt und die
    vorhandene settings.json ist unveraendert.
    """
    ordner = os.path.dirname(pfad) or "."
    tmp = os.path.join(ordner, ".%s.neu-%d" % (os.path.basename(pfad), os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pfad)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def laden():
    """(cfg, fehlermeldung). Bei Fehler wird nichts geschrieben."""
    if not os.path.exists(SETTINGS):
        return {}, None
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as ex:
        return None, "settings.json ist kein gueltiges JSON: %s" % ex
    if not isinstance(cfg, dict):
        return None, "settings.json enthaelt kein Objekt."
    return cfg, None


def sichern():
    """Zeitgestempelte Sicherung, wie bisher. Nur wenn es etwas zu sichern gibt."""
    if not os.path.exists(SETTINGS):
        return None
    ziel = SETTINGS + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(SETTINGS, ziel)
    return ziel


def eintragen():
    cfg, fehler = laden()
    if fehler:
        print(fehler, file=sys.stderr)
        print("Nichts geaendert. Bitte erst von Hand pruefen.", file=sys.stderr)
        return 1
    if not os.path.exists(SETTINGS):
        print("settings.json wird neu angelegt")

    lifecycle = befehl(sys.executable or "python3", "lifecycle.py")

    hooks = cfg.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print("hooks ist kein Objekt. Nichts geaendert.", file=sys.stderr)
        return 1

    neu, schon = [], []
    for ev in EREIGNISSE:
        liste = hooks.setdefault(ev, [])
        if not isinstance(liste, list):
            print("hooks.%s ist keine Liste, wird uebersprungen" % ev, file=sys.stderr)
            continue
        if enthaelt(liste, lifecycle):
            schon.append(ev)
            continue
        liste.append({"hooks": [{"type": "command", "command": lifecycle, "timeout": 5}]})
        neu.append(ev)

    if not neu:
        # Nichts zu tun. Dann auch nicht schreiben und nicht sichern — ein
        # zweiter Lauf soll die Datei in Ruhe lassen, nicht nur inhaltlich
        # dasselbe hineinschreiben.
        print("eingetragen: nichts")
        print("schon vorhanden: %s" % ", ".join(schon))
        return 0

    sicherung = sichern()
    if sicherung:
        print("Sicherung: %s" % sicherung)
    sichere_schreiben(SETTINGS, cfg)

    print("eingetragen: %s" % ", ".join(neu))
    if schon:
        print("schon vorhanden: %s" % ", ".join(schon))

    fremde = 0
    for ev, liste in hooks.items():
        if not isinstance(liste, list):
            continue
        for gruppe in liste or []:
            for h in (gruppe or {}).get("hooks", []) or []:
                if (h or {}).get("command") and not ist_unserer(h["command"]):
                    fremde += 1
    print("unveraendert uebernommene Hooks: %d" % fremde)
    return 0


def entfernen():
    cfg, fehler = laden()
    if fehler:
        print(fehler, file=sys.stderr)
        print("Nichts geaendert. Bitte erst von Hand pruefen.", file=sys.stderr)
        return 1
    if not os.path.exists(SETTINGS):
        print("settings.json gibt es nicht. Nichts zu entfernen.")
        return 0

    hooks = cfg.get("hooks")
    if hooks is None:
        print("entfernt: nichts")
        return 0
    if not isinstance(hooks, dict):
        # Unbekannte Struktur wird nicht zurechtgebogen.
        print("hooks ist kein Objekt. Nichts geaendert.", file=sys.stderr)
        return 1

    weg, fremde = 0, 0
    leere_ereignisse = []

    for ev in list(hooks.keys()):
        liste = hooks[ev]
        if not isinstance(liste, list):
            print("hooks.%s ist keine Liste, bleibt unberuehrt" % ev, file=sys.stderr)
            continue

        behaltene_gruppen = []
        entfernt_im_ereignis = 0
        for gruppe in liste:
            if not isinstance(gruppe, dict) or not isinstance(gruppe.get("hooks"), list):
                # Nichts, was wir erzeugt haetten. Unangetastet uebernehmen.
                behaltene_gruppen.append(gruppe)
                continue

            behaltene_hooks = []
            entfernt_hier = 0
            for h in gruppe["hooks"]:
                if isinstance(h, dict) and ist_unserer(h.get("command")):
                    entfernt_hier += 1
                    continue
                if isinstance(h, dict) and h.get("command"):
                    fremde += 1
                behaltene_hooks.append(h)

            weg += entfernt_hier
            entfernt_im_ereignis += entfernt_hier
            if entfernt_hier and not behaltene_hooks and len(gruppe) == 1:
                # Diese Gruppe ist durch UNSERE Entfernung leer geworden und
                # trug ausser `hooks` nichts weiter — kein `matcher`, keine
                # unbekannten Felder. Nur dann faellt sie weg; sonst bliebe
                # eine Angabe verloren, die jemand anders gesetzt hat.
                continue
            gruppe["hooks"] = behaltene_hooks
            behaltene_gruppen.append(gruppe)

        hooks[ev] = behaltene_gruppen
        if entfernt_im_ereignis and not behaltene_gruppen:
            # Leer geworden, und zwar durch UNSERE Entfernung. Ein Ereignis,
            # das schon vorher leer dastand, bleibt stehen — es ist nicht
            # unseres, und stillschweigend aufzuraeumen waere eine Aenderung,
            # um die niemand gebeten hat.
            leere_ereignisse.append(ev)

    for ev in leere_ereignisse:
        del hooks[ev]

    if not weg:
        print("entfernt: nichts")
        print("Es standen keine Dashboard-Hooks in settings.json.")
        return 0

    sicherung = sichern()
    if sicherung:
        print("Sicherung: %s" % sicherung)
    sichere_schreiben(SETTINGS, cfg)

    print("entfernt: %d Dashboard-Hook(s)" % weg)
    if leere_ereignisse:
        print("leer gewordene Ereignisse entfernt: %s" % ", ".join(leere_ereignisse))
    print("unveraendert uebernommene Hooks: %d" % fremde)
    return 0


def main():
    args = sys.argv[1:]
    unbekannt = [a for a in args if a != "--entfernen"]
    if unbekannt:
        print("unbekanntes Argument: %s" % " ".join(unbekannt), file=sys.stderr)
        print("Aufruf: install-hooks.py [--entfernen]", file=sys.stderr)
        return 2
    return entfernen() if "--entfernen" in args else eintragen()


if __name__ == "__main__":
    sys.exit(main())
