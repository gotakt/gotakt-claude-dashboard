#!/usr/bin/env python3
"""
Traegt die Dashboard-Hooks in ~/.claude/settings.json ein.

Grundsaetze:
  - vorhandene Einstellungen bleiben unangetastet
  - fremde Hooks bleiben erhalten, auch im selben Ereignis
  - ein zweiter Lauf traegt nichts doppelt ein
  - vor jeder Aenderung eine Sicherung
  - ist die Datei kein gueltiges JSON, wird abgebrochen statt geraten
"""
import os, sys, json, shutil, datetime

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOKS    = os.path.dirname(os.path.abspath(__file__))

EREIGNISSE = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
              "PostToolUseFailure", "PermissionRequest", "Stop", "StopFailure",
              "SessionEnd"]


def befehl(python, skript):
    return "%s %s" % (python, os.path.join(HOOKS, skript))


def enthaelt(eintraege, cmd):
    for gruppe in eintraege or []:
        for h in (gruppe or {}).get("hooks", []) or []:
            if (h or {}).get("command") == cmd:
                return True
    return False


def main():
    python = sys.executable or "python3"
    lifecycle = befehl(python, "lifecycle.py")

    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception as ex:
            print("settings.json ist kein gueltiges JSON: %s" % ex, file=sys.stderr)
            print("Nichts geaendert. Bitte erst von Hand pruefen.", file=sys.stderr)
            return 1
        sicherung = SETTINGS + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(SETTINGS, sicherung)
        print("Sicherung: %s" % sicherung)
    else:
        cfg = {}
        print("settings.json wird neu angelegt")

    if not isinstance(cfg, dict):
        print("settings.json enthaelt kein Objekt. Nichts geaendert.", file=sys.stderr)
        return 1

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

    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)

    print("eingetragen: %s" % (", ".join(neu) if neu else "nichts"))
    if schon:
        print("schon vorhanden: %s" % ", ".join(schon))

    fremde = 0
    for ev, liste in hooks.items():
        for gruppe in liste or []:
            for h in (gruppe or {}).get("hooks", []) or []:
                if (h or {}).get("command") and lifecycle not in h["command"]:
                    fremde += 1
    print("unveraendert uebernommene Hooks: %d" % fremde)
    return 0


if __name__ == "__main__":
    sys.exit(main())
