"""Mastery (0 to 1) for each concept in progress/concepts.json.

    python progress/mastery.py update [--lesson SLUG] [--json]

Add `--file PATH` before the command to use another concepts file (tests do).
`update` recomputes `mastery` from `attempts` and writes the file only if a
value changed. `--json` also prints {id: mastery} for the concepts it covered.
The rule is documented in progress/README.md. Standard library only.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().with_name("concepts.json")
WINDOW = 5            # only the last this-many graded attempts count
DECAY = 0.7           # each step back in time multiplies the weight by this
HINT_SCORE = 0.5      # a right answer that needed hints counts this much
CONFIDENT_WRONG = 2   # a wrong answer given at confidence 3 weighs this many times more
RATING_SCORE = {"again": 0.0, "hard": HINT_SCORE, "good": 1.0, "easy": 1.0}


def hints_used(note):
    m = re.search(r"hints?\s*:\s*(\d+)", note if isinstance(note, str) else "", re.I)
    return int(m.group(1)) if m else 0


def score(attempt):
    """How much one graded attempt counts as, 0 to 1.

    A `rating` (written by spaced review) decides it when present. Otherwise a
    wrong answer is 0, a right one that needed hints ("hints: N" in the note)
    is HINT_SCORE, and a clean right one is 1.
    """
    rating = attempt.get("rating")
    if rating in RATING_SCORE:
        return RATING_SCORE[rating]
    if not attempt.get("correct"):
        return 0.0
    return HINT_SCORE if hints_used(attempt.get("note")) > 0 else 1.0


def weight(attempt, age):
    """Recency weight (age 0 = newest), heavier for a confident wrong answer."""
    w = DECAY ** age
    if score(attempt) == 0.0 and attempt.get("confidence") == 3:
        w *= CONFIDENT_WRONG
    return w


def mastery(attempts):
    """Weighted mean of the last WINDOW attempts' scores; 0.0 with no attempts."""
    recent = [a for a in (attempts or []) if isinstance(a, dict)][-WINDOW:]
    if not recent:
        return 0.0
    newest_first = list(reversed(recent))
    ws = [weight(a, age) for age, a in enumerate(newest_first)]
    ss = [score(a) for a in newest_first]
    return round(sum(w * s for w, s in zip(ws, ss)) / sum(ws), 3)


def load(path):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "concepts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"{path} is not valid JSON ({e}). Fix it by hand or restore it from git.")
    if not isinstance(data, dict) or not isinstance(data.get("concepts"), dict):
        sys.exit(f"{path} has no 'concepts' object. See progress/README.md.")
    return data


def save(path, data):
    # Whole file, sorted keys, 2-space indent, LF newlines (progress/README.md).
    # Written to a temp file and swapped in, so a crash mid-write can't truncate history.
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".concepts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update(data, lesson=None):
    """Recompute mastery in place. Returns ({id: mastery} covered, changed ids)."""
    covered, changed = {}, []
    for cid, c in data["concepts"].items():
        if not isinstance(c, dict):  # a broken hand edit: leave it alone
            continue
        if lesson and c.get("lesson") != lesson:
            continue
        m = mastery(c.get("attempts"))
        covered[cid] = m
        if c.get("mastery") != m:
            c["mastery"] = m
            changed.append(cid)
    return covered, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="concepts file (default: progress/concepts.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("update")
    p.add_argument("--lesson", help="only this lesson's concepts")
    p.add_argument("--json", action="store_true", help="print {id: mastery} as JSON")
    args = ap.parse_args(argv)

    data = load(args.file)
    covered, changed = update(data, args.lesson)
    if changed:
        save(args.file, data)
    if args.json:
        print(json.dumps(covered, indent=2, sort_keys=True))
    else:
        print(f"{len(covered)} concept(s), {len(changed)} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
