"""Unit tests for the chat.md transcript in .claude/hooks/chat_feed.py:
  python -m unittest tests/test_chat_md.py

Everything runs in a temp lessons dir (via CLASSROOM_LESSONS_DIR or direct calls),
so the real lessons/.current is never touched.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "chat_feed.py"
spec = importlib.util.spec_from_file_location("chat_feed", HOOK)
chat_feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat_feed)

QUIZ = {"id": "quiz:t1", "kind": "quiz", "questions": [
    {"question": "What does DNS turn a name into?", "header": "Probe 1", "multiSelect": False,
     "options": [{"label": "An IP address"}, {"label": "A MAC address"}]}]}
ENTRIES = [
    {"id": "you:1", "kind": "you", "text": "teach me DNS\nplease"},
    {"id": "a1:0", "kind": "claude", "text": "Let's start.\n\n## A heading\n- point"},
    QUIZ,
    {"id": "answer:t1", "kind": "answer", "answers": {"What does DNS turn a name into?": "An IP address"}},
]


def write_feed(lesson, entries, lesson_md="# DNS basics\n\ntext\n"):
    lesson.mkdir(parents=True, exist_ok=True)
    if lesson_md is not None:
        (lesson / "lesson.md").write_text(lesson_md, encoding="utf-8")
    feed = lesson / "chat.jsonl"
    feed.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    return feed


class ChatMdTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lessons = Path(self._tmp.name)
        self.lesson = self.lessons / "dns"

    def md(self):
        return (self.lesson / "chat.md").read_text(encoding="utf-8")

    def test_renders_you_claude_and_answered_quiz(self):
        chat_feed.write_chat_md(write_feed(self.lesson, ENTRIES))
        md = self.md()
        self.assertTrue(md.startswith("# Chat: DNS basics\n"))
        self.assertIn("### You\n\n> teach me DNS\n> please\n", md)
        self.assertIn("### Claude\n\nLet's start.\n\n## A heading\n- point\n", md)
        self.assertIn("### Quiz: Probe 1\n\n**What does DNS turn a name into?**\n\n"
                      "1. An IP address ✓\n2. A MAC address\n", md)
        self.assertNotIn("Your answer", md)

    def test_unanswered_quiz_has_no_mark(self):
        chat_feed.write_chat_md(write_feed(self.lesson, ENTRIES[:3]))
        self.assertNotIn("✓", self.md())

    def test_multiselect_and_free_text_answers(self):
        q = {"id": "quiz:t2", "kind": "quiz", "questions": [
            {"question": "Pick layers", "header": "Probe 2", "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
            {"question": "Why?", "header": "Probe 3", "options": [{"label": "X"}, {"label": "Y"}]}]}
        ans = {"id": "answer:t2", "kind": "answer", "answers": {"Pick layers": "A, C", "Why?": "because I said so"}}
        # the answer applies to the most recent preceding quiz only
        chat_feed.write_chat_md(write_feed(self.lesson, [QUIZ, q, ans]))
        md = self.md()
        self.assertIn("1. A ✓\n2. B\n3. C ✓\n", md)
        self.assertIn("1. X\n2. Y\n\nYour answer: because I said so\n", md)
        self.assertIn("1. An IP address\n2. A MAC address\n", md)  # first quiz untouched
        self.assertEqual(md.count("✓"), 2)

    def test_option_label_containing_comma_is_marked(self):
        q = {"question": "Sure?", "options": [{"label": "Yes, definitely"}, {"label": "No"}]}
        lines = chat_feed.render_quiz_question(q, "Yes, definitely")
        self.assertIn("1. Yes, definitely ✓", lines)
        self.assertFalse(any(l.startswith("Your answer") for l in lines))

    def test_option_mixed_with_free_text_keeps_both(self):
        q = {"question": "Pick", "options": [{"label": "A"}, {"label": "B, C"}]}
        lines = chat_feed.render_quiz_question(q, "A, B, C, my own, thing")
        self.assertIn("1. A ✓", lines)
        self.assertIn("2. B, C ✓", lines)
        self.assertIn("Your answer: my own, thing", lines)

    def test_title_falls_back_to_slug(self):
        chat_feed.write_chat_md(write_feed(self.lesson, ENTRIES[:1], lesson_md=None))
        self.assertTrue(self.md().startswith("# Chat: dns\n"))

    def test_backfill_and_heal(self):
        feed = write_feed(self.lesson, ENTRIES)  # a jsonl with no chat.md yet
        self.assertFalse((self.lesson / "chat.md").exists())
        chat_feed.write_chat_md(feed)
        good = (self.lesson / "chat.md").read_bytes()
        (self.lesson / "chat.md").write_text("hand edited", encoding="utf-8")
        chat_feed.write_chat_md(feed)
        self.assertEqual((self.lesson / "chat.md").read_bytes(), good)

    def test_unchanged_content_is_not_rewritten(self):
        feed = write_feed(self.lesson, ENTRIES)
        chat_feed.write_chat_md(feed)
        md = self.lesson / "chat.md"
        os.utime(md, (1_000_000, 1_000_000))
        chat_feed.write_chat_md(feed)
        self.assertEqual(md.stat().st_mtime, 1_000_000)

    def test_atomic_write_leaves_no_temp_files(self):
        feed = write_feed(self.lesson, ENTRIES)
        chat_feed.write_chat_md(feed)
        chat_feed.write_chat_md(feed)
        self.assertEqual(sorted(p.name for p in self.lesson.iterdir()), ["chat.jsonl", "chat.md", "lesson.md"])

    def test_failed_replace_cleans_up_temp_and_keeps_old_file(self):
        feed = write_feed(self.lesson, ENTRIES)
        chat_feed.write_chat_md(feed)
        before = (self.lesson / "chat.md").read_bytes()
        write_feed(self.lesson, ENTRIES + [{"id": "a2:0", "kind": "claude", "text": "more"}])
        real = os.replace

        def boom(*args):
            raise OSError("boom")

        chat_feed.os.replace = boom
        try:
            with self.assertRaises(OSError):
                chat_feed.write_chat_md(feed)
        finally:
            chat_feed.os.replace = real
        self.assertEqual((self.lesson / "chat.md").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.lesson.iterdir()), ["chat.jsonl", "chat.md", "lesson.md"])

    def test_corrupt_jsonl_lines_are_skipped(self):
        feed = write_feed(self.lesson, ENTRIES[:2])
        with open(feed, "a", encoding="utf-8") as f:
            f.write("{not json\n[1, 2]\n\n")
            f.write(json.dumps({"id": "a2:0", "kind": "claude", "text": "after the bad line"}) + "\n")
        chat_feed.write_chat_md(feed)
        md = self.md()
        self.assertIn("teach me DNS", md)
        self.assertIn("after the bad line", md)

    def run_hook(self, event):
        env = {**os.environ, "CLASSROOM_LESSONS_DIR": str(self.lessons)}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), text=True,
                              capture_output=True, env=env, encoding="utf-8")

    def test_hook_subprocess_writes_jsonl_then_md_and_backfills(self):
        write_feed(self.lesson, ENTRIES[:2])  # existing history, no chat.md
        (self.lessons / ".current").write_text("dns", encoding="utf-8")
        r = self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "next question"})
        self.assertEqual(r.returncode, 0)
        kinds = [json.loads(l)["kind"] for l in (self.lesson / "chat.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["you", "claude", "you"])
        md = self.md()
        self.assertIn("> teach me DNS", md)
        self.assertIn("> next question", md)

    def test_hook_reads_stdin_as_utf8(self):
        # Claude Code sends UTF-8; Windows would decode stdin as cp1252 by default.
        write_feed(self.lesson, ENTRIES[:2])
        (self.lessons / ".current").write_text("dns", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        env["CLASSROOM_LESSONS_DIR"] = str(self.lessons)
        text = "1 – guessing — sure 🎓"
        event = {"hook_event_name": "UserPromptSubmit", "prompt": text}
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0)
        last = json.loads((self.lesson / "chat.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["text"], text)

    def test_hook_regenerates_md_even_with_no_new_entries(self):
        write_feed(self.lesson, ENTRIES[:2])
        (self.lessons / ".current").write_text("dns", encoding="utf-8")
        (self.lesson / "chat.md").write_text("stale", encoding="utf-8")
        r = self.run_hook({"hook_event_name": "Stop", "transcript_path": ""})
        self.assertEqual(r.returncode, 0)
        self.assertIn("### Claude", self.md())

    def test_hook_with_no_current_lesson_writes_nothing(self):
        write_feed(self.lesson, ENTRIES[:2])
        r = self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.lesson / "chat.md").exists())


if __name__ == "__main__":
    unittest.main()
