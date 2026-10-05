# Progress data

`progress/concepts.json` is the one record of what the learner knows. The
spaced-review scheduler, the progress map, confidence tracking and the learner
profile all read and write it. Don't create a parallel file for any of them.
Add a field here instead, and document it below.

The file is committed with the lessons, so progress travels with the repo.

## Format

```json
{
  "version": 1,
  "concepts": {
    "dns-maps-names-to-ips": {
      "title": "DNS maps names to IP addresses",
      "lesson": "2026-10-01-how-dns-works",
      "requires": ["ip-address-identifies-a-host"],
      "added": "2026-10-01T18:04:00Z",
      "mastery": 0.8,
      "attempts": [
        {
          "at": "2026-10-01T18:20:00Z",
          "kind": "quiz",
          "correct": true,
          "confidence": 3,
          "note": "picked IP address"
        }
      ],
      "fsrs": null,
      "misconceptions": []
    }
  }
}
```

| Field | Meaning |
|---|---|
| key | Concept id: a kebab-case slug, unique across all lessons. One per node in a lesson's dependency map. |
| `title` | The node's label, written as a claim. |
| `lesson` | Slug of the lesson that introduced it. |
| `requires` | Concept ids this node is built on (the map's incoming edges). |
| `added` | ISO-8601 UTC time it entered the map. |
| `mastery` | 0–1, how well the learner has hold of the concept. Owned by the progress-map feature and written only by `progress/mastery.py` (see below). `0` when there are no attempts. |
| `attempts` | Every graded check, oldest first. `kind`: `quiz`, `recall`, `practice` or `review`. `confidence`: 1–3 as the learner rated it, or `null` if not asked. |
| `fsrs` | Spaced-review card state, owned by the spaced-review feature and written only by `progress/review.py`. It is py-fsrs's `Card.to_dict()` (see below). `null` only for a concept written by hand; `review.py record` starts a card for it. |
| `misconceptions` | Short strings naming wrong models the learner has shown, e.g. `"thinks DNS assigns IPs"`. |

## Spaced review (`fsrs`, `attempts`)

`progress/review.py` is the one writer of `fsrs` and of the attempts it
records. It needs [py-fsrs](https://pypi.org/project/fsrs/) (MIT licence):
`pip install fsrs`. It was written against py-fsrs 6.3.2 (`Scheduler`, `Card`,
`Rating`, default parameters, fuzzing on).

- `fsrs` holds the card exactly as py-fsrs serializes it: `card_id`, `state`
  (1 learning, 2 review, 3 relearning), `step`, `stability`, `difficulty`,
  `due` and `last_review`. Times are ISO-8601 with a `+00:00` offset. A
  concept is **due** when `fsrs.due` is now or earlier. The viewer's "N due"
  badge counts exactly that.
- `add` writes a fresh card whose first `due` is one day after `added`, so a
  lesson's concepts are not quizzed straight after they were taught.
- `record` appends to `attempts` and reschedules. The attempt has the usual
  fields plus `rating` (`again`, `hard`, `good` or `easy`). `correct` is
  `false` only for `again`.
- Rating from a graded check: wrong is `again`, right with hints is `hard`,
  right is `good`, right quickly and confidently is `easy`.
- `due` lists due concepts interleaved across lessons, not one lesson at a time.

## Mastery (`mastery`)

`python progress/mastery.py update [--lesson SLUG] [--json]` recomputes
`mastery` for every concept (or one lesson's) from `attempts`, and writes the
file only if a value changed. `--json` prints `{id: mastery}`. Run it after
each recorded check. Nothing else writes `mastery`, and it never touches
`attempts` or `fsrs`.

The rule, in `progress/mastery.py`:

- Only the last 5 attempts count. Older ones stay in the file but not in the score.
- Each attempt scores 1 for a clean right answer, 0.5 for a right answer that
  needed hints, and 0 for a wrong one. Hints are read from the note, as
  `hints: N`. If the attempt has a `rating` (written by spaced review), the
  rating decides instead: `again` 0, `hard` 0.5, `good` and `easy` 1.
- Mastery is the weighted mean of those scores. The newest attempt has weight
  1, and each older one counts 0.7 times the one after it.
- A wrong answer given with `confidence` 3 weighs double, so a confident
  misconception drags the score down hard.
- No attempts gives 0.

The thresholds are shared with the viewer's progress map and the `teach`
skill: mastery of 0.8 or more is **mastered**. A concept is **locked** when
it is not mastered and some concept in its `requires` is not mastered. A
concept is **new** with no attempts, and **learning** otherwise.

## Rules

- Write the file as a whole, pretty-printed with 2-space indents and sorted
  keys, so diffs stay readable.
- Never delete a concept or an attempt. History is the point.
- Times are UTC with a trailing `Z`.

## Learner profile

`progress/profile.md` is a short, human-readable note about the learner, with
five headings: goals, interests (to draw analogies from), what has worked,
pacing, and recurring misconceptions. Misconception entries cross-reference
concept ids from `concepts.json`. Claude reads it at session start and updates
it at session end. It holds observed facts only, never secrets, and starts as
an empty template.
