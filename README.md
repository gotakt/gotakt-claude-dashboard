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

Every session lands in one of four states. These are **inferred** from the transcript
file: who spoke last, and when the file last changed. A session running a long command
without writing to the transcript can therefore look aborted.

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
  Before starting a second writer the dashboard looks for a Terminal tab whose title
  matches the session, and refuses if it finds one. The match is by tab title, so it
  is a good check, not a guarantee: if two tabs carry the same title the dashboard
  refuses as well rather than guessing.
- **Cost and tokens** are summed from the `usage` fields of every message,
  per model, with a configurable price table.
- **Budget** for today and this week, with a ring that turns orange and then red.
- **Archive** for sessions you are done with, and a trash that moves transcripts
  aside instead of deleting them.
- **Keyboard**: `j` `k` to move, `Enter` to open, `e` to archive, `/` or `⌘K` to
  search, `⌘J` for the numbers panel.

**On data.** The dashboard itself makes no network requests: no fonts, no CDN, no
telemetry. It binds to `127.0.0.1`, reads only files under `~/.claude`, and writes
its own state to `~/.claude/dashboard/`. Actions that invoke Claude Code (opening a
session, sending a reply) run Claude Code normally, and Claude Code talks to
Anthropic as it always does.

**On access.** The action endpoints are POST only and require a per-run token that is
embedded in the page. `Host` and `Origin` are validated. A static snapshot carries no
token and renders read-only.

## Requirements

- macOS (the window handling uses AppleScript and Terminal.app)
- Python 3.9 or newer, no third-party packages
- Claude Code, with at least one session in `~/.claude/projects`

## Run it

```bash
git clone https://github.com/maroxd3/gotakt-claude-dashboard.git
cd gotakt-claude-dashboard
python3 dash.py --serve
```

Then open <http://localhost:8787>. The page refreshes itself every 20 seconds.

The first request takes a few seconds because every transcript is scanned once
for token counts. After that only new bytes are read, so it stays well under a
second even with several hundred megabytes of history.

Build a **read-only snapshot** instead of running a server:

```bash
python3 dash.py          # writes ~/.claude/dashboard/index.html and opens it
python3 dash.py --demo   # same, with all titles and texts replaced
```

The snapshot has no token and no action buttons, because opening a terminal or
sending a reply needs the server. It is written to `~/.claude/dashboard/`, not next
to the repository, so a synced folder never picks up transcript excerpts.

Demo mode is for screenshots and screen recordings. It replaces every name, title
and message with a placeholder **but keeps the real numbers**: session count, tokens,
cost and durations are yours. If that matters for what you are publishing, crop them.

## Start it automatically

```bash
./install.sh          # optional: ./install.sh 9000 for another port
```

The script finds `python3`, `claude` and the repository path itself, writes the
plist, creates `logs/` and starts the service. `KeepAlive` is on, so it comes back
if it dies.

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
