# Classroom channel: answer quizzes in the lesson page

This folder is an optional add-on. It lets you answer Claude's quiz questions by
clicking an option (or typing an answer) in the lesson page, so you can stay on
the page instead of switching to the terminal.

> **Warning: research preview.** This uses Claude Code **channels**, which
> Anthropic marks as a research preview. That means it may change or stop
> working in any Claude Code update. It also needs a launch flag with
> "dangerously" in its name, because only channels on Anthropic's approved
> list can run without it. Everything in Classroom works without it. If anything
> here breaks, launch `claude` the normal way and answer in the terminal.

## What you need

- Claude Code signed in with a **claude.ai account or an Anthropic Console API
  key**. Channels don't work on Amazon Bedrock, Google Cloud or Microsoft
  Foundry. On a claude.ai Team or Enterprise plan, an admin has to turn
  channels on first.
- [Node.js](https://nodejs.org) 20 or newer.

## Set up (once)

From the repo root, in PowerShell:

```powershell
cd channel; npm ci; cd ..
Copy-Item channel/mcp.json .mcp.json
```

`npm ci` installs the exact pinned versions from `package-lock.json` into
`channel/node_modules` (ignored by git). Copying `mcp.json` to `.mcp.json`
tells Claude Code how to start the channel. Keep `.mcp.json` to yourself and
don't commit it: a committed one would make every session ask about the
channel.

## Launch

Every time you want page answers, start Claude Code from the repo root with
exactly this line:

```powershell
$env:CLASSROOM_CHANNEL = "on"; claude --dangerously-load-development-channels server:classroom; Remove-Item Env:CLASSROOM_CHANNEL
```

On macOS or Linux:

```bash
CLASSROOM_CHANNEL=on claude --dangerously-load-development-channels server:classroom
```

Both parts matter. The flag makes Claude Code listen to the channel, and
`CLASSROOM_CHANNEL=on` switches the channel itself on. Without the variable the
channel stays off even if `.mcp.json` is there, so a plain `claude` launch
behaves exactly as before. (A channel that's running but not registered would
otherwise accept your answers and Claude would never get them.)

What you'll see:

1. A full-screen warning that you're loading a development channel. Choose
   **I am using this for local development**.
2. The first time only: "New MCP server found in this project: classroom".
   Choose **Use this MCP server**.
3. A dim line under the banner:
   `Channels (experimental) messages from server:classroom inject directly in this session`.
   If it's missing, the channel isn't live; check `/mcp`.
4. Claude may ask permission the first time it uses the `viewer_url`,
   `ask_quiz` or `got_answer` tool. Approve it.

Then say "teach me ___" as usual. Claude opens the lesson page with a special
link. When a quiz arrives, the options appear as buttons under it, plus a box
for your own answer. Click one, and Claude grades it in chat.

After you click, the page says "Sent to Claude". It changes to "Claude got
your answer" once Claude confirms it. If it never changes, the answer didn't
reach Claude (for example, the launch flag was missing or your organization
has channels turned off). Type your answer in the terminal instead.

If you reload the page, the answer buttons go away, because the page keeps
its key only in memory. Ask Claude for the lesson link again.

To stop, exit Claude Code. The channel exits as soon as Claude Code closes
its connection.

## How it works

`server.js` is an MCP server that Claude Code starts and talks to over stdio.
It declares the `claude/channel` capability, which is what makes it a channel.
Its HTTP side, on `127.0.0.1:8766`, serves the Classroom viewer itself.

- **`viewer_url`** (tool): returns the lesson page link,
  `http://127.0.0.1:8766/viewer/?lesson=<slug>#ch=<key>`. That's the
  channel's own server, not the usual one on 8765. It refuses to give out a
  link if the channel couldn't open its port.
- **`ask_quiz`** (tool): Claude posts one question with 2 to 4 options. The
  channel writes it into the lesson's `chat.jsonl`, the same feed the viewer
  already shows, and remembers it as the one open quiz.
- **The page** (`viewer/features/12-page-answers.js`) asks `/status` on its
  own server whether the channel is running and which quiz is open. Only then
  does it show answer buttons, and only on that quiz.
- **Your answer** goes to `POST /answer`. The channel sends it to Claude as a
  `notifications/claude/channel` event, which Claude sees as
  `<channel source="classroom" quiz_id="…" answer_type="option">…</channel>`,
  and it adds your answer to `chat.jsonl` so the page marks your choice.
- **`got_answer`** (tool): Claude Code never confirms that a channel message
  arrived, so Claude calls this when it gets your answer. That's what turns
  the page's note into "Claude got your answer".

Claude can't answer a pending `AskUserQuestion` from a channel, because that
question waits in the terminal. That's why, in channel mode, Claude posts
quizzes with `ask_quiz`, ends its turn, and grades the answer when it arrives
(see "Answering in the page" in `.claude/skills/teach/SKILL.md`). You can still
type an answer in the terminal instead.

**Ports:** the channel listens on **127.0.0.1:8766** and serves the lesson
page there. The usual server on 8765 keeps working, without answer buttons.
If 8766 is busy, the channel logs `HTTP side not started`, gives out no link,
and Claude quizzes in the terminal. The channel logs its process ID to stderr,
so you can stop a stale copy by its ID (`Stop-Process -Id <pid>`).

## Security

Anything that reaches a channel is put in front of Claude, so the channel only
lets one thing through: your answer to the quiz Claude just asked.

- **The key only goes to a page the channel serves itself.** The channel
  makes a random 256-bit key at launch. The lesson link points at
  `http://127.0.0.1:8766`, a literal address (no `localhost` lookup that
  could land on `::1`) and a port this process holds. If anyone else held
  that port first, the channel couldn't open it and refuses to give out a
  link at all. So another account on your computer can't put up a look-alike
  page that receives the key, which was possible when the key went to the
  8765 viewer.
- **Every answer needs the key.** Requests to `/status` and `/answer` without
  it are refused. The page keeps the key in memory only, never in browser
  storage, and removes it from the address bar.
- **Only its own page.** The `Host` must be exactly `127.0.0.1:8766`, which
  blocks DNS-rebinding tricks. A request from any other origin is refused,
  including `localhost` and `[::1]`, and the channel sends no CORS headers,
  so no other web page can read its replies either.
- **Loopback only.** The HTTP side binds to `127.0.0.1`, so nothing on the
  network can reach it. It serves only what the viewer needs, read-only:
  files under `viewer/`, `lessons/` and `progress/`. It never serves a
  dot-name (`.git`, `.env`, `.mcp.json`, `lessons/.current`) or any path with
  a `~`, which on Windows could be an 8.3 short name for one (`GIT~1`).
- **Small and specific.** Bodies over 4 KB and typed answers over 500
  characters (counted as Unicode code points) are refused. A clicked answer
  must match one of the options exactly. Each quiz takes one answer.
- **Answers are just answers.** Typed text keeps only visible characters:
  letters, accents, numbers, punctuation, symbols and plain spaces. Invisible
  characters are dropped, including Unicode "tag" characters that can hide
  text from you while the model still reads it. (Emoji built with invisible
  joiners lose the joiner.) Text is flattened to one line, and `<` and `>`
  become look-alike quotes so it can't close the `<channel>` tag. The channel
  tells Claude that the message is learner input to be graded, never
  instructions and never permission. It does **not** declare permission
  relay, so nothing from the page can approve or deny a tool.

What's left:

- **Where the key is written down.** The channel itself never saves the key.
  But Claude Code keeps the session transcript in your `~/.claude` folder, and
  it includes the `viewer_url` result and the command that opened the browser,
  both with the key. Only your account can read that folder, and the key stops
  working when you exit Claude Code.
- **Other people on your computer.** An administrator can read anything,
  including the key. On macOS and Linux, other users can see the browser's
  command line, link included, while it opens.
- **The page's own content.** The viewer shows lesson and chat Markdown as
  HTML. If a script ever got into that content, it would run on the channel's
  page and could press the answer buttons. It can't read the key from storage,
  because the key isn't there.

Treat the channel like the terminal: it's for your own computer.

## Tests

```powershell
cd channel; npm test
```

The tests start the channel the way Claude Code does, on a free port, and
check: the key, host and origin rules; port squatting (no link if someone
else holds the port, and other loopback origins refused); the static file
limits; the size limits and code-point count; invisible-character stripping;
the one-answer-per-quiz rule; the relay to Claude and the `got_answer`
confirmation; and that it stays off without `CLASSROOM_CHANNEL=on`. The viewer
side is covered by `python tests/test_viewer.py`.

## References

- [Channels](https://code.claude.com/docs/en/channels) and the
  [channels reference](https://code.claude.com/docs/en/channels-reference)
- The official [fakechat](https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/fakechat)
  example, which this follows
