#!/usr/bin/env bash
# Installiert das Dashboard als LaunchAgent. Findet Python, claude und den
# Repo-Pfad selbst und traegt sie in die plist ein.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Werte, die in die plist wandern, muessen XML-sicher sein. Sonst zerlegt ein
# kaufmaennisches Und im Pfad oder Namen die Datei.
xml() {
  local t="$1"
  t="${t//&/&amp;}"; t="${t//</&lt;}"; t="${t//>/&gt;}"
  t="${t//\"/&quot;}"; t="${t//\'/&apos;}"
  printf '%s' "$t"
}
LABEL="de.gotakt.claude-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${1:-8787}"
case "$PORT" in ''|*[!0-9]*) echo "Port muss eine Zahl sein: $PORT"; exit 1;; esac

echo "Repository : $REPO"

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 nicht gefunden."; exit 1; }
echo "python3    : $PY"

CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
  echo "Hinweis    : claude nicht im PATH. Antworten aus dem Dashboard bleiben deaktiviert."
else
  echo "claude     : $CLAUDE"
fi

NAME="${CLAUDE_DASH_NAME:-$(id -F 2>/dev/null | awk '{print $1}')}"
[ -n "$NAME" ] || NAME="$USER"
echo "Anrede     : $NAME"

mkdir -p "$REPO/logs" "$HOME/Library/LaunchAgents"

X_PY="$(xml "$PY")"
X_REPO="$(xml "$REPO")"
X_CLAUDE="$(xml "$CLAUDE")"
X_NAME="$(xml "$NAME")"
X_LABEL="$(xml "$LABEL")"
X_PATH="$(xml "$(dirname "$PY"):${CLAUDE:+$(dirname "$CLAUDE"):}/usr/bin:/bin:/usr/sbin:/sbin")"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$X_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$X_PY</string>
    <string>$X_REPO/dash.py</string>
    <string>--serve</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$X_REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CLAUDE_DASH_NAME</key><string>$X_NAME</string>
    <key>CLAUDE_BIN</key><string>$X_CLAUDE</string>
    <key>PATH</key><string>$X_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$X_REPO/logs/aus.log</string>
  <key>StandardErrorPath</key><string>$X_REPO/logs/fehler.log</string>
</dict>
</plist>
PL

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "Hooks eintragen ..."
"$PY" "$REPO/hooks/install-hooks.py" || echo "Hooks nicht eingetragen, Dashboard laeuft trotzdem."

echo
echo "Installiert. Das Dashboard laeuft auf http://localhost:$PORT"
echo "Protokolle : $REPO/logs/"
echo "Entfernen  : launchctl bootout gui/\$(id -u)/$LABEL"
echo "             rm $PLIST"
echo "             $PY $REPO/hooks/install-hooks.py --entfernen"
echo "             (der dritte Schritt traegt die Hooks aus settings.json aus)"
