# Classroom

A personal learning environment for Claude Code, built around one way of
learning: **listen and see first, then do it yourself.**

Open Claude Code in this repo and say **"teach me ___"**. Claude will:

1. **Probe**: ask graded quiz questions in chat until it finds where your
   knowledge of the topic runs out, and ask what you want to be able to do.
2. **Plan**: show a dependency map, from facts you can accept as they are up
   to your goal, and wait for your OK.
3. **Teach**: go through the map one idea at a time. For each idea:
   - **Listen and see**: a short section appears in the lesson viewer, read
     aloud, with a diagram.
   - **Do**: a hands-on task with files in `lessons/<lesson>/practice/`.
   - **Check**: a quick graded question in chat.

## The lesson viewer

`viewer/index.html` is a page served locally (`python viewer/serve.py 8765`
from the repo root; Claude starts it for you). It displays
`lessons/<lesson>/lesson.md` with its diagrams, reads it aloud using your
browser's built-in voices, and picks up each new section as Claude writes it.

- **Use Microsoft Edge** for the most natural voices (the ones marked
  "Online (Natural)"). Chrome and Firefox work too, with more robotic voices.
- Click anywhere on the page once. Browsers block speech until you do.
- **Read new sections** reads each new lesson section aloud as soon as it appears. Each
  section also has its own ▶ button for replaying it.
- **Chat panel**: Claude's replies, quiz questions (with the options) and your
  answers appear beside the lesson and are read aloud too. You still type and
  answer in the terminal. This is done by hooks in `.claude/settings.json`,
  which run `.claude/hooks/chat_feed.py` to copy the conversation into
  `lessons/<lesson>/chat.jsonl`.
- Voice, speed and both read-aloud switches are remembered in your browser.
- **Offline voice (Kokoro)** in the header switches to a natural-sounding voice
  model that runs in your browser, with no internet needed after the first
  download. The first time you turn it on it downloads roughly 90 MB (cached
  afterwards). The gear lets you pick the teacher and student voices. If it
  can't load, the page tells you and goes back to the browser voice.
- Open `http://localhost:8765/viewer/` with no lesson selected to see a list of
  all your lessons.

`lessons/welcome/` is a short sample lesson for testing the audio.

## Answering by voice

You can answer Claude by speaking instead of typing, using Claude Code's
built-in voice dictation. Source: the
[official voice dictation docs](https://code.claude.com/docs/en/voice-dictation).

**What it needs:**

- A **claude.ai account** login. It doesn't work with an Anthropic API key,
  Bedrock, Google Cloud or Foundry. If it says
  `Voice mode requires a Claude.ai account`, run `/login`.
- An **internet connection**. Your audio is streamed to Anthropic's servers
  for transcription and isn't processed locally. It doesn't use up Claude
  messages or tokens.
- A **local microphone**. It doesn't work over SSH or in cloud sessions.

**Turn it on (Windows 11):**

1. In Windows Settings, go to **Privacy & security** → **Microphone** and turn
   on microphone access for desktop apps.
2. In Claude Code, run `/voice`. You'll see:
   `Voice mode enabled (hold). Hold space to record. Dictation language: en (/config to change).`
   It stays on across sessions until you run `/voice off`.

**Hold mode (the default):** hold **Space**, speak, and release. There's a
short warm-up before recording starts. Releasing puts the text into the prompt
and waits for you to press **Enter**. To send automatically when you release
(for answers of three words or more), set `"autoSubmit": true` in the `voice`
settings.

**Tap mode:** run `/voice tap`. With the prompt empty, tap **Space** once to
start and again to stop. Answers of three words or more are sent
automatically. Shorter ones are put in the prompt but not sent, so an accidental
tap doesn't send a stray word.

Press **Esc** to cancel a recording. Dictation is tuned for coding vocabulary,
supports 20 languages, and defaults to English unless you set a language in
`/config`.

## Answering in the page (optional)

You can answer quizzes by clicking in the lesson page instead of the terminal.
This uses Claude Code **channels**, which are a **research preview**: they may
change or break in any Claude Code update, and they need a launch flag with
"dangerously" in its name. Classroom works fine without them.

After a one-time setup (see [`channel/README.md`](channel/README.md)), launch
from the repo root in PowerShell with:

```powershell
$env:CLASSROOM_CHANNEL = "on"; claude --dangerously-load-development-channels server:classroom; Remove-Item Env:CLASSROOM_CHANNEL
```

The channel listens on `127.0.0.1:8766` and serves its own copy of the
lesson page there. Only that page gets the key, which changes every launch. `channel/README.md` covers
setup, what you'll see at startup, and the security details.

## Layout

| Path | What it is |
|---|---|
| `CLAUDE.md` | Tells Claude this repo is for learning, how you learn, and how to start a session |
| `.claude/skills/teach/` | The teaching method: probe → plan → teach, and listen and see → do → check |
| `.claude/agents/researcher.md` | A web-research subagent that checks facts before they're taught |
| `viewer/` | The read-aloud lesson viewer |
| `.claude/settings.json`, `.claude/hooks/` | Hooks that copy the chat into the viewer |
| `lessons/<date>-<topic>/` | `lesson.md` (the lesson and your notes), `chat.jsonl` (the conversation) and `practice/` |

## Spaced review

Concepts you've learned come back for review on a schedule, using the FSRS
algorithm. Say "review" or "what's due" to Claude, and the viewer's header
shows how many reviews are due. It needs one package:
`python -m pip install fsrs` (py-fsrs, MIT licence). See `progress/README.md`.

## Credit

The approach comes from Eero Alvar's video
[How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU)
and his [amosblomqvist/learn](https://github.com/amosblomqvist/learn) repo,
which is built for the `pi` agent. This repo rewrites the same ideas for Claude
Code and adds audio-first lessons and hands-on practice.
