# Practice templates: worked, faded, blank

A complete example of the three-step practice sequence, for a tiny Python
topic: list comprehensions. Copy the shape, not the topic. The files go in
`lessons/<slug>/practice/`.

Every file below runs as written with `python <file>`:

- The worked file runs clean.
- The faded and blank files are **meant to fail** until the learner fills them
  in. Their self-check is the `assert` lines at the bottom, and the failure
  message says what is wrong. Never put the answer in a faded or blank file.
- Number the files after the node: `03-worked.py`, `03-faded.py`,
  `03-faded-2.py`, `03-blank.py`.
- To test a template, copy it to a scratch folder, fill it in, and check that
  the asserts pass. Then check that the untouched copy fails. Don't commit
  filled-in answers.

## Step 1: worked, `03-worked.py`

Say aloud: "Open `03-worked.py`. Before you run it, tell me what you think it
prints. Then run it with `python 03-worked.py`."

```python
# WORKED EXAMPLE: the squares of the even numbers.
# Predict what this prints, then run it.

numbers = [1, 2, 3, 4, 5, 6]

# First the long way, with a loop, so the shape is familiar.
squares_loop = []
for n in numbers:            # visit every item in the source list
    if n % 2 == 0:           # keep only the even ones
        squares_loop.append(n * n)   # build the new item and add it

# The same thing as a list comprehension.
# Read it as: [what to build, for each item, in the source, if the test passes]
squares = [n * n for n in numbers if n % 2 == 0]

print(squares_loop)
print(squares)

# Both ways must give the same list.
assert squares == squares_loop == [4, 16, 36]
print("Worked example OK")
```

## Step 2: faded, `03-faded.py`

Same shape of problem, new data. The loop version is still there. The key
lines are replaced by `TODO(you)` markers.

Say aloud: "Open `03-faded.py`. Fill in the TODO line, then run it. If the
checks pass you will see 'Faded 1 OK'."

```python
# FADED 1: the lengths of the words that are longer than 3 letters.
# Fill in the TODO(you) lines, then run: python 03-faded.py

words = ["cat", "horse", "owl", "tiger", "ant", "zebra"]

# The long way, to compare against.
lengths_loop = []
for w in words:
    if len(w) > 3:
        lengths_loop.append(len(w))

# TODO(you): write the comprehension that builds the same list.
# Hint: [what to build  for item in source  if test]
lengths = []

# --- self-check ---
import re
source = open(__file__, encoding="utf-8").read()
assert re.search(r"^lengths = \[.+ for .+ in .+\]", source, re.M), "write lengths as a list comprehension"
assert lengths_loop == [5, 5, 5], "the loop version should give [5, 5, 5]"
assert lengths == lengths_loop, "lengths should match the loop version"
print("Faded 1 OK")
```

The next faded file removes more: no loop to compare against, and the test
condition is yours to write as well.

```python
# FADED 2: the upper-case versions of the words that start with a vowel.
# Fill in the TODO(you) lines, then run: python 03-faded-2.py

words = ["apple", "banana", "orange", "kiwi", "egg", "plum"]

# TODO(you): build the list of the words that start with a vowel,
# each one in upper case. Use one list comprehension.
# Tip: w[0] is the first letter, and "aeiou" is the vowels.
loud = []

# --- self-check ---
assert loud == ["APPLE", "ORANGE", "EGG"], "expected ['APPLE', 'ORANGE', 'EGG']"
print("Faded 2 OK")
```

## Step 3: blank, `03-blank.py`

A new problem. Only a spec in a docstring, an empty function, and the
self-check.

Say aloud: "Open `03-blank.py`. Read the spec, write the function, and run the
file. When the checks pass, tell me."

```python
# BLANK: write the function from the spec, then run: python 03-blank.py

def initials(names):
    """Return a list of the upper-case first letters of each name.

    Skip any name that is an empty string.
    Use a list comprehension.

    initials(["ada", "", "grace"])  ->  ["A", "G"]
    """
    # TODO(you): write the function body.
    return None

# --- self-check ---
assert initials(["ada", "", "grace"]) == ["A", "G"], "the example from the spec"
assert initials([]) == [], "an empty list gives an empty list"
assert initials(["", ""]) == [], "empty names are skipped"
assert initials(["linus"]) == ["L"], "one name"
print("Blank OK")
```

## For a longer exercise: `check_03.py`

When asserts at the bottom would give the answer away, or the exercise has
several functions, put the checks in a separate `check_03.py` that imports the
learner's file and prints one friendly line per check. Keep it standard
library only.

## Non-programming topics

Use Markdown worksheets with the same three steps: `03-worked.md` is a full
solution with the reason for each step, `03-faded.md` is the same kind of
problem with blanks written as `TODO(you): ...`, and `03-blank.md` is a new
problem with only the question. The self-check is a short answer key you hold
back and reveal after they have tried, or a question in chat.
