---
name: review
description: Use when the learner says "review", "what's due", "what is due", "quiz me on old stuff", or asks to revisit things they learned before. Pulls the concepts that are due from the spaced-review schedule and runs a short recall-first session in a lesson folder, so it gets the viewer and audio too.
---

# Review

Review is for things the learner has already been taught. The point is to make
them pull the idea out of memory, because pulling it out is what makes it last.
Rereading does not. So this is not a lesson. It is a short quiz session, with
re-teaching only where something has gone wrong.

## Step 1: Find what is due

Run from the repo root:

```
python progress/review.py due --limit 8
```

If it prints an install hint, run `pip install fsrs` and try again. The list is
already mixed across lessons, on purpose: switching topics makes recall harder
and the memory stronger. Keep that order.

If nothing is due, say so in one line, say that the schedule is working, and
stop. Don't invent a review.

## Step 2: Set up the session

Follow "Starting a session" in `CLAUDE.md`, with the slug `<date>-review`, for
example `2026-10-20-review`. If that folder already exists today, add `-2`. The
lesson page is only a short title and a list of what's covered, so the viewer
and the audio work as usual.

Write one `## Today's review` section: a sentence on how many concepts are due
and from which lessons, with the titles as a list. Keep it for the ear.

## Step 3: Ask, one concept at a time

For each due concept, in the order given:

1. **Recall first.** Ask the question with no hint and no options, as an open
   question in chat. Ask for the claim in their own words, or what happens in
   a small example. Look at the concept's title and, if it helps, the notes in
   the lesson that introduced it. Never read the answer out first.
2. If recall is clearly the wrong shape for the idea, a short graded
   multiple-choice is fine. Follow "Writing quiz options" in the `teach` skill.
3. Optionally ask how sure they are, from one to three.
4. Give the feedback in the usual form: start with "✓ Correct." or
   "✗ Not quite.", then the right answer and a short reason.
5. **Record it right away**, using the ratings from the `teach` skill's Spaced
   review section: wrong is `again`, right with hints is `hard`, right is
   `good`, right quickly and confidently is `easy`.

```
python progress/review.py record <id> --rating good --kind review [--confidence 1-3] [--note "..."]
```

Keep the questions short and speakable. Don't give hints before the first
try. If they are stuck, a hint is fine, but the answer then counts as `hard`
at best.

## Step 4: Confident and wrong goes back to teaching

If the learner was confident (three) and wrong, or was wrong in a way that
shows a real misconception, don't just move on. A confident wrong belief is
the most expensive kind.

1. Record it as `again`, with the misconception in `--note`.
2. Switch to teaching mode for that one node, using the `teach` skill: say the
   idea afresh from the nodes it rests on, add a short `## <node>` section with
   a visual, then a practice task, then a new graded check.
3. Record the new check as its own attempt.

Add the wrong belief, in a few words, to that concept's `misconceptions` list
in `progress/concepts.json`. That is the one thing you may edit by hand there.
Write the file as a whole, with sorted keys, two-space indents, and never
delete anything.

## Step 5: Close

Say how many they got right, in a sentence, and when the next ones are due
(`python progress/review.py stats` shows the counts). Commit the review
lesson folder and `progress/concepts.json`.
