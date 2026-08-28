# Architecture

The whole product is `dash.py` plus three hook scripts. No framework, no database, no
build step. That is a deliberate constraint: the thing has to survive being ignored for
six months and still start with `python3 dash.py`.

```
~/.claude/projects/*/*.jsonl      transcripts, written by Claude Code
             │
             │  read: head for metadata, tail for the last exchange,
             │        new bytes only for token counting
             ▼
        ┌─────────┐      ~/.claude/dashboard/lifecycle/<id>.json
        │ dash.py │◀──── written by hooks/lifecycle.py
        └─────────┘
             │  ~/.claude/dashboard/cache.json   byte offsets + token totals
             │  ~/.claude/dashboard/state.json   archive, budget, escalations
             ▼
     http://127.0.0.1:8787          one HTML page, rebuilt per request
             │
             ▼
        AppleScript ──▶ Terminal.app        bring a window and tab to the front
        subprocess  ──▶ claude -r -p        answer a session without a terminal
```

## Resolving a state

Two sources, in this order:

**1. The hook.** `hooks/lifecycle.py` writes one small file per session on nine events.
Each state has a shelf life, defined in `HALTBAR`:

| State | Trusted for | Why |
|---|---|---|
| `arbeitet` | 20 min | a single tool call rarely runs longer |
| `freigabe` | 2 h | a permission dialog may sit open, that is the point |
| `wartet` | 7 days | matches the line where a session counts as cold |
| `bereit` | 10 min | started but nothing happened yet, a weak signal |
| `fehler` | 2 h | turn broke, after that rather infer again |
| `beendet` | forever | an end stays an end |

Without a shelf life a single reported `arbeitet` would show as *running* forever after a
`SIGKILL` — exactly the false signal the hooks were meant to remove.

**2. The transcript.** Fresh file plus an assistant message last means *waiting*, your
message last means *aborted*, no change for a week means *cold*. This is what the
dashboard did before hooks existed, and it still carries every session that has no hook
data: old ones, other machines, installations without hooks.

`LEBEN_ZU_SPALTE` maps hook states onto columns. A test asserts that every state in
`HALTBAR` has an entry there, because a state without a column is a dead path that fails
silently — which is exactly the bug that test was written for.

## Reading transcripts cheaply

A transcript grows to tens of megabytes. Reading all of them on every page would be
absurd, so:

- **Head** (256 KB) for session id, working directory, branch, title, first prompt.
- **Tail** (1 MB) for the last exchange and the current title.
- **Token counting** remembers a byte offset per file in `cache.json` and only parses
  what was appended since. Lines without `"usage"` are skipped before any JSON parsing.

First run over ~400 MB takes about half a second. After that it stays well under a
second.

## Matching a session to a terminal

Claude Code writes the session title into the Terminal tab title. The dashboard reads all
window and tab titles through AppleScript, normalises away the status glyph and compares.

It is a good check, not a guarantee. Two tabs can carry the same title, a title can be
changed by hand, and a session may not have one yet. So the matching is **fail-closed**:
exactly one match means act, zero or more than one means refuse and say why. The result is
cached for two seconds, because building one page asks for it several times.

## Concurrency

Three writers can meet: HTTP requests, the escalation thread, and several hook processes.

- State writes go through `os.replace()`, so a reader never sees half a file.
- Read-modify-write sequences hold `SPERRE`, a process-wide `RLock`.
- The escalation loop deliberately does **not** hold the lock while collecting sessions or
  sending a notification. It works out what is due, then takes the lock, reloads the state
  and merges only the `eskaliert` field. Holding it across the slow part would block HTTP;
  writing the whole state afterwards would drop an archive action made in the meantime.
- Hook processes lock per session with `flock`, because `PostToolUse` of one tool and
  `PreToolUse` of the next can overlap.

## Files

| Path | Contents |
|---|---|
| `~/.claude/dashboard/index.html` | the read-only snapshot, kept out of the repo folder |
| `~/.claude/dashboard/cache.json` | byte offsets and token totals per transcript |
| `~/.claude/dashboard/state.json` | archived sessions, budget, escalation timestamps |
| `~/.claude/dashboard/lifecycle/` | one state file per session, written by the hook |
| `~/.claude/projects/_papierkorb/` | trashed transcripts plus `_index.json` for restoring |

Generated output lives under `~/.claude/` on purpose. Next to the repository a synced
folder would happily copy transcript excerpts into iCloud or Dropbox.
