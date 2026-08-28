#!/usr/bin/env python3
"""
Lifecycle-Hook fuer das Claude Session Dashboard.

Liest ein Claude-Code-Hook-Ereignis von stdin und schreibt daraus einen kleinen
Zustandssatz nach ~/.claude/dashboard/lifecycle/<session_id>.json.

Bewusst enthalten ist nur, was fuer die Anzeige eines Zustands noetig ist:
Sitzungskennung, Arbeitsverzeichnis, Zustand, letztes Ereignis, Zeitpunkt und
der Name des laufenden Werkzeugs. Prompttexte, Werkzeugeingaben und Ergebnisse
werden ausdruecklich nicht uebernommen. Das Transkript ist schon die eine
Kopie dieser Daten, eine zweite waere nur ein zusaetzliches Leck.

Der Hook beobachtet, er greift nicht ein: kein Netz, keine Rueckgabe an Claude,
immer Beendigung mit 0, auch im Fehlerfall.
"""
import os, sys, json, tempfile, datetime

BASIS = os.path.expanduser("~/.claude/dashboard/lifecycle")

# Ereignis -> (Zustand, setzt Werkzeug, leert Werkzeug)
# None als Zustand heisst: Zeitstempel auffrischen, Zustand unveraendert lassen.
REGELN = {
    "SessionStart":       ("bereit",   False, True),
    "UserPromptSubmit":   ("arbeitet", False, True),
    "PreToolUse":         ("arbeitet", True,  False),
    "PostToolUse":        ("arbeitet", False, True),
    "PostToolUseFailure": ("arbeitet", False, True),
    "PermissionRequest":  ("freigabe", True,  False),
    "Stop":               ("wartet",   False, True),
    "StopFailure":        ("fehler",   False, True),
    "SessionEnd":         ("beendet",  False, True),
    "SubagentStop":       (None,       False, False),
    "Notification":       (None,       False, False),
    "PreCompact":         (None,       False, False),
}


def jetzt():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sicher_schreiben(pfad, daten):
    """Atomar schreiben, damit ein Leser nie eine halbe Datei sieht."""
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(pfad), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(daten, fh)
        os.replace(tmp, pfad)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(ev, dict):
        return 0

    sid = str(ev.get("session_id") or "").strip()
    name = str(ev.get("hook_event_name") or "").strip()
    if not sid or "/" in sid or "\\" in sid or name not in REGELN:
        return 0

    zustand, setzt_werkzeug, leert_werkzeug = REGELN[name]
    pfad = os.path.join(BASIS, sid + ".json")

    # Mehrere Hook-Prozesse koennen gleichzeitig laufen, etwa PostToolUse des
    # einen und PreToolUse des naechsten Werkzeugs. Deshalb Lesen, Aendern und
    # Schreiben unter einer Dateisperre.
    os.makedirs(BASIS, exist_ok=True)
    sperre = os.path.join(BASIS, sid + ".lock")
    fh = open(sperre, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass

        satz = {}
        if os.path.exists(pfad):
            try:
                with open(pfad, encoding="utf-8") as f:
                    satz = json.load(f) or {}
            except Exception:
                satz = {}

        satz.setdefault("session_id", sid)
        satz.setdefault("started_at", jetzt())
        if ev.get("cwd"):
            satz["cwd"] = ev["cwd"]
        if ev.get("transcript_path"):
            satz["transcript_path"] = ev["transcript_path"]

        satz["last_event"] = name
        satz["last_event_at"] = jetzt()
        if zustand:
            satz["state"] = zustand

        if name == "SessionStart":
            satz["source"] = ev.get("source") or ""
            satz["ended_at"] = None
            satz["end_reason"] = None
        if name == "SessionEnd":
            satz["ended_at"] = jetzt()
            satz["end_reason"] = ev.get("reason") or ""
        if name == "PostToolUseFailure":
            satz["last_tool_ok"] = False
        elif name == "PostToolUse":
            satz["last_tool_ok"] = True

        if setzt_werkzeug:
            werkzeug = ev.get("tool_name")
            satz["current_tool"] = str(werkzeug)[:40] if werkzeug else None
            satz["tool_started_at"] = jetzt()
        elif leert_werkzeug:
            satz["current_tool"] = None
            satz["tool_started_at"] = None

        # Sicherheitsnetz: nichts speichern, was Inhalte tragen koennte.
        for verboten in ("tool_input", "tool_response", "prompt", "message",
                         "content", "command"):
            satz.pop(verboten, None)

        sicher_schreiben(pfad, satz)
    finally:
        try:
            fh.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
