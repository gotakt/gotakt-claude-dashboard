#!/usr/bin/env bash
#
# Gegenprobe zum Kernvertrag der Deinstallation:
#
#   Beim Entfernen darf KEIN fremder Hook mitgehen.
#
#   ./tests/sabotage-entfernen.sh
#
# Warum das noetig ist: Ein gruener Test beweist nichts, solange niemand
# gezeigt hat, dass er auch rot werden kann. Ein Test, der versehentlich am
# Gegenstand vorbeilaeuft, bleibt dauerhaft und unauffaellig gruen — und hier
# haengt daran, ob eine Deinstallation jemandem seine eigene Einrichtung
# wegnimmt.
#
# Sabotiert wird die Erkennung: `ist_unserer` sagt danach zu JEDEM Hook ja.
# Damit nimmt das Entfernen den fremden Hook mit, und genau der dafuer
# zustaendige Test muss das melden.
#
# Ein blosser Exit-Code != 0 zaehlt NICHT. Ein Tippfehler, ein fehlendes
# Modul oder ein kaputter Testlauf wuerden ebenfalls scheitern und diese
# Gegenprobe faelschlich bestehen lassen. Verlangt wird, dass genau
# `test_fremder_hook_bleibt_beim_entfernen` rot ist.

set -uo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WURZEL="$(cd "$HIER/.." && pwd)"
cd "$WURZEL"

ZIEL="hooks/install-hooks.py"
SICHERUNG="$(mktemp "${TMPDIR:-/tmp}/install-hooks-original-XXXXXX.py")"
LOG="$(mktemp "${TMPDIR:-/tmp}/sabotage-entfernen-XXXXXX.log")"
cp "$ZIEL" "$SICHERUNG"
zurueck() { cp "$SICHERUNG" "$ZIEL"; rm -f "$SICHERUNG" "$LOG"; }
trap zurueck EXIT

ZU='    return LIFECYCLE in teile'
AUF='    return bool(teile)  # SABOTAGE: haelt jeden Hook fuer unseren'

# Woertlich ersetzen, nicht per Regex — und vorher pruefen, dass es die Zeile
# ueberhaupt noch gibt. Eine Sabotage, die nichts sabotiert, ist schlimmer
# als keine: sie gaebe Entwarnung, ohne etwas geprueft zu haben.
python3 - "$ZIEL" "$ZU" "$AUF" <<'PY'
import sys, pathlib
ziel, zu, auf = sys.argv[1:4]
p = pathlib.Path(ziel)
t = p.read_text()
if zu not in t:
    sys.exit("FEHLER: Die erwartete Zeile steht nicht mehr in %s:\n  %s\n"
             "Wurde ist_unserer geaendert? Dann gehoert dieses Skript angepasst." % (ziel, zu))
p.write_text(t.replace(zu, auf, 1))
PY
[ $? -eq 0 ] || exit 1
grep -qF "SABOTAGE" "$ZIEL" || { echo "FEHLER: sabotierte Fassung nicht erzeugt." >&2; exit 1; }

echo "Sabotage: ist_unserer haelt jeden Hook fuer unseren."
echo "Der Test um den fremden Hook MUSS jetzt rot werden."
echo

python3 -m unittest discover -s tests -v -k fremder_hook_bleibt > "$LOG" 2>&1
ENDE=$?

if [ "$ENDE" -eq 0 ]; then
  echo "GEGENPROBE FEHLGESCHLAGEN: Die Tests blieben gruen, obwohl das" >&2
  echo "Entfernen jeden fremden Hook mitnimmt. Der Kernvertrag ist damit" >&2
  echo "NICHT geprueft." >&2
  tail -20 "$LOG" >&2
  exit 1
fi

if ! grep -q "FAIL: test_fremder_hook_bleibt_beim_entfernen" "$LOG"; then
  echo "GEGENPROBE UNGUELTIG: Der Lauf ist zwar fehlgeschlagen, aber nicht" >&2
  echo "wegen dieses Tests. Der Fehlschlag hat eine andere Ursache." >&2
  echo "Erwartet: FAIL: test_fremder_hook_bleibt_beim_entfernen" >&2
  tail -20 "$LOG" >&2
  exit 1
fi

echo "  ok  rot geworden: test_fremder_hook_bleibt_beim_entfernen"
grep -A3 "fremder Hook wurde mitentfernt" "$LOG" | head -4 | sed 's/^/      /'

# Zurueck auf das Original und nachweisen, dass es wieder gruen ist. Sonst
# koennte die Gegenprobe eine kaputte Fassung hinterlassen.
zurueck
trap - EXIT
LOG2="$(mktemp "${TMPDIR:-/tmp}/sabotage-entfernen-gruen-XXXXXX.log")"
python3 -m unittest discover -s tests > "$LOG2" 2>&1
ENDE2=$?
ANZAHL="$(grep -oE "^Ran [0-9]+ tests" "$LOG2" | grep -oE "[0-9]+")"
rm -f "$LOG2"
if [ "$ENDE2" -ne 0 ]; then
  echo "FEHLER: Nach der Wiederherstellung sind die Tests nicht gruen." >&2
  exit 1
fi

echo
echo "════════════════════════════════════════════"
echo " GEGENPROBE BESTANDEN"
echo " Original wiederhergestellt, $ANZAHL Tests gruen"
echo "════════════════════════════════════════════"
