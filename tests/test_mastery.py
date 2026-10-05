"""Unit tests for progress/mastery.py:  python -m unittest tests/test_mastery.py

Everything runs on temp files, so progress/concepts.json is never touched.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "progress"))
import mastery  # noqa: E402


def ok(note=None, confidence=None, **kw):
    return {"correct": True, "confidence": confidence, "note": note, **kw}


def bad(confidence=None, **kw):
    return {"correct": False, "confidence": confidence, "note": None, **kw}


class FormulaTests(unittest.TestCase):
    def test_no_attempts_is_zero(self):
        self.assertEqual(mastery.mastery([]), 0.0)
        self.assertEqual(mastery.mastery(None), 0.0)

    def test_all_correct_is_one(self):
        self.assertEqual(mastery.mastery([ok(), ok(), ok()]), 1.0)

    def test_all_wrong_is_zero(self):
        self.assertEqual(mastery.mastery([bad(), bad()]), 0.0)

    def test_one_clean_answer_passes_the_gate(self):
        self.assertGreaterEqual(mastery.mastery([ok()]), 0.8)

    def test_confident_wrong_counts_heavier_than_unsure_wrong(self):
        history = [ok(), ok()]
        confident = mastery.mastery(history + [bad(confidence=3)])
        unsure = mastery.mastery(history + [bad(confidence=1)])
        unrated = mastery.mastery(history + [bad()])
        self.assertLess(confident, unsure)
        self.assertEqual(unsure, unrated)
        self.assertLess(confident, 0.8)

    def test_confident_right_is_not_special(self):
        self.assertEqual(mastery.mastery([ok(confidence=3)]), mastery.mastery([ok(confidence=1)]))

    def test_hints_count_partly(self):
        hinted = mastery.mastery([ok("hints: 2")])
        self.assertEqual(hinted, mastery.HINT_SCORE)
        self.assertLess(hinted, 0.8)
        self.assertGreater(hinted, 0.0)

    def test_hint_note_forms(self):
        for note in ("hints: 1", "Hints:3", "needed hint: 1", "picked IP, hints: 2"):
            self.assertGreater(mastery.hints_used(note), 0, note)
        for note in (None, "", "no hints needed", "hints: 0"):
            self.assertEqual(mastery.hints_used(note), 0, note)
        self.assertEqual(mastery.mastery([ok("no hints needed")]), 1.0)

    def test_recent_attempts_matter_more(self):
        improving = mastery.mastery([bad(), ok(), ok()])
        declining = mastery.mastery([ok(), ok(), bad()])
        self.assertGreater(improving, declining)

    def test_only_last_window_counts(self):
        old_failures = [bad()] * 10
        self.assertEqual(mastery.mastery(old_failures + [ok()] * mastery.WINDOW), 1.0)

    def test_rating_overrides_correct_and_note(self):
        self.assertEqual(mastery.mastery([ok(rating="again")]), 0.0)
        self.assertEqual(mastery.mastery([ok(rating="hard")]), mastery.HINT_SCORE)
        self.assertEqual(mastery.mastery([ok("hints: 3", rating="good")]), 1.0)
        self.assertEqual(mastery.mastery([ok(rating="easy")]), 1.0)

    def test_again_with_confidence_three_is_confident_wrong(self):
        plain = mastery.mastery([ok(), ok(), ok(rating="again", confidence=1)])
        confident = mastery.mastery([ok(), ok(), ok(rating="again", confidence=3)])
        self.assertLess(confident, plain)

    def test_always_between_zero_and_one(self):
        mixes = [[bad(3)] * 5, [ok()] * 5, [ok("hints: 1"), bad(3), ok(), bad()]]
        for attempts in mixes:
            self.assertTrue(0.0 <= mastery.mastery(attempts) <= 1.0)


class UpdateTests(unittest.TestCase):
    def data(self):
        return {"version": 1, "concepts": {
            "a": {"lesson": "L1", "attempts": [ok(), ok()]},
            "b": {"lesson": "L2", "attempts": [bad(3)]},
            "c": {"lesson": "L1", "attempts": []},
        }}

    def test_update_writes_mastery_for_every_concept(self):
        d = self.data()
        covered, changed = mastery.update(d)
        self.assertEqual(sorted(changed), ["a", "b", "c"])
        self.assertEqual(d["concepts"]["a"]["mastery"], 1.0)
        self.assertEqual(d["concepts"]["b"]["mastery"], 0.0)
        self.assertEqual(d["concepts"]["c"]["mastery"], 0.0)
        self.assertEqual(covered, {"a": 1.0, "b": 0.0, "c": 0.0})

    def test_lesson_filter(self):
        d = self.data()
        covered, _ = mastery.update(d, "L1")
        self.assertEqual(sorted(covered), ["a", "c"])
        self.assertNotIn("mastery", d["concepts"]["b"])

    def test_second_update_changes_nothing(self):
        d = self.data()
        mastery.update(d)
        self.assertEqual(mastery.update(d)[1], [])

    def test_cli_writes_sorted_file_and_leaves_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            d = self.data()
            d["concepts"]["a"]["fsrs"] = {"state": 2}
            f.write_text(json.dumps(d), encoding="utf-8")
            self.assertEqual(mastery.main(["--file", str(f), "update"]), 0)
            text = f.read_text(encoding="utf-8")
            out = json.loads(text)
            self.assertEqual(out["concepts"]["a"]["fsrs"], {"state": 2})
            self.assertEqual(out["concepts"]["a"]["mastery"], 1.0)
            self.assertEqual(text, json.dumps(out, indent=2, sort_keys=True) + "\n")

    def test_cli_unchanged_file_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            d = self.data()
            mastery.update(d)
            raw = json.dumps(d)  # deliberately not the canonical format
            f.write_text(raw, encoding="utf-8")
            mastery.main(["--file", str(f), "update"])
            self.assertEqual(f.read_text(encoding="utf-8"), raw)

    def test_missing_file_is_empty(self):
        self.assertEqual(mastery.load(Path("does-not-exist.json")), {"version": 1, "concepts": {}})

    def test_failed_write_leaves_the_old_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            f.write_text('{"concepts": {}, "version": 1}\n', encoding="utf-8")
            before = f.read_bytes()
            with self.assertRaises(TypeError):
                mastery.save(f, {"concepts": {"a": {"bad": object()}}, "version": 1})
            self.assertEqual(f.read_bytes(), before)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["concepts.json"])

    def test_malformed_file_exits_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "concepts.json"
            for content in ("", "{not json", "[]", '{"version": 1}'):
                f.write_text(content, encoding="utf-8")
                with self.assertRaises(SystemExit) as cm:
                    mastery.load(f)
                self.assertIsInstance(cm.exception.code, str)
                self.assertEqual(f.read_text(encoding="utf-8"), content)

    def test_broken_entries_are_skipped_not_fatal(self):
        data = {"version": 1, "concepts": {
            "null": None,
            "odd": {"attempts": [None, {"correct": True, "note": 5}, "x"]},
        }}
        covered, _ = mastery.update(data)
        self.assertEqual(covered, {"odd": 1.0})
        self.assertIsNone(data["concepts"]["null"])


if __name__ == "__main__":
    unittest.main()
