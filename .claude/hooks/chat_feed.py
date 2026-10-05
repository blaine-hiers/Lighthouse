"""Mirror the Claude Code conversation into the current lesson's chat feed.

Wired up in .claude/settings.json for these hook events:
  UserPromptSubmit         -> the learner's message
  PreToolUse  (AskUserQuestion) -> the quiz question and its options
  PostToolUse (AskUserQuestion) -> the learner's answer
  Stop                     -> Claude's reply text

Each event first "flushes" any Claude text from the transcript that isn't in
the feed yet, so the feed keeps conversation order. Entries are appended to
lessons/<slug>/chat.jsonl, where <slug> is read from lessons/.current. The
viewer polls that file. No current lesson means nothing is written.

After the jsonl append, the hook also regenerates lessons/<slug>/chat.md, a
readable Markdown transcript of the whole chat.jsonl (committed with the
lesson). It is rewritten from scratch on every event (atomically, and only if
the content changed), so it is idempotent, backfills older lessons, and heals
if it was edited or deleted. A failure in that step is swallowed and never
stops chat.jsonl from being written.

LESSONS can be pointed elsewhere with the CLASSROOM_LESSONS_DIR env var (tests).

A hook must never get in the way of the session, so every failure exits 0.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LESSONS = Path(os.environ.get("CLASSROOM_LESSONS_DIR") or ROOT / "lessons")


def current_feed():
    pointer = LESSONS / ".current"
    if not pointer.is_file():
        return None, None
    slug = pointer.read_text(encoding="utf-8").strip()
    if not slug or not (LESSONS / slug).is_dir():
        return None, None
    since = datetime.fromtimestamp(pointer.stat().st_mtime, timezone.utc)
    return LESSONS / slug / "chat.jsonl", since.strftime("%Y-%m-%dT%H:%M:%S")


def existing_ids(feed):
    ids = set()
    if feed.is_file():
        for line in feed.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                pass
    return ids


def read_entries(feed):
    """All well-formed entries of chat.jsonl; corrupt lines are skipped."""
    entries = []
    if feed.is_file():
        for line in feed.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def lesson_title(lesson_dir):
    """First "# " line of lesson.md, else the folder name (the slug)."""
    try:
        for line in (lesson_dir / "lesson.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
    except OSError:
        pass
    return lesson_dir.name


def render_quiz_question(q, answer):
    """Markdown lines for one quiz question; `answer` is the learner's text or None."""
    q = q if isinstance(q, dict) else {}
    question = str(q.get("question", ""))
    lines = [f"### Quiz: {q.get('header') or 'Question'}", "", f"**{question}**", ""]
    labels = []
    for opt in q.get("options") or []:
        labels.append(str(opt.get("label", "")) if isinstance(opt, dict) else str(opt))
    picked, other = split_answer(answer, labels) if answer is not None else (set(), [])
    for i, label in enumerate(labels, 1):
        lines.append(f"{i}. {label}{' ✓' if label in picked else ''}")
    if other:
        lines += ["", f"Your answer: {', '.join(other)}"]
    return lines + [""]


def split_answer(answer, labels):
    """(chosen labels, leftover free text) from an answer string.

    Multi-select answers arrive joined with ", ", and labels may contain ", "
    themselves, so match the longest run of parts that forms a label first.
    """
    parts = str(answer).split(", ")
    picked, other, i = set(), [], 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            run = ", ".join(parts[i:j])
            if run in labels:
                picked.add(run)
                i = j
                break
        else:
            other.append(parts[i])
            i += 1
    return picked, other


def render_chat_md(title, entries):
    """The whole transcript as Markdown. An answer applies to the latest quiz before it."""
    answers_for = {}  # index of a quiz entry -> {question text: answer}
    latest_quiz = None
    for i, entry in enumerate(entries):
        if entry.get("kind") == "quiz":
            latest_quiz = i
        elif entry.get("kind") == "answer" and latest_quiz is not None:
            given = entry.get("answers")
            if isinstance(given, dict):
                answers_for.setdefault(latest_quiz, {}).update(given)

    out = [f"# Chat: {title}", ""]
    for i, entry in enumerate(entries):
        kind = entry.get("kind")
        if kind == "you":
            out += ["### You", ""]
            out += ["> " + t if t else ">" for t in str(entry.get("text", "")).splitlines()]
            out.append("")
        elif kind == "claude":
            out += ["### Claude", "", str(entry.get("text", "")), ""]
        elif kind == "quiz":
            questions = entry.get("questions")
            for q in questions if isinstance(questions, list) else []:
                text = q.get("question") if isinstance(q, dict) else None
                out += render_quiz_question(q, answers_for.get(i, {}).get(text))
    return "\n".join(out).rstrip("\n") + "\n"


def write_chat_md(feed):
    """Regenerate chat.md next to `feed` from the full feed. Atomic; skipped if unchanged."""
    lesson_dir = feed.parent
    if not feed.is_file():
        return
    text = render_chat_md(lesson_title(lesson_dir), read_entries(feed))
    target = lesson_dir / "chat.md"
    try:
        if target.read_bytes() == text.encode("utf-8"):
            return
    except OSError:
        pass
    tmp = lesson_dir / f".chat.md.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def claude_texts(transcript_path, since):
    """Yield (id, text) for each Claude text block written after `since`."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            if entry.get("timestamp", "")[:19] < since:
                continue
            content = entry.get("message", {}).get("content", [])
            for i, block in enumerate(content if isinstance(content, list) else []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    yield f"{entry.get('uuid')}:{i}", block["text"].strip()


def main():
    # Claude Code sends UTF-8; on Windows sys.stdin would decode it as cp1252.
    event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    feed, since = current_feed()
    if feed is None:
        return
    name = event.get("hook_event_name")
    if name == "Stop":
        time.sleep(0.4)  # let the final reply land in the transcript

    seen = existing_ids(feed)
    new = []
    for entry_id, text in claude_texts(event.get("transcript_path"), since):
        if entry_id not in seen:
            seen.add(entry_id)
            new.append({"id": entry_id, "kind": "claude", "text": text})

    now = time.time()
    tool_id = event.get("tool_use_id") or f"t{now}"
    if name == "UserPromptSubmit":
        prompt = (event.get("prompt") or "").strip()
        if prompt:
            new.append({"id": f"you:{now}", "kind": "you", "text": prompt})
    elif name == "PreToolUse" and event.get("tool_name") == "AskUserQuestion":
        questions = (event.get("tool_input") or {}).get("questions", [])
        new.append({"id": f"quiz:{tool_id}", "kind": "quiz", "questions": questions})
    elif name == "PostToolUse" and event.get("tool_name") == "AskUserQuestion":
        response = event.get("tool_response") or {}
        answers = response.get("answers") if isinstance(response, dict) else None
        new.append({"id": f"answer:{tool_id}", "kind": "answer", "answers": answers or {}})

    if new:
        with open(feed, "a", encoding="utf-8") as f:
            for item in new:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    try:  # after the jsonl append, so a failure here never loses the feed
        write_chat_md(feed)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
