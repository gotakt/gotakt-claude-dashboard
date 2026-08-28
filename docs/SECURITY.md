# Security model

## The trust boundary

Everything runs on your machine. The server binds to `127.0.0.1` and is never reachable
from the network. There is no account, no cloud, no telemetry.

**The dashboard itself makes no outbound requests.** No fonts, no CDN, no analytics. A
test builds the page and scans every `src`, `href`, `action` and `url()` in the result,
allowing only localhost. That test replaced a CI check that looked correct and never ran:
`grep` had been given `-E` and `-P` together, which makes GNU grep fail, and the leading
`!` turned that failure into a green step.

Actions that invoke Claude Code — opening a session, sending a reply — run Claude Code
normally. Claude Code then talks to Anthropic exactly as it always does. That is the one
path where data leaves the machine, and it is the same path you use when you type in the
terminal yourself.

## Why the local API is authenticated at all

The dashboard is not read-only. It can open terminals, start sessions, send prompts and
move transcript files. "It is only localhost" is not a boundary for that: any page in your
browser can send requests to `localhost`.

So every mutating endpoint requires all of:

| Check | Effect |
|---|---|
| `POST` only | a page cannot trigger an action with an image or a link |
| `X-Dashboard-Token` | a random 32-byte token, new on every process start, embedded in the page |
| `Host` validation | requests addressed to another name are refused |
| `Origin` validation | a foreign page's request is refused |

The custom header matters more than it looks: it forces any cross-origin request through a
preflight, which a foreign page cannot satisfy without the token.

The page itself sends `Content-Security-Policy` with `default-src 'none'` and
`frame-ancestors 'none'`, plus `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. The token sits in the
page, so a dashboard embedded in a foreign frame would be a way to make you click with a
valid token. That is what `frame-ancestors` closes.

`tests/test_server.py` starts a real server and attacks it: missing token, wrong token,
foreign `Origin`, foreign `Host`, `GET` on an action, oversized payload, unknown session,
path traversal on restore, and every one of those headers.

## What is stored, and what is not

The lifecycle hook writes session id, working directory, state, last event, timestamp and
the **name** of the running tool.

It does not write prompts, tool inputs, tool results or the transcript path. The transcript
is already the one copy of that; a second one would only be another place to leak from. The
hook strips those keys before saving even if a future event carries them, so a later change
cannot quietly reintroduce them. A test feeds the hook a tool input containing a secret and
asserts it does not appear in the file.

The static snapshot does contain transcript excerpts, which is why it is written to
`~/.claude/dashboard/` and not next to the repository, where a synced folder would pick it
up.

## Reply drafts

Unsent replies live in `sessionStorage`, not `localStorage`. They need to survive a reload,
not a week. `localStorage` belongs to the origin `http://localhost:8787`, and any other
local tool that later runs on that port could read what was left there.

## Deliberately fail-closed

Where the dashboard is unsure, it does nothing:

- Two Terminal tabs with the same title: refuses to open or to answer, rather than picking
  one.
- A session that has a matching tab: refuses to answer from the dashboard, so two writers
  never fight over one transcript. The check is by tab title, so it is a good check, not a
  guarantee — the README says so.
- Restoring from the trash: refuses if the target already exists or the recorded origin is
  outside `~/.claude/projects`.
- The trash moves files, it never deletes them.

## What this does not protect against

Anyone with an account on your Mac can read `~/.claude` directly, with or without this
tool. It does not encrypt anything, and it is not meant to.
