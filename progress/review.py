"""Spaced review over progress/concepts.json, scheduled with py-fsrs.

    python progress/review.py add <id> --title "..." --lesson <slug> [--requires a,b]
    python progress/review.py due [--limit N]
    python progress/review.py record <id> --rating again|hard|good|easy
                              [--confidence 1-3] [--note "..."] [--kind quiz|recall|practice|review]
    python progress/review.py stats

Add `--file PATH` before the command to use another concepts file (tests do).
Needs the py-fsrs package: pip install fsrs
Data format and write rules: progress/README.md.
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    from fsrs import Card, Rating, Scheduler, State
except ImportError:
    sys.exit("py-fsrs is not installed. Run: pip install fsrs")

DEFAULT_FILE = Path(__file__).resolve().with_name("concepts.json")
RATINGS = {"again": Rating.Again, "hard": Rating.Hard, "good": Rating.Good, "easy": Rating.Easy}
FIRST_REVIEW_DELAY = dt.timedelta(days=1)  # a new concept is first due a day after it is added
KINDS = ("quiz", "recall", "practice", "review")


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def stamp(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def new_card(now):
    card = Card()
    card.due = now + FIRST_REVIEW_DELAY
    return card


def add(data, cid, title, lesson, requires=(), now=None):
    """Add a concept. Adding an id that exists changes nothing and returns False."""
    now = now or utc_now()
    if cid in data["concepts"]:
        return False
    data["concepts"][cid] = {
        "title": title,
        "lesson": lesson,
        "requires": list(requires),
        "added": stamp(now),
        "attempts": [],
        "fsrs": new_card(now).to_dict(),
        "misconceptions": [],
    }
    return True


def record(data, cid, rating, confidence=None, note=None, kind="review", now=None):
    """Append an attempt and reschedule. Returns the next due time."""
    now = now or utc_now()
    c = data["concepts"][cid]
    card = Card.from_dict(c["fsrs"]) if c.get("fsrs") else new_card(now)
    card, _ = Scheduler().review_card(card, RATINGS[rating], now)
    c["fsrs"] = card.to_dict()
    c.setdefault("attempts", []).append({
        "at": stamp(now), "kind": kind, "correct": rating != "again",
        "confidence": confidence, "note": note, "rating": rating,
    })
    return card.due


def due_concepts(data, now=None, limit=None):
    """Due concept ids, interleaved across lessons.

    Within a lesson the most overdue comes first. Lessons then take turns,
    the lesson with the most overdue concept going first in each round.
    """
    now = now or utc_now()
    by_lesson = {}
    for cid, c in data["concepts"].items():
        if not c.get("fsrs"):
            continue
        due = dt.datetime.fromisoformat(c["fsrs"]["due"].replace("Z", "+00:00"))
        if due.tzinfo is None:  # a hand-written time without an offset is taken as UTC
            due = due.replace(tzinfo=dt.timezone.utc)
        if due <= now:
            by_lesson.setdefault(c.get("lesson"), []).append((due, cid))
    queues = [sorted(q) for q in by_lesson.values()]
    queues.sort(key=lambda q: q[0])
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0)[1])
    return out[:limit] if limit else out


def stats(data, now=None):
    now = now or utc_now()
    cs = data["concepts"]
    scheduled = [c for c in cs.values() if c.get("fsrs")]
    return {
        "concepts": len(cs),
        "due": len(due_concepts(data, now)),
        "mature": sum(1 for c in scheduled if c["fsrs"]["state"] == State.Review),
        "attempts": sum(len(c.get("attempts", [])) for c in cs.values()),
        "correct": sum(1 for c in cs.values() for a in c.get("attempts", []) if a.get("correct")),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="concepts file (default: progress/concepts.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("id")
    p.add_argument("--title", required=True)
    p.add_argument("--lesson", required=True)
    p.add_argument("--requires", default="")
    p = sub.add_parser("due")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("record")
    p.add_argument("id")
    p.add_argument("--rating", required=True, choices=list(RATINGS))
    p.add_argument("--confidence", type=int, choices=[1, 2, 3])
    p.add_argument("--note")
    p.add_argument("--kind", choices=KINDS, default="review")
    sub.add_parser("stats")
    args = ap.parse_args(argv)

    data = load(args.file)
    if args.cmd == "add":
        requires = [r.strip() for r in args.requires.split(",") if r.strip()]
        if not add(data, args.id, args.title, args.lesson, requires):
            print(f"{args.id} already exists; left unchanged")
            return 0
        save(args.file, data)
        print(f"added {args.id}")
    elif args.cmd == "due":
        ids = due_concepts(data, limit=args.limit)
        for cid in ids:
            c = data["concepts"][cid]
            print(f"{cid}\t{c['lesson']}\t{c['title']}")
        if not ids:
            print("nothing due")
    elif args.cmd == "record":
        if args.id not in data["concepts"]:
            sys.exit(f"unknown concept: {args.id}")
        due = record(data, args.id, args.rating, args.confidence, args.note, args.kind)
        save(args.file, data)
        print(f"recorded {args.rating} for {args.id}; next due {stamp(due)}")
    else:
        for k, v in stats(data).items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
