"""Unit tests for progress/review.py:  python -m unittest tests/test_review.py

Everything runs on temp files, so progress/concepts.json is never touched.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "progress"))
import review  # noqa: E402

T0 = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
DAY = dt.timedelta(days=1)


def fresh():
    return {"version": 1, "concepts": {}}


class ReviewTests(unittest.TestCase):
    def test_add_creates_scheduled_concept(self):
        d = fresh()
        self.assertTrue(review.add(d, "a", "A is a claim", "L1", ["b"], now=T0))
        c = d["concepts"]["a"]
        self.assertEqual((c["title"], c["lesson"], c["requires"]), ("A is a claim", "L1", ["b"]))
        self.assertEqual(c["attempts"], [])
        self.assertEqual(c["added"], "2026-10-01T12:00:00Z")
        self.assertEqual(dt.datetime.fromisoformat(c["fsrs"]["due"]), T0 + DAY)
        self.assertEqual(review.due_concepts(d, T0), [])
        self.assertEqual(review.due_concepts(d, T0 + DAY), ["a"])

    def test_add_existing_changes_nothing(self):
        d = fresh()
        review.add(d, "a", "first", "L1", now=T0)
        review.record(d, "a", "good", now=T0 + DAY)
        before = json.dumps(d, sort_keys=True)
        self.assertFalse(review.add(d, "a", "second", "L2", now=T0 + 2 * DAY))
        self.assertEqual(json.dumps(d, sort_keys=True), before)

    def test_record_appends_attempt_and_reschedules(self):
        d = fresh()
        review.add(d, "a", "A", "L1", now=T0)
        now = T0 + DAY
        due = review.record(d, "a", "good", confidence=3, note="n", now=now)
        c = d["concepts"]["a"]
        self.assertEqual(c["attempts"], [{"at": "2026-10-02T12:00:00Z", "kind": "review", "correct": True,
                                          "confidence": 3, "note": "n", "rating": "good"}])
        self.assertGreater(due, now)
        self.assertEqual(dt.datetime.fromisoformat(c["fsrs"]["due"]), due)

    def test_again_is_incorrect_and_easy_waits_longer_than_hard(self):
        gaps = {}
        for rating in ("again", "hard", "good", "easy"):
            d = fresh()
            review.add(d, "a", "A", "L1", now=T0)
            gaps[rating] = review.record(d, "a", rating, now=T0 + DAY) - (T0 + DAY)
            self.assertEqual(d["concepts"]["a"]["attempts"][0]["correct"], rating != "again")
        self.assertLess(gaps["again"], gaps["good"])
        self.assertLess(gaps["hard"], gaps["easy"])

    def test_record_on_unscheduled_concept_starts_a_card(self):
        d = {"version": 1, "concepts": {"a": {"title": "A", "lesson": "L1", "attempts": [], "fsrs": None}}}
        review.record(d, "a", "good", now=T0)
        self.assertIsNotNone(d["concepts"]["a"]["fsrs"])

    def test_history_is_never_deleted(self):
        d = fresh()
        review.add(d, "a", "A", "L1", now=T0)
        d["concepts"]["a"]["mastery"] = 0.5  # fields owned by other features survive
        d["concepts"]["a"]["misconceptions"] = ["thinks X"]
        now = T0 + DAY
        for rating in ("again", "good", "hard", "easy"):
            review.record(d, "a", rating, now=now)
            now += 3 * DAY
        c = d["concepts"]["a"]
        self.assertEqual([a["rating"] for a in c["attempts"]], ["again", "good", "hard", "easy"])
        self.assertEqual((c["mastery"], c["misconceptions"]), (0.5, ["thinks X"]))
        review.add(d, "a", "again", "L9", now=now)
        self.assertEqual(len(d["concepts"]["a"]["attempts"]), 4)

    def test_due_orders_by_overdue_and_interleaves_lessons(self):
        d = fresh()
        # L1 has three concepts, L2 has two; plain sorting by due time would not interleave them.
        for cid, lesson, days_ago in [("a1", "L1", 9), ("a2", "L1", 8), ("a3", "L1", 7),
                                      ("b1", "L2", 6), ("b2", "L2", 5)]:
            review.add(d, cid, cid, lesson, now=T0 - (days_ago + 1) * DAY)
        review.add(d, "later", "later", "L3", now=T0)  # due tomorrow, not yet
        self.assertEqual(review.due_concepts(d, T0), ["a1", "b1", "a2", "b2", "a3"])
        self.assertEqual(review.due_concepts(d, T0, limit=2), ["a1", "b1"])

    def test_stats(self):
        d = fresh()
        review.add(d, "a", "A", "L1", now=T0)
        review.add(d, "b", "B", "L1", now=T0)
        review.record(d, "a", "good", now=T0 + DAY)
        review.record(d, "a", "again", now=T0 + 2 * DAY)
        s = review.stats(d, T0 + 30 * DAY)
        self.assertEqual((s["concepts"], s["attempts"], s["correct"], s["due"]), (2, 2, 1, 2))

    def test_cli_round_trip_writes_canonical_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = str(Path(tmp) / "concepts.json")
            self.assertEqual(review.main(["--file", f, "add", "z", "--title", "Z", "--lesson", "L1", "--requires", "x, y"]), 0)
            review.main(["--file", f, "add", "a", "--title", "A", "--lesson", "L1"])
            review.main(["--file", f, "record", "z", "--rating", "hard", "--confidence", "2", "--kind", "quiz"])
            raw = Path(f).read_bytes().decode("utf-8")
            data = json.loads(raw)
            self.assertEqual(raw, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            self.assertNotIn("\r", raw)
            self.assertEqual(data["concepts"]["z"]["requires"], ["x", "y"])
            self.assertEqual(data["concepts"]["z"]["attempts"][0]["kind"], "quiz")
            self.assertNotIn("mastery", data["concepts"]["z"])
            with self.assertRaises(SystemExit):
                review.main(["--file", f, "record", "nope", "--rating", "good"])

    def test_failed_write_leaves_the_old_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            review.main(["--file", str(f), "add", "a", "--title", "A", "--lesson", "L1"])
            before = f.read_bytes()
            data = review.load(f)
            data["concepts"]["a"]["bad"] = object()  # not JSON-serialisable: the write fails part way
            with self.assertRaises(TypeError):
                review.save(f, data)
            self.assertEqual(f.read_bytes(), before)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["concepts.json"])  # no temp file left

    def test_malformed_file_exits_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            for content in ("", "{not json", "[]", '{"version": 1}'):
                f.write_text(content, encoding="utf-8")
                with self.assertRaises(SystemExit) as cm:
                    review.load(f)
                self.assertIsInstance(cm.exception.code, str)
                self.assertEqual(f.read_text(encoding="utf-8"), content)  # never rewritten

    def test_due_tolerates_hand_written_times_and_missing_lesson(self):
        data = fresh()
        data["concepts"]["naive"] = {"fsrs": {"due": "2026-09-30T12:00:00"}}
        data["concepts"]["zulu"] = {"lesson": "L1", "fsrs": {"due": "2026-09-30T13:00:00Z"}}
        self.assertEqual(review.due_concepts(data, now=T0), ["naive", "zulu"])


if __name__ == "__main__":
    unittest.main()
