#!/usr/bin/env python3
"""
Stop-Hook: meldet per macOS-Mitteilung, dass eine Sitzung auf eine Antwort wartet.
Bekommt das Hook-Ereignis als JSON auf stdin.
"""
import sys, os, json, re, subprocess

def titel_aus_protokoll(pfad):
    """Letzten von Claude vergebenen Titel aus dem Protokoll holen."""
    try:
        size = os.path.getsize(pfad)
        with open(pfad, "rb") as fh:
            if size > 1048576:
                fh.seek(-1048576, os.SEEK_END)
                raw = fh.read().split(b"\n", 1)[-1]
            else:
                raw = fh.read()
        titel = None
        for line in raw.decode("utf-8", "ignore").split("\n"):
            m = re.search(r'"aiTitle"\s*:\s*"((?:[^"\\]|\\.)*)"', line)
            if m:
                titel = json.loads('"%s"' % m.group(1))
        if titel:
            return titel
        with open(pfad, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh.read(262144).split("\n"):
                m = re.search(r'"aiTitle"\s*:\s*"((?:[^"\\]|\\.)*)"', line)
                if m:
                    return json.loads('"%s"' % m.group(1))
    except Exception:
        pass
    return None


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}

    cwd = ev.get("cwd") or os.getcwd()
    projekt = os.path.basename(cwd.rstrip("/")) or cwd
    sid = (ev.get("session_id") or "")[:8]
    titel = titel_aus_protokoll(ev.get("transcript_path") or "") or "Sitzung"

    # Kein Larm, wenn das Terminal gerade im Vordergrund ist
    try:
        vorne = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        vorne = ""

    if vorne in ("Terminal", "iTerm2", "Ghostty", "WezTerm", "Alacritty", "kitty"):
        return 0

    def esc(s):
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:110]

    script = (
        'display notification "%s" with title "%s wartet auf dich" subtitle "%s" sound name "Tink"'
        % (esc(titel), esc(projekt), esc(sid))
    )
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=6)
    return 0


if __name__ == "__main__":
    sys.exit(main())
