# Claude Session Dashboard

A local dashboard for your Claude Code sessions. It reads the transcripts that
Claude Code already writes to `~/.claude/projects` and shows, at a glance, which
session is waiting for you, which one crashed mid-turn, and what every session
has cost so far.

Built because I kept running six terminals at once and losing track of where I
had stopped.

![Screenshot](docs/screenshot.png)

*(Screenshot taken in demo mode, so the titles are placeholders.)*

## What it does

Every session lands in one of four states, derived from the transcript:

| State | Meaning |
|---|---|
| **Abgebrochen** | your message was the last one, so something stopped mid-turn |
| **Wartet auf dich** | Claude answered and nobody came back |
| **Läuft gerade** | the transcript changed in the last two minutes |
| **Kalt** | nothing happened for over a week |

Each card shows the session title that Claude Code assigns itself, the last
exchange, how long it has been waiting, and the token cost.

- **Click a card** and the matching Terminal window and tab is brought to the
  front. If no window is open, a new one starts with `claude -r <id>`.
- **Answer without a terminal** on crashed or cold sessions, via `claude -r -p`.
  Refused when the session is open in a terminal, so two writers can never fight
  over the same transcript.
- **Cost and tokens** are summed from the `usage` fields of every message,
  per model, with a configurable price table.
- **Budget** for today and this week, with a ring that turns orange and then red.
- **Archive** for sessions you are done with, and a trash that moves transcripts
  aside instead of deleting them.
- **Keyboard**: `j` `k` to move, `Enter` to open, `e` to archive, `/` or `⌘K` to
  search, `⌘J` for the numbers panel.

Nothing leaves the machine. The server binds to `127.0.0.1` and only reads files
under `~/.claude`.

## Requirements

- macOS (the window handling uses AppleScript and Terminal.app)
- Python 3.9 or newer, no third-party packages
- Claude Code, with at least one session in `~/.claude/projects`

## Run it

```bash
git clone https://github.com/<you>/claude-session-dashboard.git
cd claude-session-dashboard
python3 dash.py --serve
```

Then open <http://localhost:8787>. The page refreshes itself every 20 seconds.

The first request takes a few seconds because every transcript is scanned once
for token counts. After that only new bytes are read, so it stays well under a
second even with several hundred megabytes of history.

Build a static file instead of running a server:

```bash
python3 dash.py          # writes index.html and opens it
python3 dash.py --demo   # same, with all titles and texts replaced
```

Demo mode is there for screenshots and screen recordings. It keeps the real
numbers and replaces every name and message with a placeholder.

## Start it automatically

```bash
cp launchagent/de.gotakt.claude-dashboard.plist ~/Library/LaunchAgents/
# replace __HOME__ and __PYTHON__ inside the copied file first
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/de.gotakt.claude-dashboard.plist
```

`KeepAlive` is on, so the service comes back on its own if it dies. To remove it:

```bash
launchctl bootout gui/$(id -u)/de.gotakt.claude-dashboard
rm ~/Library/LaunchAgents/de.gotakt.claude-dashboard.plist
```

## Get notified instead of checking

A dashboard is passive: you still have to remember to look at it. The included
hook fires a macOS notification whenever a session finishes answering, and stays
quiet while your terminal is in the foreground.

```bash
mkdir -p ~/.claude/hooks
cp hooks/stop-notify.py ~/.claude/hooks/
```

Then add this to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
                     "command": "python3 ~/.claude/hooks/stop-notify.py",
                     "timeout": 10 } ] }
    ]
  }
}
```

## Configuration

Everything sits at the top of `dash.py`.

- `PREISE` — USD per million tokens, per model family. **These are list prices.**
  If you are on a subscription, Anthropic bills differently, so read the figure
  as the value of the tokens you burned, not as your invoice.
- `CLAUDE_DASH_NAME` — environment variable for the name in the greeting.
- Port — `python3 dash.py --serve 9000`.

## Known limits

- macOS and Terminal.app only. iTerm2 and others are not wired up.
- The interface is in German.
- Cloud sessions and Remote Control sessions are not shown. Their transcripts
  live on the server, not on your machine, so there is nothing local to read.
- Your remaining usage quota is not shown either, for the same reason. Claude
  Code fetches it from the server when you run `/usage` and never stores it.
  That is why the budget is one you set yourself.
- If your terminal windows are spread across several Spaces, macOS will switch
  desktops when a session is opened. Merging the windows into tabs fixes it.

## Licence

MIT
