# Hooks

`./install.sh` registers one script on nine Claude Code events. You can also do it alone:

```bash
python3 hooks/install-hooks.py
```

## What gets registered

| Event | Written state | Also recorded |
|---|---|---|
| `SessionStart` | `bereit` | `source` (startup, resume, clear, compact) |
| `UserPromptSubmit` | `arbeitet` | — |
| `PreToolUse` | `arbeitet` | `current_tool`, `tool_started_at` |
| `PostToolUse` | `arbeitet` | clears the tool, `last_tool_ok: true` |
| `PostToolUseFailure` | `arbeitet` | clears the tool, `last_tool_ok: false` |
| `PermissionRequest` | `freigabe` | the tool asking for approval |
| `Stop` | `wartet` | — |
| `StopFailure` | `fehler` | — |
| `SessionEnd` | `beendet` | `end_reason` |

`SubagentStop`, `Notification` and `PreCompact` only refresh the timestamp. They say
something happened, not what state to move to.

## The state file

One file per session in `~/.claude/dashboard/lifecycle/`:

```json
{
  "session_id": "6f0cf3c9-…",
  "cwd": "/Users/you/projects/shop",
  "state": "arbeitet",
  "last_event": "PreToolUse",
  "last_event_at": "2026-08-28T04:12:03+02:00",
  "current_tool": "Bash",
  "tool_started_at": "2026-08-28T04:12:03+02:00",
  "started_at": "2026-08-28T03:56:11+02:00",
  "ended_at": null,
  "end_reason": null
}
```

That is the whole record. No prompts, no tool inputs, no results, not even the path to the
transcript.

## How the hook behaves

It observes, it never intervenes. No network, no output that Claude Code would act on, and
it always exits 0 — a broken hook must not break your session. Unknown events are ignored.
Writes are atomic, and several hook processes for the same session serialise through
`flock`, because `PostToolUse` of one tool and `PreToolUse` of the next can overlap.

## Installing safely

`install-hooks.py` never overwrites your settings:

- a backup of `settings.json` is written first
- third-party hooks are kept, including inside the same event
- running it twice changes nothing the second time
- invalid JSON aborts the install instead of being guessed at

The notification hook from `hooks/stop-notify.py`, if you use it, keeps working alongside.

## Removing them

Delete the entries whose command points at `hooks/lifecycle.py` from
`~/.claude/settings.json`, or restore one of the `settings.json.bak-*` files the installer
left behind. The dashboard keeps working; every session simply shows **hergeleitet**
instead of **HOOK**.
