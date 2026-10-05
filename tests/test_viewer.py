"""Smoke test for the lesson viewer and the chat hook.

    python tests/test_viewer.py [--port 8790] [--headed]

Starts its own server (viewer/serve.py) on the given port (default 8790, so it never
collides with a live session on 8765), runs every check in headless Chromium,
and exits non-zero on any failure. Requires: pip install playwright &&
python -m playwright install chromium.

Feature branches add their own checks as new `check_*` functions, defined
above the CHECKS line. They are picked up automatically.
"""


import argparse
import datetime
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


HOOK = ROOT / ".claude" / "hooks" / "chat_feed.py"


# Records every utterance instead of speaking it, and ends it right away.
FAKE_SPEECH = """
window.__spoken = [];
speechSynthesis.speak = (u) => { window.__spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 5); };
// New lesson sections wait this long for Claude's chat reply (30 s in real use).
try { localStorage.setItem("classroom.chatWait", "400"); } catch {}
"""


class Fixture:
    """A throwaway lesson folder plus a fake transcript, removed afterwards."""

    def __init__(self, port):
        self.slug = f"_test-{port}"
        self.dir = ROOT / "lessons" / self.slug
        self.transcript = self.dir / "transcript.jsonl"
        self.pointer = ROOT / "lessons" / ".current"
        self.saved_pointer = self.pointer.read_text() if self.pointer.exists() else None

    def __enter__(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        (self.dir / "lesson.md").write_text(
            "# Test lesson\n\n## First idea\n\nThe first idea is short. It visits example.com once.\n\n"
            "```mermaid\ngraph LR\n  A --> B\n```\n\n<details>\n\nHidden from audio.\n\n</details>\n",
            encoding="utf-8",
        )
        self.transcript.write_text(json.dumps({
            "type": "assistant", "uuid": "old", "timestamp": "2020-01-01T00:00:00.000Z",
            "message": {"content": [{"type": "text", "text": "OLD, must not appear"}]},
        }) + "\n", encoding="utf-8")
        self.pointer.write_text(self.slug)
        time.sleep(1.1)  # the hook only copies messages newer than .current
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)
        if self.saved_pointer is None:
            self.pointer.unlink(missing_ok=True)
        else:
            self.pointer.write_text(self.saved_pointer)

    def say(self, uuid, text):
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "uuid": uuid, "timestamp": stamp,
                                "message": {"content": [{"type": "text", "text": text}]}}) + "\n")

    def hook(self, event):
        event["transcript_path"] = str(self.transcript)
        subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), text=True, check=True)


def check_lesson_renders(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector(".mermaid svg", timeout=15000)
    assert page.locator("main section").count() == 1, "expected one section"
    items = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section')).map(i => i.text)")
    assert items == ["First idea", "The first idea is short.", "It visits example.com once."], items


def check_new_section_autoreads(page, fx, base):
    page.evaluate("() => { window.__spoken.length = 0; }")
    lesson = fx.dir / "lesson.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n## Second idea\n\nAdded live.\n", encoding="utf-8")
    page.wait_for_function("() => document.querySelectorAll('main section').length === 2", timeout=8000)
    page.wait_for_function("() => window.__spoken.includes('Added live.')", timeout=5000)
    # Focus mode followed the reading to this section and saved the place; put it back at
    # the top so later checks on this page (the lesson keeps both sections) start there.
    page.evaluate("() => localStorage.setItem('classroom.focus.pos.' + Classroom.slug, JSON.stringify({ k: 'First idea#1', i: 0 }))")


def check_chat_feed(page, fx, base):
    page.evaluate("() => { window.__spoken.length = 0; }")
    q = {"questions": [{"question": "What does DNS turn a name into?", "header": "Probe 1", "multiSelect": False,
                        "options": [{"label": "An IP address"}, {"label": "A MAC address"}]}]}
    fx.hook({"hook_event_name": "UserPromptSubmit", "prompt": "teach me DNS"})
    fx.say("a1", "Let's see what you already know.")
    fx.hook({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "tu1", "tool_input": q})
    fx.hook({"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "tu1",
             "tool_input": q, "tool_response": {**q, "answers": {"What does DNS turn a name into?": "An IP address"}}})
    fx.say("a2", "✓ Correct. DNS maps example.com to an IP address.")
    fx.hook({"hook_event_name": "Stop"})
    fx.hook({"hook_event_name": "Stop"})  # must not duplicate

    kinds = [json.loads(l)["kind"] for l in (fx.dir / "chat.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kinds == ["you", "claude", "quiz", "answer", "claude"], kinds
    page.wait_for_function("() => window.__spoken.includes('DNS maps example.com to an IP address.')", timeout=8000)
    spoken = page.evaluate("window.__spoken")
    assert "Option 1: An IP address." in spoken, spoken
    assert not any("OLD" in s for s in spoken), spoken
    assert page.locator("li.chosen").inner_text() == "An IP address"


def check_confidence_question(page, fx, base):
    page.evaluate("() => { window.__spoken.length = 0; }")
    opts = ["1 – guessing", "2 – fairly sure", "3 – certain"]
    q = {"questions": [
        {"question": "Which layer routes packets?", "header": "Probe 2", "multiSelect": False,
         "options": [{"label": "Network"}, {"label": "Link"}]},
        {"question": "How sure are you?", "header": "Confidence", "multiSelect": False,
         "options": [{"label": o} for o in opts]}]}
    fx.hook({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "tu2", "tool_input": q})
    page.wait_for_function("() => window.__spoken.includes('How sure are you? One to three.')", timeout=8000)
    spoken = page.evaluate("window.__spoken")
    assert not any("guessing" in s for s in spoken), spoken
    assert "Option 1: Network." in spoken, spoken  # other questions are unchanged
    conf = page.locator(".quiz-q.confidence").last
    assert page.locator(".quiz-q.confidence").count() == 1, page.locator(".quiz-q.confidence").count()
    assert conf.locator("li").count() == 3
    assert conf.locator("ol").evaluate("e => getComputedStyle(e).display") == "flex", conf.locator("ol").evaluate("e => getComputedStyle(e).display")
    assert conf.locator("li").first.evaluate("e => getComputedStyle(e).listStyleType") == "none"


VOICES_ROBOTIC = [
    {"name": "Microsoft David - English (United States)", "lang": "en-US", "voiceURI": "Microsoft David - English (United States)", "localService": True, "default": True},
    {"name": "Microsoft Zira - English (United States)", "lang": "en-US", "voiceURI": "Microsoft Zira - English (United States)", "localService": True, "default": False},
]


VOICES_NATURAL = VOICES_ROBOTIC + [
    {"name": "Microsoft Aria Online (Natural) - English (United States)", "lang": "en-US", "voiceURI": "Microsoft Aria Online (Natural) - English (United States)", "localService": False, "default": False},
]


ARIA = VOICES_NATURAL[-1]["name"]


DAVID = VOICES_ROBOTIC[0]["name"]


class voice_page:
    """A page in its own browser context with stubbed voices, closed afterwards.

    Its own context keeps the init scripts and localStorage away from the
    shared page. With late=True getVoices() is empty at first and the voices
    arrive 300 ms later with a voiceschanged event, as in a real browser.
    """

    def __init__(self, page, voices, late=False):
        self.browser, self.voices, self.late = page.context.browser, voices, late

    def __enter__(self):
        self.ctx = self.browser.new_context(viewport={"width": 1300, "height": 900})
        page = self.ctx.new_page()
        page.add_init_script(FAKE_SPEECH)
        page.add_init_script(f"""
        window.__all = {json.dumps(self.voices)};
        window.__voices = {"[]" if self.late else "window.__all"};
        speechSynthesis.getVoices = () => window.__voices;
        Object.defineProperty(SpeechSynthesisUtterance.prototype, "voice", {{
            set(){{}}, get(){{return null}}, configurable: true
        }});
        if ({"true" if self.late else "false"}) setTimeout(() => {{
            window.__voices = window.__all;
            speechSynthesis.dispatchEvent(new Event("voiceschanged"));
        }}, 300);
        """)
        return page

    def __exit__(self, *exc):
        self.ctx.close()


def open_settings(page):
    """Open the Settings popover (secondary controls live in it); a no-op if already open."""
    page.wait_for_selector("#btn-settings", timeout=5000)
    page.evaluate("() => { if (document.getElementById('settings-pop').hidden) document.getElementById('btn-settings').click(); }")


def focus_off(page):
    """Switch focus mode (#45, on by default) off for a check that isn't about it.

    Focus mode hides sections not reached yet and makes Play read only the current
    section. Call on a page of this origin before loading the viewer; undo with
    focus_default(page).
    """
    page.evaluate("() => localStorage.setItem('classroom.focus', 'false')")


def focus_default(page):
    page.evaluate("() => localStorage.removeItem('classroom.focus')")


def open_viewer(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    open_settings(page)
    page.wait_for_selector("button:has-text('Test voice')", timeout=5000)


def check_natural_voice_auto_selected(page, fx, base):
    """The natural voice is picked on first load, even when voices arrive late."""
    for late in (False, True):
        with voice_page(page, VOICES_NATURAL, late=late) as p:
            open_viewer(p, fx, base)
            p.wait_for_function("() => document.querySelectorAll('#voice option').length === 3", timeout=5000)
            p.wait_for_function(f"() => document.querySelector('#voice').value === {json.dumps(ARIA)}", timeout=5000)
            assert p.locator("#edge-tip").count() == 0, "tip must not show when a natural voice exists"


def check_chosen_voice_is_kept(page, fx, base):
    """A voice the learner picked survives a reload; the natural one does not override it."""
    with voice_page(page, VOICES_NATURAL) as p:
        open_viewer(p, fx, base)
        p.wait_for_function(f"() => document.querySelector('#voice').value === {json.dumps(ARIA)}", timeout=5000)
        p.select_option("#voice", DAVID)
        p.reload()
        open_viewer(p, fx, base)
        p.wait_for_function("() => document.querySelectorAll('#voice option').length === 3", timeout=5000)
        p.wait_for_timeout(500)
        assert p.input_value("#voice") == DAVID, f"learner's voice was replaced by {p.input_value('#voice')}"


def check_edge_tip_shown_once(page, fx, base):
    """With no natural voice the Edge tip shows, and stays gone once dismissed."""
    with voice_page(page, VOICES_ROBOTIC, late=True) as p:
        open_viewer(p, fx, base)
        p.wait_for_selector("#edge-tip", timeout=5000)
        assert "Microsoft Edge" in p.inner_text("#edge-tip")
        assert p.locator("#settings-body #edge-tip").count() == 1, "the tip lives inside Settings, not over the lesson"
        assert p.locator("#settings-body #edge-tip").count() == 1, "the tip lives inside Settings, not over the lesson"
        p.click("#edge-tip button")
        assert p.locator("#edge-tip").count() == 0
        p.reload()
        open_viewer(p, fx, base)
        p.wait_for_function("() => document.querySelectorAll('#voice option').length === 2", timeout=5000)
        p.wait_for_timeout(800)
        assert p.locator("#edge-tip").count() == 0, "tip came back after dismissal"


def check_voices_arriving_in_stages(page, fx, base):
    """Local voices first, the natural one in a later voiceschanged: it is still picked, and the tip goes."""
    with voice_page(page, VOICES_ROBOTIC) as p:
        p.add_init_script(f"setTimeout(() => {{ window.__voices = {json.dumps(VOICES_NATURAL)};"
                          " speechSynthesis.dispatchEvent(new Event('voiceschanged')); }, 600);")
        open_viewer(p, fx, base)
        p.wait_for_function(f"() => document.querySelector('#voice').value === {json.dumps(ARIA)}", timeout=5000)
        assert p.locator("#edge-tip").count() == 0, "tip must go once a natural voice arrives"


def check_saved_voice_from_before_is_kept(page, fx, base):
    """A voice saved by the picker before this feature existed counts as the learner's choice."""
    with voice_page(page, VOICES_NATURAL) as p:
        p.add_init_script(f"localStorage.setItem('classroom.voice', {json.dumps(DAVID)});")
        open_viewer(p, fx, base)
        p.wait_for_timeout(500)
        assert p.evaluate("() => Classroom.settings.voice") == DAVID


def check_test_voice_does_not_cut_the_lesson(page, fx, base):
    """Test voice while a lesson is being read keeps the lesson queue."""
    with voice_page(page, VOICES_NATURAL) as p:
        open_viewer(p, fx, base)
        p.evaluate("""() => Classroom.setEngine({ speak(item, done) { window.__held = done; window.__said = (window.__said || []).concat(item.text); },
                                                  cancel() {}, pause() {}, resume() {}, paused() { return false; } })""")
        p.evaluate("() => Classroom.enqueue([{ text: 'one' }, { text: 'two' }])")
        p.click("button:has-text('Test voice')")
        p.evaluate("() => window.__held()")  # finish 'one'
        assert p.evaluate("() => window.__said") == ["one", "two"], p.evaluate("() => window.__said")


def check_test_voice_button_works(page, fx, base):
    """The Test voice button speaks one short sample sentence."""
    with voice_page(page, VOICES_NATURAL) as p:
        open_viewer(p, fx, base)
        p.click("button:has-text('Test voice')")
        p.wait_for_function("() => window.__spoken.some(s => s.includes('sample'))", timeout=5000)


def check_spaced_review_badge(page, fx, base):
    """Badge counts concepts whose fsrs.due has passed; hidden at zero or with no file."""
    f = ROOT / "progress" / "concepts.json"
    original = f.read_bytes() if f.exists() else None
    now = datetime.datetime.now(datetime.timezone.utc)

    def concept(due):
        return {"title": "t", "lesson": "l", "attempts": [], "fsrs": None if due is None else {"due": due.isoformat()}}

    fixture = {"version": 1, "concepts": {
        "past-a": concept(now - datetime.timedelta(days=2)),
        "past-b": concept(now - datetime.timedelta(minutes=1)),
        "future": concept(now + datetime.timedelta(days=2)),
        "unscheduled": concept(None),
    }}
    try:
        f.write_text(json.dumps(fixture), encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_function("() => document.getElementById('review-badge')?.textContent === '2 due'", timeout=8000)
        assert page.locator("#review-badge").is_visible()

        f.write_text(json.dumps({"version": 1, "concepts": {"future": concept(now + datetime.timedelta(days=2))}}), encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_function("() => document.getElementById('review-badge')?.textContent === '0 due'", timeout=8000)
        assert not page.locator("#review-badge").is_visible()

        f.unlink()
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_function("() => document.getElementById('review-badge')?.hidden === true", timeout=8000)
        assert not page.locator("#review-badge").is_visible()
    finally:
        if original is None:
            f.unlink(missing_ok=True)
        else:
            f.write_bytes(original)


def check_two_voice_dialogue(page, fx, base):
    slug = fx.slug + "-dialogue"
    d = ROOT / "lessons" / slug
    d.mkdir(parents=True)
    try:
        (d / "lesson.md").write_text(
            "# Dialogue\n\n## Why\n\n**Teacher:** Why does it fail? Think about it.\n\n"
            "**Student:** Is it the cache?\n\nPlain narration stays plain.\n", encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".dialogue.student", timeout=15000)
        items = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section'))"
                              ".map(i => [i.voice || null, i.pitch || null, i.text])")
        assert items == [[None, None, "Why"], ["A", None, "Why does it fail?"], ["A", None, "Think about it."],
                         ["B", 1.3, "Is it the cache?"], [None, None, "Plain narration stays plain."]], items
        assert page.locator("#student-voice").count() == 1
        # The default engine gives role B a different pitch, or a different voice when one is picked.
        out = page.evaluate("""() => {
            const seen = [];
            const real = speechSynthesis.speak, getVoices = speechSynthesis.getVoices, Utt = window.SpeechSynthesisUtterance;
            window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; this.pitch = 1; } };
            speechSynthesis.speak = (u) => seen.push({ pitch: u.pitch, voice: u.voice && u.voice.name });
            speechSynthesis.getVoices = () => [{ name: "Teacher Voice" }, { name: "Student Voice" }];
            Classroom.settings.voice = "Teacher Voice";
            const noop = () => {};
            Classroom.defaultEngine.speak({ text: "a", voice: "A" }, noop);
            Classroom.defaultEngine.speak({ text: "b", voice: "B", pitch: 1.3 }, noop);
            Classroom.defaultEngine.speak({ text: "c", voice: "B", voiceName: "Student Voice" }, noop);
            speechSynthesis.speak = real; speechSynthesis.getVoices = getVoices; window.SpeechSynthesisUtterance = Utt;
            return seen;
        }""")
        assert out == [{"pitch": 1, "voice": "Teacher Voice"}, {"pitch": 1.3, "voice": "Teacher Voice"},
                       {"pitch": 1, "voice": "Student Voice"}], out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_dialogue_labels_hidden_and_plain_lessons_unchanged(page, fx, base):
    slug = fx.slug + "-dialogue2"
    d = ROOT / "lessons" / slug
    d.mkdir(parents=True)
    try:
        (d / "lesson.md").write_text("# D\n\n## S\n\n**Teacher:** Hello there.\n\n**Note:** not dialogue.\n", encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".dialogue.teacher", timeout=15000)
        texts = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section')).map(i => i.text)")
        assert texts == ["S", "Hello there.", "Note: not dialogue."], texts
        assert page.locator(".dialogue").count() == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # the fixture lesson has no dialogue: items carry no role or pitch
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector(".mermaid svg", timeout=15000)
    keys = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section')).map(i => Object.keys(i).sort().join())")
    assert set(keys) == {"el,text"}, keys


def check_dialogue_in_loose_list_read_once(page, fx, base):
    slug = fx.slug + "-dialogue3"
    d = ROOT / "lessons" / slug
    d.mkdir(parents=True)
    try:
        (d / "lesson.md").write_text(
            "# L" + chr(10)*2 + "## S" + chr(10)*2 + "1. **Teacher:** loose item." + chr(10)*2 + "2. **Student:** loose two." + chr(10), encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".dialogue.student", timeout=15000)
        items = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section'))"
                              ".map(i => [i.voice || null, i.text])")
        assert items == [[None, "S"], ["A", "loose item."], ["B", "loose two."]], items
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_progress_map(page, fx, base):
    """The Progress panel colours the lesson's concepts by mastery and follows the file."""
    store = ROOT / "progress" / "concepts.json"
    original = store.read_bytes() if store.exists() else None

    def concept(title, requires, mastery, attempts):
        return {"title": title, "lesson": fx.slug, "requires": requires, "mastery": mastery,
                "added": "2026-10-01T00:00:00Z", "fsrs": None, "misconceptions": [],
                "attempts": [{"at": "2026-10-01T00:00:00Z", "kind": "quiz", "correct": True,
                              "confidence": None, "note": None}] * attempts}

    def write(concepts):
        text = json.dumps({"version": 1, "concepts": concepts}, indent=2, sort_keys=True)
        store.write_text(text + "\n", encoding="utf-8")

    def states():
        return page.evaluate(r"""() => Object.fromEntries(
            [...document.querySelectorAll('#progress-panel g.node')].map(n => [
                n.textContent.trim().replace(/\s*(✓|🔒|\(\d+%\))$/u, ''),
                ['new', 'learning', 'mastered', 'locked'].find(c => n.classList.contains(c))]))""")

    try:
        write({
            "base": concept("Base idea", [], 0.9, 3),
            "mid": concept("Middle idea", ["base"], 0.4, 2),
            "top": concept("Top idea", ["mid"], 0, 0),
            "fresh": concept("Fresh idea", ["base"], 0, 0),
            "elsewhere": {**concept("Other lesson", [], 1.0, 1), "lesson": "some-other-lesson"},
        })
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("#btn-progress")
        assert page.locator("#progress-panel").is_hidden(), "panel should start closed"
        page.click("#btn-progress")
        page.wait_for_selector("#progress-panel g.node", timeout=15000)
        got = states()
        assert got == {"Base idea": "mastered", "Middle idea": "learning", "Top idea": "locked",
                       "Fresh idea": "new"}, got
        assert page.locator("#progress-panel .flowchart-link").count() == 3

        # The poller picks up a change to the file: mastering Middle unlocks Top.
        write({
            "base": concept("Base idea", [], 0.9, 3),
            "mid": concept("Middle idea", ["base"], 0.85, 3),
            "top": concept("Top idea", ["mid"], 0, 0),
            "fresh": concept("Fresh idea", ["base"], 0, 0),
        })
        page.wait_for_function("() => document.querySelectorAll('#progress-panel g.node.mastered').length === 2",
                               timeout=8000)
        got = states()
        assert got["Top idea"] == "new" and got["Middle idea"] == "mastered", got
        page.click("#btn-progress")
        assert page.locator("#progress-panel").is_hidden(), "toggle should close the panel"
    finally:
        if original is None:
            store.unlink(missing_ok=True)
        else:
            store.write_bytes(original)


def check_progress_map_odd_titles(page, fx, base):
    """Titles with mermaid syntax characters (backticks, quotes, brackets) still draw."""
    store = ROOT / "progress" / "concepts.json"
    original = store.read_bytes() if store.exists() else None
    odd = {"a": "`x` is a variable", "b": 'say "hi"', "c": "list[0] (first)", "d": "end"}
    try:
        store.write_text(json.dumps({"version": 1, "concepts": {
            k: {"title": t, "lesson": fx.slug, "requires": [], "mastery": 0, "attempts": [], "fsrs": None,
                "added": "2026-10-01T00:00:00Z", "misconceptions": []} for k, t in odd.items()}}), encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("#btn-progress")
        if page.locator("#progress-panel").is_hidden():
            page.click("#btn-progress")
        page.wait_for_function("() => document.querySelectorAll('#progress-panel g.node').length === 4 && document.querySelector('.mermaid svg .edgeLabel')", timeout=15000)
        labels = page.evaluate("() => [...document.querySelectorAll('#progress-panel g.node')].map(n => n.textContent)")
        assert any("x" in l and "variable" in l for l in labels), labels
        assert not any("Unsupported" in l for l in labels), labels
    finally:
        if original is None:
            store.unlink(missing_ok=True)
        else:
            store.write_bytes(original)


# Kokoro stand-in: no model is downloaded. generate() takes 30 ms; a fake
# AudioContext "plays" each clip for 150 ms and logs every step to __kk.
KOKORO_STUB = """
export class KokoroTTS {
  static async from_pretrained(id, opts) {
    window.__kk.push("load:" + opts.device);
    await new Promise(r => setTimeout(r, window.__kkLoadDelay || 0));
    if (window.__kkFailLoad) throw new Error("no network");
    return new KokoroTTS();
  }
  async generate(text, opts) {
    window.__kk.push("gen:" + text + ":" + opts.voice);
    await new Promise(r => setTimeout(r, window.__kkDelay || 30));
    if (window.__kkFailGen) throw new Error("synthesis broke");
    const audio = new Float32Array(4); audio.text = text;
    return { audio, sampling_rate: 24000 };
  }
}
"""


FAKE_AUDIO = """
window.__kk = [];
window.AudioContext = class {
  constructor() { this.state = "running"; this.destination = {}; this.live = new Set(); }
  createBuffer(ch, n, rate) { return { copyToChannel(a) { this.text = a.text; } }; }
  createBufferSource() {
    const ctx = this;
    return { connect() {}, onended: null, buffer: null, left: 150,
      arm() { this.t0 = Date.now(); this.timer = setTimeout(() => this.finish(), this.left); },
      finish() { const t = this.buffer.text; ctx.live.delete(this); window.__kk.push("end:" + t); this.onended && this.onended(); },
      start() { window.__kk.push("play:" + this.buffer.text); ctx.live.add(this); if (ctx.state === "running") this.arm(); },
      stop() { clearTimeout(this.timer); ctx.live.delete(this); window.__kk.push("stop:" + this.buffer.text); } };
  }
  async suspend() { this.state = "suspended"; for (const s of this.live) { clearTimeout(s.timer); s.left -= Date.now() - s.t0; } }
  async resume() { this.state = "running"; for (const s of this.live) s.arm(); }
};
"""


def kokoro_page(page, base, fx, fail_load=False, fail_gen=False, delay=30, load_delay=0):
    """Fresh page load with the Kokoro module and audio faked, the toggle still off."""
    page.unroute("**/kokoro-js@*/+esm")
    page.route("**/kokoro-js@*/+esm", lambda r: r.fulfill(status=200, content_type="text/javascript", body=KOKORO_STUB))
    page.add_init_script(FAKE_AUDIO + f"window.__kkFailLoad = {str(fail_load).lower()}; window.__kkFailGen = {str(fail_gen).lower()}; window.__kkDelay = {delay}; window.__kkLoadDelay = {load_delay};")
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.evaluate("() => { localStorage.removeItem('classroom.kokoro'); }")
    page.reload()
    open_settings(page)
    page.wait_for_selector("#kk-toggle")
    page.evaluate("() => { window.__spoken.length = 0; }")


def kk_click(page):
    open_settings(page)  # a click elsewhere closes the popover
    page.click("#kk-toggle")


def kokoro_on(page):
    kk_click(page)
    page.wait_for_function("() => document.getElementById('kk-status').textContent.includes('ready')", timeout=5000)


def check_kokoro_engine_order_and_prefetch(page, fx, base):
    kokoro_page(page, base, fx)
    assert not page.is_checked("#kk-toggle"), "offline voice must be off by default"
    kokoro_on(page)
    assert page.evaluate("() => __kk[0]") == "load:wasm", page.evaluate("() => __kk")  # headless has no WebGPU
    page.evaluate("""() => Classroom.enqueue([{text: "one", voice: "A"}, {text: "two", voice: "B"}, {text: "three"}])""")
    page.wait_for_function("() => __kk.includes('end:three')", timeout=5000)
    kk = page.evaluate("() => __kk")
    plays = [e for e in kk if e.startswith("play:")]
    assert plays == ["play:one", "play:two", "play:three"], kk
    assert "gen:one:af_heart" in kk and "gen:two:am_michael" in kk and "gen:three:af_heart" in kk, kk
    assert kk.index("gen:two:am_michael") < kk.index("end:one"), f"next item not prefetched: {kk}"
    assert page.evaluate("() => window.__spoken") == [], "browser engine must stay silent"
    kk_click(page)  # back to the browser engine
    page.evaluate("() => Classroom.enqueue([{text: 'plain'}])")
    page.wait_for_function("() => window.__spoken.includes('plain')", timeout=3000)


def check_kokoro_cancel_stops_playback(page, fx, base):
    kokoro_page(page, base, fx)
    kokoro_on(page)
    page.evaluate("() => Classroom.enqueue([{text: 'long one'}, {text: 'long two'}])")
    page.wait_for_function("() => __kk.includes('play:long one')", timeout=3000)
    page.evaluate("() => Classroom.stop()")
    page.wait_for_timeout(400)
    kk = page.evaluate("() => __kk")
    assert "stop:long one" in kk and "end:long one" not in kk, kk
    assert "play:long two" not in kk, kk


def check_kokoro_load_failure_falls_back(page, fx, base):
    kokoro_page(page, base, fx, fail_load=True)
    kk_click(page)
    page.wait_for_function("() => document.getElementById('kk-status').textContent.includes('failed')", timeout=5000)
    assert not page.is_checked("#kk-toggle")
    page.evaluate("() => Classroom.enqueue([{text: 'still audible'}])")
    page.wait_for_function("() => window.__spoken.includes('still audible')", timeout=3000)


def check_kokoro_synthesis_failure_falls_back(page, fx, base):
    kokoro_page(page, base, fx, fail_gen=True)
    kokoro_on(page)
    page.evaluate("() => Classroom.enqueue([{text: 'first'}, {text: 'second'}])")
    page.wait_for_function("() => window.__spoken.includes('first') && window.__spoken.includes('second')", timeout=5000)
    assert "failed" in page.inner_text("#kk-status")
    page.evaluate("() => { delete window.__kkFailGen; }")


def check_kokoro_pause_while_generating(page, fx, base):
    kokoro_page(page, base, fx, delay=300)
    kokoro_on(page)
    page.evaluate("() => Classroom.enqueue([{text: 'a'}, {text: 'b'}])")
    page.wait_for_timeout(100)
    page.click("#btn-pause")
    page.wait_for_timeout(700)
    kk = page.evaluate("() => __kk")
    assert not any(e.startswith("play:") for e in kk), f"played while paused: {kk}"
    page.click("#btn-play")  # resume, because paused() must be true (otherwise this restarts the lesson)
    page.wait_for_function("() => __kk.includes('end:b')", timeout=5000)
    kk = page.evaluate("() => __kk")
    assert [e for e in kk if e.startswith("play:")] == ["play:a", "play:b"], kk
    assert not any(e.startswith("stop:") for e in kk), kk


def check_kokoro_pause_resume_during_playback(page, fx, base):
    kokoro_page(page, base, fx)
    kokoro_on(page)
    page.evaluate("() => Classroom.enqueue([{text: 'a'}, {text: 'b'}])")
    page.wait_for_function("() => __kk.includes('play:a')", timeout=3000)
    page.click("#btn-pause")
    page.wait_for_timeout(500)
    kk = page.evaluate("() => __kk")
    assert "end:a" not in kk, f"kept playing while paused: {kk}"
    page.click("#btn-play")
    page.wait_for_function("() => __kk.includes('end:b')", timeout=5000)
    kk = page.evaluate("() => __kk")
    assert kk.index("end:a") < kk.index("play:b"), kk


def check_kokoro_toggle_off_keeps_reading(page, fx, base):
    kokoro_page(page, base, fx)
    kokoro_on(page)
    page.evaluate("() => Classroom.enqueue([{text: 'one'}, {text: 'two'}, {text: 'three'}])")
    page.wait_for_function("() => __kk.includes('play:one')", timeout=3000)
    kk_click(page)
    page.wait_for_function("() => ['one', 'two', 'three'].every(t => window.__spoken.includes(t))", timeout=3000)


def check_kokoro_late_load_keeps_reading(page, fx, base):
    kokoro_page(page, base, fx, load_delay=400)
    page.evaluate("() => localStorage.setItem('classroom.kokoro', 'true')")
    page.reload()
    open_settings(page)
    page.wait_for_selector("#kk-toggle")
    texts = [f"s{i}" for i in range(150)]
    page.evaluate("(t) => Classroom.enqueue(t.map(text => ({text})))", texts)
    page.wait_for_function("() => document.getElementById('kk-status').textContent.includes('ready')", timeout=5000)
    page.wait_for_function("() => __kk.filter(e => e.startsWith('end:')).length > 0", timeout=5000)
    page.wait_for_timeout(300)
    spoken = set(page.evaluate("() => window.__spoken"))
    done = {e[5:] for e in page.evaluate("() => __kk") if e.startswith("play:")}
    assert spoken, "browser voice never read before the model loaded"
    assert done, "the model never took over"
    assert (spoken | done) >= set(texts[:len(spoken) + len(done) - 1]), "queue was dropped on engine switch"
    page.evaluate("() => { localStorage.removeItem('classroom.kokoro'); Classroom.stop(); }")


import re


# Page answers (channel/): the channel serves the viewer itself, so the page
# calls /status and /answer on its own origin. Here those two paths are stubbed
# with page.route on the test server; no channel server runs.
CHANNEL_TOKEN = "t" * 43


def channel_quiz(fx, quiz_id):
    """Append a quiz the way channel/server.js's ask_quiz tool writes it."""
    entry = {"id": f"chquiz:{quiz_id}", "kind": "quiz", "via": "channel", "quiz_id": quiz_id,
             "questions": [{"question": f"Quiz {quiz_id}: what does DNS return?", "header": "Check",
                            "options": [{"label": "An IP address"}, {"label": "A MAC address"}]}]}
    with open(fx.dir / "chat.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def key_stored(page):
    return page.evaluate("""() => [sessionStorage, localStorage].some(s =>
        Object.keys(s).some(k => (s.getItem(k) || '').includes('%s')))""" % CHANNEL_TOKEN)


def check_page_answers_hidden_without_channel(page, fx, base):
    hits = []
    api = re.compile(r"/(status|answer)$")
    page.route(api, lambda route: (hits.append(route.request.method), route.abort()))
    try:
        # Opened the ordinary way: no key, so the viewer never calls the channel.
        page.goto(f"{base}/viewer/?lesson={fx.slug}&v=plain")
        channel_quiz(fx, "q-plain")
        page.wait_for_selector('.quiz-q[data-question^="Quiz q-plain"]', timeout=8000)
        assert hits == [], hits
        assert page.locator(".page-answer").count() == 0

        # Opened from the channel's link, but the channel is unreachable.
        page.goto(f"{base}/viewer/?lesson={fx.slug}&v=down#ch={CHANNEL_TOKEN}")
        assert "#ch=" not in page.url, page.url
        channel_quiz(fx, "q-down")
        page.wait_for_selector('.msg.quiz[data-quiz-id="q-down"]', timeout=8000)
        for _ in range(50):  # wait for the status probe that finds the channel down
            if hits:
                break
            page.wait_for_timeout(100)
        assert hits, "expected a status probe"
        page.wait_for_timeout(200)
        assert page.locator(".page-answer").count() == 0
        assert page.locator('.msg.quiz[data-quiz-id="q-down"] .answer-here').is_visible()
        # The key is kept in memory only: not in the URL, not in storage.
        assert not key_stored(page), "key leaked into web storage"
    finally:
        page.unroute(api)


def check_page_answers_click_posts_answer(page, fx, base):
    posts = []
    state = {"received": None}
    api = re.compile(r"/(status|answer)$")

    def stub(route):
        req = route.request
        if req.method == "GET" and req.url.endswith("/status"):
            return route.fulfill(status=200, json={"ok": True, "pending": None if posts else "q-live",
                                                   "received": state["received"]})
        if req.method == "POST" and req.url.endswith("/answer"):
            posts.append({"headers": req.headers, "body": json.loads(req.post_data)})
            return route.fulfill(status=200, json={"ok": True})
        return route.fulfill(status=404, body="")

    page.route(api, stub)
    try:
        channel_quiz(fx, "q-live")
        page.goto(f"{base}/viewer/?lesson={fx.slug}&v=live#ch={CHANNEL_TOKEN}")
        live = '.msg.quiz[data-quiz-id="q-live"] .page-answer'
        page.wait_for_selector(f"{live} button:text-is('A MAC address')", timeout=8000)
        # Only the quiz the channel is waiting for gets controls.
        assert page.locator(".page-answer").count() == 1
        assert not page.locator('.msg.quiz[data-quiz-id="q-live"] .answer-here').is_visible()
        page.click(f"{live} button:text-is('A MAC address')")
        note = "document.querySelector('.page-answer .note').textContent"
        # Sent is not the same as delivered: the page says so until Claude confirms.
        page.wait_for_function(f"() => {note}.startsWith('Sent to Claude.') && {note}.includes('terminal')", timeout=5000)
        assert len(posts) == 1, posts
        assert posts[0]["headers"].get("x-classroom-token") == CHANNEL_TOKEN, posts[0]["headers"]
        assert posts[0]["body"] == {"quiz_id": "q-live", "answer": "A MAC address", "answer_type": "option"}, posts
        state["received"] = "q-live"  # Claude called got_answer
        page.wait_for_function(f"() => {note} === 'Claude got your answer.'", timeout=5000)
        assert not key_stored(page), "key leaked into web storage"
    finally:
        page.unroute(api)


VISUALS_LESSON = (
    "# Visuals\n\n## Flow\n\nThe load balancer sends traffic to the web server.\n\n"
    "```mermaid\ngraph LR\n  %% animate\n  A[Load Balancer] --> B(Web Server)\n  B --> C[(Database)]\n```\n\n"
    "```mermaid\ngraph LR\n  X[Plain] --> Y[Static]\n```\n\n"
    '```explorable\n{"vars": {"n": {"min": 1, "max": 10, "value": 3}}, "show": "n squared is {n*n}"}\n```\n'
)


HOLD_ENGINE = """() => {
  Classroom.setEngine({ speak(item, done) { window.__done = done; }, cancel() {}, pause() {}, resume() {}, paused() { return false; } });
}"""


def check_animated_visuals(page, fx, base):
    slug = fx.slug + "-visuals"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(VISUALS_LESSON, encoding="utf-8")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelectorAll('.mermaid svg').length === 2 && document.querySelector('.explorable')", timeout=15000)

        # animate class: only the diagram with "%% animate", and the CSS really animates the edges
        classes = page.evaluate("() => [...document.querySelectorAll('.mermaid')].map(d => d.classList.contains('animated'))")
        assert classes == [True, False], classes
        edge = ".mermaid.animated path.flowchart-link"
        assert page.evaluate(f"() => getComputedStyle(document.querySelector('{edge}')).animationName") == "classroom-flow"
        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate(f"() => getComputedStyle(document.querySelector('{edge}')).animationName") == "none"
        page.emulate_media(reduced_motion="no-preference")

        # highlight follows the narration, longest label first, and clears on idle
        page.evaluate(HOLD_ENGINE)
        def narrated():
            return page.evaluate("() => [...document.querySelectorAll('g.node.narrated')].map(n => n.textContent.trim())")
        page.evaluate("() => Classroom.enqueue([{ el: document.querySelector('main section p'), text: 'The load balancer sends traffic to the web server.' }])")
        assert sorted(narrated()) == ["Load Balancer", "Web Server"], narrated()
        page.evaluate("() => { const d = window.__done; window.__done = null; d(); }")  # ends the item -> idle
        assert narrated() == [], narrated()
        page.evaluate("() => Classroom.enqueue([{ el: document.querySelector('main section p'), text: 'Servers are not web servers, but the DATABASE is.' }])")
        assert narrated() == ["Database"], narrated()  # "Server" alone is not a node; whole words only
        page.evaluate("() => { Classroom.stop(); Classroom.setEngine(null); }")

        # explorables: sliders update the text, and the reader skips them
        show = page.locator(".explorable-show")
        assert show.inner_text() == "n squared is 9", show.inner_text()
        page.locator(".explorable input").evaluate("(el) => { el.value = 7; el.dispatchEvent(new Event('input', { bubbles: true })); }")
        assert show.inner_text() == "n squared is 49", show.inner_text()
        read = page.evaluate("() => Classroom.itemsFor(document.querySelector('main section')).map(i => i.text).join(' ')")
        assert "squared" not in read, read
    finally:
        page.evaluate("() => { Classroom.stop(); Classroom.setEngine(null); }")
        shutil.rmtree(d, ignore_errors=True)


def check_animated_visuals_edge_cases(page, fx, base):
    # A list-nested (indented) "%% animate" diagram must still pair up, and an
    # out-of-range explorable value is clamped so text and slider agree.
    slug = fx.slug + "-visuals2"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(
        "# Edges\n\n## Nested\n\n- A step:\n\n  ```mermaid\n  %% animate\n  graph LR\n    A --> B\n  ```\n\n"
        "~~~mermaid\ngraph LR\n  C --> D\n~~~\n\n"
        '```explorable\n{"vars": {"n": {"min": 1, "max": 10, "value": 50}}, "show": "n is {n}"}\n```\n', encoding="utf-8")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelectorAll('.mermaid svg').length === 2 && document.querySelector('.explorable')", timeout=15000)
        classes = page.evaluate("() => [...document.querySelectorAll('.mermaid')].map(d => d.classList.contains('animated'))")
        assert classes == [True, False], classes
        assert page.locator(".explorable-show").inner_text() == "n is 10", page.locator(".explorable-show").inner_text()
        assert page.locator(".explorable input").input_value() == "10"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_safe_evaluator(page, fx, base):
    page.goto(f"{base}/viewer/")
    page.wait_for_function("() => window.Classroom && Classroom.visuals", timeout=8000)
    run = """([expr, vars]) => { try { return { ok: Classroom.visuals.evaluate(expr, vars) }; } catch (e) { return { err: String(e.message) }; } }"""
    good = {
        "n*n": 9, "n ** 2 + 1": 10, "(n + 1) * 2 - 1": 7, "n / 2": 1.5, "n % 2": 1, "-2 ** 2": -4,
        "2 ** 3 ** 2": 512, "Math.round(2.6)": 3, "min(n, 2)": 2, "max(1, n, 2)": 3, "sqrt(16)": 4, "  n  ": 3,
    }
    for expr, want in good.items():
        got = page.evaluate(run, [expr, {"n": 3}])
        assert got == {"ok": want}, (expr, got)
    page.evaluate("() => { window.__pwned = 0; }")
    hostile = [
        "alert(1)", "constructor", "n.constructor", "__proto__", "toString", "this", "window", "globalThis",
        "Math.round.constructor", "Math.floor(1)", "Math.round", "Math", "(n)(1)", "n; 1", "1 +", "()", "min()",
        "sqrt(1, 2)", "'a'", "`x`", "[1]", "n = 5", "n++", "a b", "1e3", "eval('1')", "Function('1')()",
        "fetch('x')", "(()=>1)()", "n ? 1 : 2", "n.x", "x", "min(1,", "((((" * 20 + "1" + "))))" * 20, "1" + "+1" * 200,
    ]
    for expr in hostile:
        got = page.evaluate(run, [expr, {"n": 3}])
        assert "err" in got, (expr, got)
    assert page.evaluate("() => window.__pwned") == 0
    # Names inherited from Object.prototype never resolve as variables.
    assert "err" in page.evaluate(run, ["constructor", {}])


def check_index_resume_badges(page, fx, base):
    lessons = ROOT / "lessons"
    # "zz" sorts first (newest-first), so the finished lesson would lead without the reordering.
    done = lessons / f"_test-{fx.slug[6:]}-zz-done"
    open_ = lessons / f"_test-{fx.slug[6:]}-aa-open"
    pct = lessons / f"_test-{fx.slug[6:]}-mm-100%-open"  # a "%" in the name must not break decoding
    try:
        for d, body in ((done, "\n## Recap\n\nDone.\n"), (open_, "\n## Part one\n\nMore to come.\n"),
                        (pct, "\n## Part one\n\nPercent.\n")):
            d.mkdir(parents=True)
            (d / "lesson.md").write_text("# Fixture\n" + body, encoding="utf-8")
        page.goto(f"{base}/viewer/")
        page.wait_for_function("() => document.querySelectorAll('.lesson-list .badge').length >= 3", timeout=8000)
        rows = page.evaluate("""() => [...document.querySelectorAll('.lesson-list li')].map(li =>
            [new URL(li.querySelector('a').href).searchParams.get('lesson'), li.querySelector('.badge')?.textContent])""")
        state = dict(rows)
        assert state[done.name] == "finished", rows
        assert state[open_.name] == "in progress", rows
        assert state[pct.name] == "in progress", rows
        names = [n for n, _ in rows]
        assert names.index(open_.name) < names.index(done.name), rows
        badges = [b for _, b in rows]
        assert badges == sorted(badges, key=lambda b: b != "in progress"), rows
    finally:
        shutil.rmtree(done, ignore_errors=True)
        shutil.rmtree(open_, ignore_errors=True)
        shutil.rmtree(pct, ignore_errors=True)


def check_python_runs_and_saves(page, fx, base):
    """`python run` blocks get an editor; Run executes in Pyodide; Save writes the file back."""
    slug = f"{fx.slug}-py"
    lesson = ROOT / "lessons" / slug
    shutil.rmtree(lesson, ignore_errors=True)
    (lesson / "practice").mkdir(parents=True)
    try:
        (lesson / "practice" / "t.py").write_text("print(1+1)\n", encoding="utf-8")
        (lesson / "lesson.md").write_text(
            "# Py\n\n## Try it\n\nRun this.\n\n```python run file=practice/t.py\nprint('placeholder')\n```\n\n"
            "```python\nprint('plain block')\n```\n", encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".py-run textarea", timeout=15000)
        assert page.locator(".py-run").count() == 1, "only the `python run` block becomes an editor"
        assert "plain block" in page.locator("pre code.language-python").inner_text()
        page.wait_for_function("() => document.querySelector('.py-run textarea').value.includes('1+1')", timeout=5000)
        page.click(".py-btn-run")
        # First Run downloads Pyodide, so allow a long time.
        page.wait_for_function("() => document.querySelector('.py-out').textContent.trim() === '2'", timeout=180000)
        page.fill(".py-run textarea", "print('saved by the test')\n")
        page.click(".py-btn-save")
        page.wait_for_function("() => document.querySelector('.py-status').textContent === 'saved'", timeout=5000)
        assert (lesson / "practice" / "t.py").read_text(encoding="utf-8") == "print('saved by the test')\n"
        page.click(".py-btn-run")
        page.wait_for_function("() => document.querySelector('.py-out').textContent.includes('saved by the test')", timeout=30000)
        page.fill(".py-run textarea", "1/0\n")
        page.click(".py-btn-run")
        page.wait_for_function("() => document.querySelector('.py-out').textContent.includes('ZeroDivisionError')", timeout=30000)
    finally:
        shutil.rmtree(lesson, ignore_errors=True)


# --- 15 chat reading ---


def _open_chat(page, fx, base, extra=""):
    page.goto(f"{base}/viewer/?lesson={fx.slug}{extra}")
    page.wait_for_selector(".msg.quiz", timeout=10000)


def check_chat_full_toggle(page, fx, base):
    _open_chat(page, fx, base)
    try:
        side = page.evaluate("() => document.querySelector('aside').getBoundingClientRect().width")
        assert page.locator("main").is_visible()
        side_msg = page.evaluate("() => document.querySelector('.msg.claude').getBoundingClientRect().width")
        page.click("#btn-chat-full")
        assert not page.locator("main").is_visible(), "full mode hides the lesson"
        full = page.evaluate("() => document.querySelector('aside').getBoundingClientRect().width")
        assert full > side + 200, (side, full)
        assert page.evaluate("() => getComputedStyle(document.querySelector('aside')).position") == "static"
        msg_w, fs, ch70 = page.evaluate("""() => {
            const m = document.querySelector('.msg.claude');
            const probe = document.createElement('div');
            probe.style.cssText = 'width:70ch;position:absolute;visibility:hidden';
            m.appendChild(probe);
            const w = probe.getBoundingClientRect().width;
            probe.remove();
            return [m.getBoundingClientRect().width, parseFloat(getComputedStyle(m).fontSize), w];
        }""")
        assert 19 <= fs <= 21, fs
        assert ch70 < msg_w <= 1200, (msg_w, ch70)  # wide and short, capped at 1200px
        lh = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.msg.claude')).lineHeight)")
        assert lh <= fs * 1.6, (lh, fs)
        assert msg_w > side_msg, (msg_w, side_msg)
        page.reload()
        page.wait_for_selector(".msg.quiz", timeout=10000)
        assert not page.locator("main").is_visible(), "full mode is remembered across reload"
        page.click("#btn-chat-full")
        assert page.locator("main").is_visible()
        for tag in ("input", "textarea"):
            page.evaluate("(tag) => { const e = document.createElement(tag); e.id = 'typing'; document.body.appendChild(e); e.focus(); }", tag)
            page.keyboard.press("c")
            assert page.locator("main").is_visible(), f"c typed in a {tag} must not toggle"
            page.evaluate("() => document.getElementById('typing').remove()")
        page.keyboard.press("c")
        assert not page.locator("main").is_visible(), "c toggles full mode"
        page.keyboard.press("c")
        assert page.locator("main").is_visible()
        back = page.evaluate("() => document.querySelector('aside').getBoundingClientRect().width")
        assert back == side, (back, side)
    finally:
        page.evaluate("() => localStorage.removeItem('classroom.chatFull')")


def check_chat_view_param(page, fx, base):
    _open_chat(page, fx, base, "&view=chat")
    assert not page.locator("main").is_visible()
    assert page.locator("aside").is_visible()
    assert page.locator("#btn-chat-full").count() == 0
    assert page.locator("#chat-log .msg").count() >= 3
    _open_chat(page, fx, base)
    href = page.get_attribute("#link-chat-tab", "href")
    assert href.endswith("&view=chat") and f"lesson={fx.slug}" in href, href
    assert page.locator("main").is_visible()


def check_chat_reread(page, fx, base):
    _open_chat(page, fx, base)
    assert page.locator(".msg.you .reread").count() == 0, "no re-read on the learner's own messages"
    assert page.locator(".msg.claude .reread").count() == 2
    page.evaluate("() => { window.__spoken.length = 0; }")
    page.click(".msg.quiz .reread")
    page.wait_for_function("() => window.__spoken.includes('Option 1: An IP address.')", timeout=5000)
    page.evaluate("() => { window.__spoken.length = 0; }")
    page.click("#btn-read-chat")
    page.wait_for_function("() => window.__spoken.includes('DNS maps example.com to an IP address.')", timeout=5000)
    spoken = page.evaluate("window.__spoken")
    assert "Let's see what you already know." in spoken and "Option 1: An IP address." in spoken, spoken
    assert not any("Read" in s or "\U0001F50A" in s for s in spoken), spoken
    assert not any("teach me" in s for s in spoken), spoken


def check_chat_full_scrolls_to_new_messages(page, fx, base):
    chat = fx.dir / "chat.jsonl"
    original = chat.read_text(encoding="utf-8")
    _open_chat(page, fx, base)
    try:
        page.evaluate("() => { localStorage.setItem('classroom.chatFull', 'true'); }")
        page.reload()
        page.wait_for_selector(".msg.quiz", timeout=10000)
        page.evaluate("() => { const b = document.getElementById('auto-chat'); b.checked = false; b.dispatchEvent(new Event('change')); }")
        lines = [json.dumps({"kind": "claude", "text": f"Message {i}. " + "A long sentence to fill the page. " * 12}) for i in range(8)]
        with open(chat, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        page.wait_for_function("() => document.querySelectorAll('.msg.claude').length >= 10", timeout=8000)
        page.wait_for_timeout(300)
        assert page.evaluate("() => document.documentElement.scrollHeight > innerHeight + 300"), "page should overflow"
        bottom = page.evaluate("() => [...document.querySelectorAll('.msg')].pop().getBoundingClientRect().bottom")
        assert bottom <= page.evaluate("() => innerHeight") + 2, bottom
        assert page.evaluate("() => scrollY") > 0
        # Scrolled up to reread: a new message must not pull the page down.
        page.evaluate("() => scrollTo(0, 0)")
        page.wait_for_timeout(200)
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "claude", "text": "Late message."}) + "\n")
        page.wait_for_function("() => document.querySelectorAll('.msg.claude').length >= 11", timeout=8000)
        assert page.evaluate("() => scrollY") == 0, "must not yank a reader who scrolled up"
    finally:
        chat.write_text(original, encoding="utf-8")
        page.evaluate("() => { localStorage.removeItem('classroom.chatFull'); localStorage.removeItem('classroom.chat'); }")
        _open_chat(page, fx, base)


def check_chat_full_toggle_lands_on_newest(page, fx, base):
    chat = fx.dir / "chat.jsonl"
    original = chat.read_text(encoding="utf-8")
    try:
        lines = [json.dumps({"kind": "claude", "text": f"Message {i}. " + "A long sentence to fill the page. " * 12}) for i in range(8)]
        with open(chat, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _open_chat(page, fx, base)
        page.wait_for_function("() => document.querySelectorAll('.msg.claude').length >= 9", timeout=8000)
        page.evaluate("() => scrollTo(0, 0)")
        page.evaluate("() => document.getElementById('btn-chat-full').click()")
        page.wait_for_timeout(300)
        bottom = page.evaluate("() => [...document.querySelectorAll('.msg')].pop().getBoundingClientRect().bottom")
        assert 0 < bottom <= page.evaluate("() => innerHeight") + 2, f"newest message not in view: {bottom}"
    finally:
        chat.write_text(original, encoding="utf-8")
        page.evaluate("() => localStorage.removeItem('classroom.chatFull')")
        _open_chat(page, fx, base)


def check_chat_tab_takes_over_reading(page, fx, base):
    chat = fx.dir / "chat.jsonl"
    original = chat.read_text(encoding="utf-8")
    _open_chat(page, fx, base)
    # A popup shares the browser context, so BroadcastChannel reaches it.
    with page.expect_popup() as pop:
        page.evaluate("(u) => window.open(u, '_blank')", f"{base}/viewer/?lesson={fx.slug}&view=chat")
    other = pop.value
    try:
        other.wait_for_load_state()
        other.evaluate("() => {" + FAKE_SPEECH + "}")
        other.wait_for_selector(".msg.quiz", timeout=10000)
        assert other.evaluate("() => Classroom.settings.auto") is False, "chat tab never reads lesson sections"
        page.wait_for_function("() => Classroom.settings.chat === false", timeout=5000)
        assert page.evaluate("() => Classroom.settings.auto") is True
        page.evaluate("() => { window.__spoken.length = 0; }")
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "claude", "text": "Only the chat tab says this."}) + "\n")
        other.wait_for_function("() => window.__spoken.includes('Only the chat tab says this.')", timeout=8000)
        assert "Only the chat tab says this." not in page.evaluate("window.__spoken")
        # Re-read still works in the main tab.
        page.click(".msg.claude:last-of-type .reread", force=True)
        page.wait_for_function("() => window.__spoken.includes('Only the chat tab says this.')", timeout=5000)
        other.close(run_before_unload=True)
        page.wait_for_function("() => Classroom.settings.chat === true", timeout=9000)
    finally:
        if not other.is_closed():
            other.close()
        chat.write_text(original, encoding="utf-8")
        _open_chat(page, fx, base)


def check_new_section_banner_in_full_mode(page, fx, base):
    lesson = fx.dir / "lesson.md"
    original = lesson.read_text(encoding="utf-8")
    _open_chat(page, fx, base)
    try:
        page.evaluate("() => { localStorage.setItem('classroom.chatFull', 'true'); }")
        page.reload()
        page.wait_for_selector(".msg.quiz", timeout=10000)
        assert not page.locator("#chat-banner").is_visible()
        lesson.write_text(original + "\n## Banner idea\n\nA brand new section.\n", encoding="utf-8")
        page.wait_for_selector("#chat-banner", state="visible", timeout=8000)
        assert "New lesson section" in page.inner_text("#chat-banner")
        assert not page.locator("main").is_visible()
        page.click("#chat-banner button")
        assert page.locator("main").is_visible(), "Show lesson switches to side mode"
        page.wait_for_selector("main section:last-of-type h2", state="visible")
        assert "Banner idea" in page.inner_text("main section:last-of-type h2")
        assert not page.locator("#chat-banner").is_visible()
    finally:
        lesson.write_text(original, encoding="utf-8")
        page.evaluate("() => localStorage.removeItem('classroom.chatFull')")
        _open_chat(page, fx, base)


def check_easy_font_toggle(page, fx, base):
    _open_chat(page, fx, base)
    try:
        assert not page.evaluate("() => document.documentElement.classList.contains('easy-font')")
        page.evaluate("() => document.getElementById('easy-font').click()")  # the Edge tip can cover it
        assert page.evaluate("() => document.documentElement.classList.contains('easy-font')")
        page.reload()
        page.wait_for_selector(".msg.quiz", timeout=10000)
        assert page.is_checked("#easy-font")
    finally:
        page.evaluate("() => localStorage.removeItem('classroom.easyFont')")
        page.reload()
        page.wait_for_selector(".msg.quiz", timeout=10000)


# --- 16 diagram view ---


DIAGRAM_LESSON = """# Diagrams

## Map

```mermaid
graph TD
  A[Packets] --> B[IP address]
  B -->|asks| C[DNS lookup]
  C --> D[Caching]
  subgraph S[Group]
    D
  end
  classDef known fill:#eee,stroke:#aaa,color:#888
  classDef partial fill:#fff3cd,stroke:#e0a800
  classDef new fill:#cfe8ff,stroke:#0366d6,stroke-width:3px
  class A known
  class B partial
  class C,D new
```
"""

# For every node, cluster and edge label: WCAG contrast of its colour against its fill.
LABEL_CONTRAST_JS = r"""() => {
  const parse = (css) => { const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/.exec(css); return m ? [+m[1], +m[2], +m[3]] : null; };
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const out = [];
  document.querySelectorAll('.mermaid svg g.node').forEach(g => {
    const shape = [...g.querySelectorAll('rect, polygon, circle, ellipse, path')].find(s => !s.closest('foreignObject') && getComputedStyle(s).fill !== 'none');
    const fill = parse(getComputedStyle(shape).fill);
    const label = g.querySelector('foreignObject span, foreignObject p, foreignObject div, text');
    const html = !!label.closest('foreignObject');
    const color = parse(html ? getComputedStyle(label).color : getComputedStyle(label).fill);
    out.push({ text: g.textContent.trim(), ratio: ratio(color, fill) });
  });
  const pageBg = parse(getComputedStyle(document.body).backgroundColor);
  document.querySelectorAll('.mermaid svg g.cluster').forEach(g => {
    const rect = g.querySelector('rect');
    const label = g.querySelector('foreignObject span, foreignObject p, foreignObject div, text');
    const html = !!label.closest('foreignObject');
    const fill = parse(getComputedStyle(rect).fill);
    const color = parse(html ? getComputedStyle(label).color : getComputedStyle(label).fill);
    const am = /rgba\(.*,\s*([\d.]+)\)/.exec(getComputedStyle(rect).fill);
    const a = am ? parseFloat(am[1]) : 1;
    const bg = fill.map((v, i) => v * a + pageBg[i] * (1 - a));
    out.push({ text: 'cluster ' + g.textContent.trim(), ratio: ratio(color, bg) });
  });
  document.querySelectorAll('.mermaid svg .edgeLabel').forEach(g => {
    const label = g.querySelector('span, p, text'); if (!label || !g.textContent.trim()) return;
    const html = !!label.closest('foreignObject');
    const color = parse(html ? getComputedStyle(label).color : getComputedStyle(label).fill);
    let bg = pageBg;
    for (const el of [g, ...g.querySelectorAll('span, div, p')]) {
      const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+))?/.exec(getComputedStyle(el).backgroundColor);
      if (m && (m[4] === undefined || +m[4] > 0.5)) { bg = [+m[1], +m[2], +m[3]]; break; }
    }
    out.push({ text: 'edge ' + g.textContent.trim(), ratio: ratio(color, bg) });
  });
  return out;
}"""


def check_diagram_label_contrast_in_dark_theme(page, fx, base):
    # mermaid picks its theme from matchMedia when core.js loads, so the dark
    # emulation has to be in place before navigation.
    slug = fx.slug + "-diagram"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(DIAGRAM_LESSON, encoding="utf-8")
    try:
        for scheme in ("dark", "light"):
            page.emulate_media(color_scheme=scheme)
            page.goto(f"{base}/viewer/?lesson={slug}")
            page.wait_for_function("() => document.querySelectorAll('.mermaid svg g.node').length === 4 && document.querySelector('.mermaid svg .edgeLabel')", timeout=15000)
            rows = page.evaluate(LABEL_CONTRAST_JS)
            assert len(rows) >= 6 and any(r['text'].startswith('cluster') for r in rows) and any(r['text'].startswith('edge') for r in rows), rows
            bad = [r for r in rows if r["ratio"] < 4.5]
            assert not bad, f"{scheme}: low contrast labels {bad}"
    finally:
        page.emulate_media(color_scheme="light")
        shutil.rmtree(d, ignore_errors=True)


def check_diagram_open_button_opens_blob(page, fx, base):
    slug = fx.slug + "-diagram2"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(DIAGRAM_LESSON, encoding="utf-8")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelector('.mermaid svg') && document.querySelector('.diagram-open')", timeout=15000)
        assert page.locator(".diagram-open").count() == 1
        assert page.evaluate("() => !!document.querySelector('.diagram-open').closest('.no-read')")
        page.evaluate("""() => {
          window.__opened = [];
          window.__blobs = [];
          const real = URL.createObjectURL;
          URL.createObjectURL = (b) => { window.__blobs.push(b); return real(b); };
          window.open = (u, t) => { window.__opened.push(u); return null; };
        }""")
        page.locator(".diagram-open").click()
        opened = page.evaluate("() => window.__opened")
        assert len(opened) == 1 and opened[0].startswith("blob:"), opened
        html = page.evaluate("() => window.__blobs[0].text()")
        assert "<svg" in html and "DNS lookup" in html and "<!doctype html>" in html, html[:200]
        # the learner-facing text of the diagram is 18px like the body
        size = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.mermaid svg .nodeLabel, .mermaid svg text')).fontSize)")
        assert size >= 17, size
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_diagram_open_button_not_spoken(page, fx, base):
    slug = fx.slug + "-diagram3"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(DIAGRAM_LESSON, encoding="utf-8")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelector('.diagram-open')", timeout=15000)
        text = page.evaluate("() => Classroom.itemsFor(document.querySelector('main')).map(i => i.text).join(' | ')")
        assert "Open" not in text and "⤢" not in text, text
    finally:
        shutil.rmtree(d, ignore_errors=True)


WIDE_MD = "graph LR\n" + "\n".join(f"  N{i}[Step number {i} of the chain] --> N{i+1}[Step number {i+1} of the chain]" for i in range(10))


def check_diagram_wide_graph_text_stays_readable(page, fx, base):
    slug = fx.slug + "-diagram4"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text(f"# Wide\n\n## Chain\n\n```mermaid\n{WIDE_MD}\n```\n", encoding="utf-8")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelector('.mermaid svg') && document.querySelector('.diagram-open')", timeout=15000)
        r = page.evaluate("""() => { const svg = document.querySelector('.mermaid svg');
          const vb = svg.viewBox.baseVal.width; const w = svg.getBoundingClientRect().width;
          const t = svg.querySelector('.nodeLabel, text'); const fs = parseFloat(getComputedStyle(t).fontSize);
          const m = document.querySelector('.mermaid');
          return { vb, w, fs, eff: w / vb * fs, scrolls: m.scrollWidth > m.clientWidth }; }""")
        assert r["vb"] > 1200, r
        assert r["eff"] >= 14, r
        assert r["scrolls"], r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_diagram_in_chat_message(page, fx, base):
    slug = fx.slug + "-diagram5"
    d = ROOT / "lessons" / slug
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "lesson.md").write_text("# Chat\n\n## One\n\nHello there.\n", encoding="utf-8")
    md = DIAGRAM_LESSON.split("```mermaid\n")[1].split("```")[0]
    entry = {"id": "c1", "kind": "claude", "text": "Here is the map:\n\n```mermaid\n" + md + "```\n"}
    (d / "chat.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    try:
        page.emulate_media(color_scheme="dark")
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_function("() => document.querySelectorAll('#chat-log .mermaid svg g.node').length === 4 && document.querySelector('#chat-log .diagram-open')", timeout=15000)
        rows = page.evaluate(LABEL_CONTRAST_JS.replace(".mermaid svg", "#chat-log .mermaid svg"))
        assert len(rows) >= 6, rows
        bad = [r for r in rows if r["ratio"] < 4.5]
        assert not bad, bad
    finally:
        page.emulate_media(color_scheme="light")
        shutil.rmtree(d, ignore_errors=True)


def check_back_to_lessons_button(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    home = page.locator("#home")
    assert home.is_visible(), "lesson page shows the back button"
    page.evaluate("() => document.getElementById('home').click()")
    page.wait_for_selector("ul.lesson-list, p.empty", timeout=8000)
    assert "lesson=" not in page.url, page.url
    assert not page.locator("#home").is_visible(), "the list page hides it"
    page.goto(f"{base}/viewer/?lesson={fx.slug}&view=chat")
    page.wait_for_selector("#chat-log")
    assert page.locator("#home").is_visible(), "chat-only tab shows it too"
    page.goto(f"{base}/viewer/?lesson={fx.slug}")


def check_chat_jumps_ahead_of_queued_lesson(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate("""() => {
        window.__spoken.length = 0;
        Classroom.hold('t', true);
        Classroom.enqueue([{ text: 'L1', source: 'lesson' }, { text: 'L2', source: 'lesson' }]);
        Classroom.enqueue([{ text: 'C1', source: 'chat' }]);
        Classroom.hold('t', false);
    }""")
    page.wait_for_function("() => window.__spoken.length >= 3", timeout=5000)
    assert page.evaluate("() => window.__spoken.slice(0, 3)") == ["C1", "L1", "L2"]


def check_new_section_waits_for_chat_reply(page, fx, base):
    lesson, chat = fx.dir / "lesson.md", fx.dir / "chat.jsonl"
    lesson_src = lesson.read_text(encoding="utf-8")
    chat_src = chat.read_text(encoding="utf-8") if chat.exists() else None
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate("() => { Classroom.settings.chatWait = 10000; window.__spoken.length = 0; }")
    try:
        # The learner's message starts a turn; Claude's reply ends it.
        _append_chat(fx, {"id": "wait0", "kind": "you", "text": "Next idea, please."})
        page.wait_for_function("() => [...document.querySelectorAll('.msg.you')].some(m => m.textContent === 'Next idea, please.')", timeout=5000)
        lesson.write_text(lesson_src + "\n## Waiting idea\n\nRead after the chat.\n", encoding="utf-8")
        page.wait_for_function("() => document.querySelectorAll('main section').length === 2", timeout=8000)
        page.wait_for_timeout(1000)
        assert "Read after the chat." not in page.evaluate("() => window.__spoken"), "lesson must wait for chat"
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "wait1", "kind": "claude", "text": "The section is ready."}) + "\n")
        page.wait_for_function("() => window.__spoken.includes('Read after the chat.')", timeout=8000)
        spoken = page.evaluate("() => window.__spoken")
        assert spoken.index("The section is ready.") < spoken.index("Read after the chat."), spoken
        # Re-reading a chat message right after a new section doesn't wait for chat.
        page.evaluate("() => { window.__spoken.length = 0; Classroom.settings.chatWait = 10000; }")
        lesson.write_text(lesson_src + "\n## Waiting idea\n\nRead after the chat.\n\n## Third idea\n\nThird.\n", encoding="utf-8")
        page.wait_for_function("() => document.querySelectorAll('main section').length === 3", timeout=8000)
        page.evaluate("() => { Classroom.stop(); Classroom.enqueue([{ text: 'Again', source: 'chat' }]); }")
        page.wait_for_function("() => window.__spoken.includes('Again')", timeout=2000)
        # Pressing play never waits for the reply.
        _append_chat(fx, {"id": "wait2", "kind": "you", "text": "One more."})
        page.wait_for_function("() => [...document.querySelectorAll('.msg.you')].some(m => m.textContent === 'One more.')", timeout=5000)
        page.evaluate("() => { window.__spoken.length = 0; }")
        lesson.write_text(lesson_src + "\n## Waiting idea\n\nRead after the chat.\n\n## Third idea\n\nThird.\n\n## Fourth idea\n\nFourth.\n", encoding="utf-8")
        page.wait_for_function("() => [...document.querySelectorAll('main h2')].some(h => h.textContent === 'Fourth idea')", timeout=8000)
        page.wait_for_timeout(300)
        assert "Fourth." not in page.evaluate("() => window.__spoken"), "waits for the reply"
        page.evaluate("() => Classroom.playFrom(1)")
        page.wait_for_function("() => window.__spoken.includes('Read after the chat.')", timeout=3000)
    finally:
        lesson.write_text(lesson_src, encoding="utf-8")
        if chat_src is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_text(chat_src, encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section")


def check_main_tab_waits_while_chat_tab_speaks(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    chat = fx.dir / "chat.jsonl"
    chat_src = chat.read_text(encoding="utf-8") if chat.exists() else None
    beat = "(a) => __bc.postMessage({ type: 'chat-tab', speaking: a[1], lesson: a[0] })"
    page.evaluate("() => { window.__spoken.length = 0; window.__bc = new BroadcastChannel('classroom'); }")
    try:
        page.evaluate(beat, [fx.slug, True])
        page.wait_for_timeout(300)
        page.evaluate("() => Classroom.enqueue([{ text: 'Lesson line', source: 'lesson' }])")
        # The main tab's own copy of a new chat message must not cut the hold short.
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "tab1", "kind": "claude", "text": "From the chat tab."}) + "\n")
        page.wait_for_function("() => [...document.querySelectorAll('.msg.claude')].some(m => m.textContent.includes('From the chat tab.'))", timeout=5000)
        for _ in range(3):  # 6 s, past the 4 s grace: the heartbeat still says speaking
            page.evaluate(beat, [fx.slug, True])
            page.wait_for_timeout(2000)
        assert "Lesson line" not in page.evaluate("() => window.__spoken"), "held while the chat tab talks"
        # Switching engines while held keeps the waiting section.
        page.evaluate("() => Classroom.setEngine(Classroom.defaultEngine, { keep: true })")
        page.evaluate(beat, [fx.slug, False])
        page.wait_for_function("() => window.__spoken.includes('Lesson line')", timeout=3000)
        # A chat tab that stops sending heartbeats stops holding the main tab.
        page.evaluate("() => { window.__spoken.length = 0; }")
        page.evaluate(beat, [fx.slug, True])
        page.wait_for_timeout(300)
        page.evaluate("() => Classroom.enqueue([{ text: 'After stale', source: 'lesson' }])")
        page.wait_for_function("() => window.__spoken.includes('After stale')", timeout=8000)
    finally:
        page.evaluate("(slug) => { __bc.postMessage({ type: 'chat-tab-closed', lesson: slug }); __bc.close(); }", fx.slug)
        if chat_src is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_text(chat_src, encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section")


# An engine that never finishes an item on its own: window.__done() ends the
# current one. It records what it was asked to say in window.__said.
HELD_ENGINE = """() => { window.__said = []; let paused = false;
    Classroom.setEngine({ speak(item, done) { window.__said.push(item.text); window.__done = done; },
        cancel() { paused = false; }, pause() { paused = true; }, resume() { paused = false; },
        paused() { return paused; } }); }"""


def _append_chat(fx, entry):
    with open(fx.dir / "chat.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def check_chat_tab_stop_or_pause_releases_main_tab(page, fx, base):
    """Stop or Pause in a chat tab must not leave the main tab silent."""
    _open_chat(page, fx, base)
    with page.expect_popup() as pop:
        page.evaluate("(u) => window.open(u, '_blank')", f"{base}/viewer/?lesson={fx.slug}&view=chat")
    other = pop.value
    try:
        other.wait_for_load_state()
        other.wait_for_selector(".msg.quiz", timeout=10000)
        other.evaluate(HELD_ENGINE)
        page.wait_for_function("() => Classroom.settings.chat === false", timeout=5000)
        for button in ("#btn-stop", "#btn-pause"):
            page.evaluate("() => { window.__spoken.length = 0; }")
            other.evaluate("() => Classroom.enqueue([{ text: 'Chat tab talking' }, { text: 'More' }])")
            page.wait_for_timeout(600)
            line = f"Lesson after {button}"
            page.evaluate("(t) => Classroom.enqueue([{ text: t, source: 'lesson' }])", line)
            page.wait_for_timeout(1000)
            assert line not in page.evaluate("() => window.__spoken"), "held while the chat tab talks"
            other.click(button)
            page.wait_for_function("(t) => window.__spoken.includes(t)", arg=line, timeout=3000)
            other.evaluate("() => Classroom.stop()")
    finally:
        other.close()
        _open_chat(page, fx, base)


def check_section_after_a_long_reply_does_not_wait_again(page, fx, base):
    """The reply was read first (for more than 10 s); the section then starts at once."""
    lesson, chat = fx.dir / "lesson.md", fx.dir / "chat.jsonl"
    lesson_src = lesson.read_text(encoding="utf-8")
    chat_src = chat.read_text(encoding="utf-8") if chat.exists() else None
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate("""() => { Classroom.settings.chatWait = 20000; window.__said = [];
        Classroom.setEngine({ speak(item, done) { window.__said.push([Date.now(), item.text]);
                setTimeout(done, item.source === 'chat' ? 1000 : 5); },
            cancel() {}, pause() {}, resume() {}, paused() { return false; } }); }""")
    try:
        _append_chat(fx, {"id": "long-you", "kind": "you", "text": "Next please."})
        page.wait_for_function("() => [...document.querySelectorAll('.msg.you')].some(m => m.textContent === 'Next please.')", timeout=5000)
        _append_chat(fx, {"id": "long-claude", "kind": "claude", "text": " ".join(f"Reply sentence {i}." for i in range(1, 13))})
        page.wait_for_function("() => window.__said.some(x => x[1] === 'Reply sentence 1.')", timeout=5000)
        lesson.write_text(lesson_src + "\n## After the reply\n\nRead once the reply is done.\n", encoding="utf-8")
        page.wait_for_function("() => window.__said.some(x => x[1] === 'Read once the reply is done.')", timeout=40000)
        said = page.evaluate("() => window.__said")
        last_reply = max(t for t, s in said if s.startswith("Reply sentence"))
        section = next(t for t, s in said if s == "After the reply")
        assert section - last_reply - 1000 < 3000, f"section waited {section - last_reply - 1000} ms after the reply"
    finally:
        lesson.write_text(lesson_src, encoding="utf-8")
        if chat_src is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_text(chat_src, encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section")


def check_lesson_edit_while_held_keeps_queue_attached(page, fx, base):
    """An edit to a held section doesn't detach the queue; Play skips the wait."""
    lesson, chat = fx.dir / "lesson.md", fx.dir / "chat.jsonl"
    lesson_src = lesson.read_text(encoding="utf-8")
    chat_src = chat.read_text(encoding="utf-8") if chat.exists() else None
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    focus_off(page)  # this is about core's queue: Play here means "skip the wait", not focus's "read this section"
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    n0 = page.evaluate("() => document.querySelectorAll('main section').length")
    page.evaluate("() => { Classroom.settings.chatWait = 20000; window.__spoken.length = 0; }")
    try:
        _append_chat(fx, {"id": "held-you", "kind": "you", "text": "Go on."})
        page.wait_for_function("() => [...document.querySelectorAll('.msg.you')].some(m => m.textContent === 'Go on.')", timeout=5000)
        lesson.write_text(lesson_src + "\n## Held idea\n\nDraft text.\n", encoding="utf-8")
        page.wait_for_function("(n) => document.querySelectorAll('main section').length === n", arg=n0 + 1, timeout=8000)
        page.wait_for_timeout(300)
        assert "Draft text." not in page.evaluate("() => window.__spoken"), "the section waits for the reply"
        lesson.write_text(lesson_src + "\n## Held idea\n\nFinal text.\n", encoding="utf-8")
        page.wait_for_timeout(4000)  # past a lesson poll
        assert page.evaluate("() => Classroom.peek().el.isConnected"), "held items must stay in the page"
        page.click("#btn-play")  # skip the wait
        page.wait_for_function("() => window.__spoken.includes('Draft text.')", timeout=3000)
        assert page.evaluate("() => window.__spoken[0]") == "Held idea", page.evaluate("() => window.__spoken")
        page.wait_for_function("() => document.querySelector('main').textContent.includes('Final text.')", timeout=8000)
        # Play after a re-render resumes at the section last read, not at the top.
        page.evaluate("() => { window.__spoken.length = 0; }")
        lesson.write_text(lesson_src + "\n## Held idea\n\nFinal text. More.\n", encoding="utf-8")
        page.wait_for_function("() => document.querySelector('main').textContent.includes('More.')", timeout=8000)
        page.click("#btn-play")
        page.wait_for_function("() => window.__spoken.length > 0", timeout=3000)
        assert page.evaluate("() => window.__spoken[0]") == "Held idea", page.evaluate("() => window.__spoken")
    finally:
        page.evaluate("() => Classroom.stop()")
        focus_default(page)
        lesson.write_text(lesson_src, encoding="utf-8")
        if chat_src is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_text(chat_src, encoding="utf-8")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section")


def check_test_voice_plays_during_a_hold(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate("""() => { window.__spoken.length = 0; Classroom.hold('t', true);
        Classroom.enqueue([{ text: 'Held lesson', source: 'lesson' }]); }""")
    try:
        open_settings(page)  # Test voice lives in the Settings popover (#43)
        page.click("button:has-text('Test voice')")
        page.wait_for_function("() => window.__spoken.some(s => s.includes('sample'))", timeout=1500)
        assert "Held lesson" not in page.evaluate("() => window.__spoken"), "the hold still holds the lesson"
        page.evaluate("() => Classroom.hold('t', false)")
        page.wait_for_function("() => window.__spoken.includes('Held lesson')", timeout=2000)
    finally:
        page.evaluate("() => { Classroom.hold('t', false); Classroom.stop(); }")


def check_chat_waits_for_end_of_paragraph(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate(HELD_ENGINE)
    said = page.evaluate("""() => {
        const p1 = document.createElement('p'), p2 = document.createElement('p');
        Classroom.enqueue([{ el: p1, text: 'P1a' }, { el: p1, text: 'P1b' }, { el: p2, text: 'P2' }]);
        Classroom.enqueue([{ text: 'C', source: 'chat' }]);
        for (let i = 0; i < 4; i++) window.__done();
        return window.__said.slice(); }""")
    assert said == ["P1a", "P1b", "C", "P2"], said
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")


def check_held_queue_goes_idle(page, fx, base):
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate(HELD_ENGINE)
    idle = page.evaluate("""() => { let n = 0; Classroom.on('idle', () => n++);
        Classroom.hold('t', true); Classroom.enqueue([{ text: 'L', source: 'lesson' }]);
        Classroom.hold('t', false); Classroom.stop(); return n; }""")
    assert idle >= 1, "a held queue says idle, so highlights clear"
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
# --- 43 visual redesign ---

CONTRAST_JS = """
() => {
  const parse = (c) => { const m = c.match(/[\\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const bgOf = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) layers.push(parse(getComputedStyle(n).backgroundColor));
    let out = { r: 255, g: 255, b: 255, a: 1 };
    for (const l of layers.reverse()) out = over(l, out);
    return out;
  };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const out = {};
  const pair = (name, el, pseudo) => {
    if (!el) { out[name] = "missing"; return; }
    const cs = getComputedStyle(el, pseudo || null);
    const fg = parse(cs.color);
    const bg = pseudo ? over(parse(cs.backgroundColor), bgOf(el)) : bgOf(el);
    out[name] = Math.round(ratio(over(fg, bg), bg) * 100) / 100;
  };
  const q = (s) => document.querySelector(s);
  pair("body text", q("main section p"));
  pair("heading", q("main section h2"));
  pair("muted text", q("#chat-log .hint"));
  pair("primary button", q("#btn-play"));
  pair("plain button", q("#btn-pause"));
  pair("settings button", q("#btn-settings"));
  pair("link", q("main a"));
  pair("step badge", q("main section h2"), "::before");
  pair("your turn card", q("main .your-turn"));
  pair("you bubble", q(".msg.you"));
  pair("claude bubble", q(".msg.claude"));
  pair("quiz card", q(".msg.quiz .quiz-q p"));
  pair("quiz chip", q(".msg.quiz .chip"));
  pair("chosen answer", q(".msg.quiz li.chosen"));
  pair("quiz option hint", q(".msg.quiz li small"));
  pair("code", q("main pre code"));
  return out;
}
"""

LESSON_43 = (
    "# Redesign lesson\n\n"
    "## One\n\nFirst section has a [link](https://example.com) and some text.\n\n"
    "```python\nprint('hi')\n```\n\n"
    "## Two\n\nSecond section.\n\n> Your turn: say it in your own words.\n\n"
    "## Three\n\nThird section.\n\nTry it: change one number.\n\n"
    "## Four\n\nFourth.\n\n## Five\n\nFifth.\n\n**Practice**: build one small thing.\n"
)


def _with_redesign_lesson(page, fx, base, fn):
    lesson = fx.dir / "lesson.md"
    original = lesson.read_text(encoding="utf-8")
    lesson.write_text(LESSON_43, encoding="utf-8")
    try:
        # Focus mode (classroom.focus, #45) hides future sections; these checks are about styling,
        # so switch it off for them and restore it afterwards.
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.evaluate("() => localStorage.setItem('classroom.focus', 'false')")
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section:nth-of-type(5)", state="attached", timeout=10000)
        page.evaluate("""() => {
            const log = document.getElementById('chat-log');
            log.innerHTML = `<p class="hint">hint</p><div class="msg you">hello there</div><div class="msg claude"><p>A reply with <a href="#">a link</a>.</p></div>
              <div class="msg quiz"><div class="quiz-q"><span class="chip">Check</span><p>Which one?</p>
              <ol><li data-label="a" class="chosen">Alpha<small>why</small></li><li>Beta<small>why</small></li></ol></div></div>`;
        }""")
        fn()
    finally:
        lesson.write_text(original, encoding="utf-8")
        page.evaluate("() => localStorage.removeItem('classroom.focus')")
        page.emulate_media(color_scheme="light", reduced_motion="no-preference")


def check_redesign_contrast(page, fx, base):
    """Text on every main surface reaches WCAG AA (4.5:1), in light and dark."""
    def run():
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            ratios = page.evaluate(CONTRAST_JS)
            for name, r in ratios.items():
                assert r != "missing", f"{scheme}: no element for {name}"
                assert r >= 4.5, f"{scheme}: {name} contrast {r} < 4.5 ({ratios})"
        # An explicit theme choice wins over the OS setting.
        page.emulate_media(color_scheme="light")
        page.evaluate("() => document.documentElement.setAttribute('data-theme', 'dark')")
        bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        assert bg != "rgb(245, 242, 255)", bg
        page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    _with_redesign_lesson(page, fx, base, run)


def check_redesign_design_tokens(page, fx, base):
    """The tokens other features rely on exist in :root, in light and dark."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        got = page.evaluate("""() => { const cs = getComputedStyle(document.documentElement);
            return Object.fromEntries(["--accent", "--accent-2", "--success", "--danger", "--card-shadow", "--radius"]
                .map(k => [k, cs.getPropertyValue(k).trim()])); }""")
        for k, v in got.items():
            assert v, f"{scheme}: {k} is not defined"
        assert got["--radius"] == "18px", got
    page.emulate_media(color_scheme="light")


def check_redesign_step_badges(page, fx, base):
    """Every ## section gets a coloured STEP n badge (CSS only), cycling colours; heading text is unchanged."""
    def run():
        info = page.evaluate("""() => [...document.querySelectorAll('main section')].map(s => {
            const h = s.querySelector('h2'); const cs = getComputedStyle(h, '::before');
            return { text: h.textContent, content: cs.content, bg: cs.backgroundColor, radius: parseFloat(getComputedStyle(s).borderTopLeftRadius) };
        })""")
        assert len(info) == 5, info
        for n, i in enumerate(info, 1):
            assert i["content"] == f'"STEP {n}"', (n, i)
            assert "STEP" not in i["text"], f"badge leaked into the heading text: {i}"
            assert i["radius"] >= 16, i
        assert len({i["bg"] for i in info[:4]}) == 4, "badge colours should cycle through four colours"
        assert info[4]["bg"] == info[0]["bg"], "...and then repeat"
        # Hidden sections (focus mode) must not shift the numbers.
        page.evaluate("() => { document.querySelectorAll('main section')[2].style.display = 'none'; }")
        after = page.evaluate("() => [...document.querySelectorAll('main section h2')].map(h => getComputedStyle(h, '::before').content)")
        assert after[3] == '"STEP 4"' and after[4] == '"STEP 5"', after
    _with_redesign_lesson(page, fx, base, run)


def check_redesign_your_turn_style(page, fx, base):
    """Blockquotes and paragraphs starting with "Your turn" / "Try it" are styled as task cards."""
    def run():
        marked = page.evaluate("""() => [...document.querySelectorAll('main .your-turn')].map(e => [e.tagName, e.classList.contains('try-it')])""")
        assert marked == [["BLOCKQUOTE", False], ["P", True], ["P", False]], marked  # last one: bold **Practice**
        assert page.evaluate("() => getComputedStyle(document.querySelector('main .your-turn'), '::before').content") == '"✋"'
        assert page.locator("main section p:not(.your-turn)").count() >= 3, "ordinary paragraphs stay plain"
        assert page.evaluate("() => document.querySelectorAll('main blockquote.your-turn p.your-turn').length") == 0
    _with_redesign_lesson(page, fx, base, run)


def check_redesign_settings_popover(page, fx, base):
    """The Settings popover opens and closes, holds the voice select, and the main controls stay in the bar."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("#btn-settings")
    pop = page.locator("#settings-pop")
    assert not pop.is_visible(), "closed by default"
    for sel in ("#btn-play", "#btn-pause", "#btn-stop", "#btn-chat-full", "#home"):
        assert page.locator(sel).is_visible(), f"{sel} must stay in the bar"
    for sel in ("#voice", "#rate", "#easy-font", "#kk-toggle", "#student-voice"):
        assert not page.locator(sel).is_visible(), f"{sel} belongs behind Settings"
    for sel in ("#auto", "#auto-chat"):
        assert page.locator(sel).is_visible(), f"{sel} (auto-read) must stay visible in the bar"
    page.click("#btn-settings")
    assert pop.is_visible()
    assert page.get_attribute("#btn-settings", "aria-expanded") == "true"
    for sel in ("#voice", "#rate", "#easy-font", "#kk-toggle", "#student-voice"):
        assert page.locator(f"#settings-pop {sel}").count() == 1, f"{sel} missing from the popover"
    assert page.locator("#settings-pop #voice").is_visible(), "voice select is in the popover"
    page.click("#btn-settings")
    assert not pop.is_visible(), "the button closes it again"
    page.click("#btn-settings")
    page.keyboard.press("Escape")
    assert not pop.is_visible(), "Escape closes it"
    assert page.get_attribute("#btn-settings", "aria-expanded") == "false"
    page.click("#btn-settings")
    page.click("main")
    assert not pop.is_visible(), "a click outside closes it"
    page.click("#btn-settings")
    page.focus("#settings-pop #voice")
    # Tab on until focus leaves the popover. Features add their own controls to it
    # (Classroom.addSetting), so don't hard-code how many there are.
    for _ in range(40):
        page.keyboard.press("Tab")
        if not page.evaluate("() => document.getElementById('settings-pop').contains(document.activeElement)"):
            break
    assert not pop.is_visible(), "tabbing out closes it"


def check_redesign_settings_stays_on_screen(page, fx, base):
    """The Settings popover is inside the viewport on a phone and on a mid-size window."""
    try:
        for w, h in ((390, 800), (800, 700), (1300, 900)):
            page.set_viewport_size({"width": w, "height": h})
            page.goto(f"{base}/viewer/?lesson={fx.slug}")
            page.wait_for_selector("#btn-settings")
            page.click("#btn-settings")
            r = page.evaluate("() => { const b = document.getElementById('settings-pop').getBoundingClientRect(); return [b.left, b.right, b.top, b.bottom, innerWidth, innerHeight]; }")
            assert r[0] >= 0 and r[1] <= r[4], f"{w}px: popover is off screen {r}"
            assert r[2] >= 0 and r[3] <= r[5] + 1, f"{w}px: popover runs off the bottom {r}"
            assert page.locator("#settings-pop #voice").is_visible()
    finally:
        page.set_viewport_size({"width": 1300, "height": 900})


def check_redesign_small_buttons_stay_small(page, fx, base):
    """Only header controls get the big 40px buttons; small buttons inside the page keep their own size."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    h = page.evaluate("""() => {
        const mk = (parent, cls) => { const b = document.createElement('button'); b.className = cls; b.textContent = 'x'; parent.appendChild(b); return b; };
        const dot = mk(document.querySelector('main section'), 'tiny'); dot.style.cssText = 'width:14px;height:14px;padding:0';
        const plain = mk(document.querySelector('main section'), '');
        const out = [dot.getBoundingClientRect().height, plain.getBoundingClientRect().height,
                     document.getElementById('btn-pause').getBoundingClientRect().height];
        dot.remove(); plain.remove(); return out;
    }""")
    assert h[0] <= 16, f"a 14px dot must stay 14px tall, got {h[0]}"
    assert h[1] < 40, f"ordinary page buttons are not 40px tall, got {h[1]}"
    assert h[2] >= 40, f"header buttons are big and friendly, got {h[2]}"


def check_redesign_reduced_motion(page, fx, base):
    """With prefers-reduced-motion the button and card transitions are switched off."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("#btn-play")
    js = "() => getComputedStyle(document.getElementById('btn-play')).transitionDuration"
    assert page.evaluate(js) != "0s"
    page.emulate_media(reduced_motion="reduce")
    assert page.evaluate(js) == "0s", page.evaluate(js)
    page.emulate_media(reduced_motion="no-preference")


def check_redesign_lesson_list(page, fx, base):
    """The lesson list shows big cards, one per lesson."""
    page.goto(f"{base}/viewer/")
    page.wait_for_selector("ul.lesson-list a", timeout=8000)
    h, r = page.evaluate("""() => { const a = document.querySelector('ul.lesson-list a'); const cs = getComputedStyle(a);
        return [a.getBoundingClientRect().height, parseFloat(cs.borderTopLeftRadius)]; }""")
    assert h >= 70 and r >= 16, (h, r)
    assert not page.locator("#btn-settings").is_visible(), "no settings button on the list page"


# --- 44 progress & wins ---

# Shaped like a real lesson: an intro <details> before the first "##", then a plan
# map with more nodes than there will be sections, then five sections.
WINS_LESSON = (
    "# Wins\n\n<details>\n\nProbe results, not read aloud.\n\n</details>\n\n"
    "## The plan\n\n```mermaid\ngraph TD\n  A --> B\n  B --> C\n  C --> D\n  D --> E\n  E --> F\n  F --> G\n  G --> H\n  H --> I\n```\n\n"
    "## Two\n\nSecond section.\n\n## Three\n\nThird section.\n\n## Four\n\nFourth section.\n\n## Five\n\nFifth section.\n"
)
# Counts oscillators instead of making sound. The viewer makes its AudioContext
# on the first click or key press, so a check sends one with _wins_gesture.
WINS_AUDIO = """
window.__osc = 0;
window.AudioContext = class {
  constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
  resume() {}
  createGain() { return { gain: { value: 1, setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
  createOscillator() { window.__osc++; return { frequency: {}, connect() {}, start() {}, stop() {} }; }
};
"""


def _wins_chat(fx, entries):
    with open(fx.dir / "chat.jsonl", "a", encoding="utf-8") as f:
        for i, text in entries:
            f.write(json.dumps({"id": i, "kind": "claude", "text": text}) + "\n")


def _wins_day(days_ago):
    t = datetime.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    return (t - datetime.timedelta(days=days_ago)).astimezone(datetime.timezone.utc).isoformat()


def _wins_open(page, fx, base, store):
    """Load the viewer with progress/concepts.json served from `store` (a dict holding the body, or None)."""
    def serve(route):
        if store.get("body") is None:
            route.fulfill(status=404, body="")
        else:
            route.fulfill(status=200, content_type="application/json", body=store["body"])
    page.unroute("**/progress/concepts.json")
    page.route("**/progress/concepts.json", serve)
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("#wins-progress .dot", timeout=15000)
    page.evaluate("() => {" + WINS_AUDIO + "}")


def _wins_gesture(page):
    page.evaluate("() => window.dispatchEvent(new Event('pointerdown'))")


def _wins_count(page):
    return page.evaluate("() => document.querySelector('#wins-progress .count')?.textContent")


def _wins_speak(page, i):
    page.evaluate("(i) => Classroom.emit('speechStart', {el: [...document.querySelectorAll('main section')].filter(s => s.querySelector('h2'))[i].querySelector('p'), text: 'x'})", i)


def check_progress_and_wins(page, fx, base):
    """Progress dots, celebration cues, mute, reduced motion, points and streak."""
    lesson, chat = fx.dir / "lesson.md", fx.dir / "chat.jsonl"
    saved_lesson = lesson.read_bytes()
    saved_chat = chat.read_bytes() if chat.exists() else None
    clear = "() => { try { localStorage.clear(); } catch {} }"
    # Focus mode (#45, on by default) opens a lesson with no saved place at its newest
    # section and folds the rest, which counts them done. These checks drive the bar
    # themselves, so focus is off (the fx-folded coupling is checked by hand below).
    reset = "() => { try { localStorage.clear(); localStorage.setItem('classroom.focus', 'false'); } catch {} }"
    page.evaluate(reset)
    store = {}
    try:
        lesson.write_text(WINS_LESSON, encoding="utf-8")
        store["body"] = json.dumps({"version": 1, "concepts": {"c": {"title": "c", "lesson": fx.slug, "requires": [], "attempts": [
            {"at": _wins_day(0), "kind": "quiz", "correct": True, "note": None},
            {"at": _wins_day(1), "kind": "quiz", "correct": True, "rating": "hard"},
            {"at": _wins_day(2), "kind": "quiz", "correct": False, "note": "hints: 2"},
            {"at": _wins_day(4), "kind": "quiz", "correct": True, "note": "hints: 0"},
        ]}}})
        # A correct entry that predates the page load is history: it must not celebrate.
        chat.write_text(json.dumps({"id": "old-win", "kind": "claude", "text": "✓ Correct. Old news."}) + "\n", encoding="utf-8")
        _wins_open(page, fx, base, store)
        _wins_gesture(page)

        # Progress: the intro is not a section, the plan map's nodes are not sections.
        page.wait_for_function("() => document.querySelectorAll('main section').length === 6", timeout=8000)
        page.wait_for_function("() => document.querySelector('#wins-progress .count')?.textContent === '0 / 5'", timeout=8000)
        assert page.locator("#wins-progress .dot").count() == 5
        assert page.locator("#wins-progress .more").is_visible(), "no 'more coming' before a Recap"
        # Jumping ahead credits nothing before the section.
        _wins_speak(page, 3)
        assert _wins_count(page) == "0 / 5", _wins_count(page)
        page.evaluate("() => Classroom.emit('idle')")
        assert _wins_count(page) == "1 / 5"
        # Reading straight through reaches 5 / 5.
        page.evaluate(reset)
        _wins_open(page, fx, base, store)
        page.wait_for_function("() => document.querySelectorAll('#wins-progress .dot').length === 5", timeout=8000)
        for i in range(5):
            _wins_speak(page, i)
        cls = page.evaluate("() => [...document.querySelectorAll('#wins-progress .dot')].map(d => d.className.replace('dot', '').trim())")
        assert cls == ["done", "done", "done", "done", "current"], cls
        page.evaluate("() => Classroom.emit('idle')")
        assert _wins_count(page) == "5 / 5", _wins_count(page)
        assert page.evaluate("() => JSON.parse(localStorage.getItem('classroom.progressDone.' + Classroom.slug)).length") == 5
        # The Recap arrives: the hint goes away and the bar still reaches full.
        lesson.write_text(WINS_LESSON + "\n## Recap\n\nDone.\n", encoding="utf-8")
        page.wait_for_function("() => document.querySelector('#wins-progress .count')?.textContent === '5 / 6'", timeout=8000)
        assert not page.locator("#wins-progress .more").is_visible()
        lesson.write_text(WINS_LESSON, encoding="utf-8")
        page.wait_for_function("() => document.querySelector('#wins-progress .count')?.textContent === '5 / 5'", timeout=8000)
        # A section folded away by focus mode (class fx-folded) counts as done.
        page.evaluate(reset)
        _wins_open(page, fx, base, store)
        page.wait_for_function("() => document.querySelectorAll('#wins-progress .dot').length === 5", timeout=8000)
        page.evaluate("() => [...document.querySelectorAll('main section')].filter(s => s.querySelector('h2'))[0].classList.add('fx-folded')")
        page.wait_for_function("() => document.querySelector('#wins-progress .count')?.textContent === '1 / 5'")
        cur = page.evaluate("() => [...document.querySelectorAll('#wins-progress .dot')].findIndex(d => d.classList.contains('current'))")
        assert cur == 1, cur  # the first section not yet done

        # Points: 10 (today) + 5 (hard) + 0 (wrong) + 10 = 25; streak: today, yesterday, 2 days ago.
        page.wait_for_function("() => document.getElementById('wins-chip')?.textContent === '⚡ 25 · 🔥 3'", timeout=8000)

        # History does not celebrate (wait until it is on the page so this is not vacuous).
        page.wait_for_function("() => document.getElementById('chat-log').textContent.includes('Old news')", timeout=8000)
        page.wait_for_timeout(2000)
        assert page.evaluate("window.__osc") == 0, "history entry celebrated"
        assert page.locator("#wins-fx .bit").count() == 0

        # No click or key press yet: the confetti shows, no sound is queued.
        store["body"] = json.dumps({"version": 1, "concepts": {"c": {"attempts": [{"at": _wins_day(0), "correct": True}] * 3}}})
        _wins_chat(fx, [("w0", "✓ Correct. Quiet one.")])
        page.wait_for_selector("#wins-fx .bit", timeout=6000)
        assert page.evaluate("window.__osc") == 0, "sound without a user gesture"
        page.wait_for_function("() => document.querySelectorAll('#wins-fx .bit').length === 0", timeout=2000)

        page.click("#wins-progress .dot >> nth=2")  # a real click: unlocks audio, and jumps without error
        # After a gesture, a new correct entry plays and bursts; the chip follows the file.
        _wins_gesture(page)
        _wins_chat(fx, [("w1", "✓ Correct. Well done.")])
        page.wait_for_function("() => window.__osc >= 3", timeout=6000)
        assert page.locator("#wins-fx .bit").count() > 0, "no confetti"
        page.wait_for_function("() => document.querySelectorAll('#wins-fx .bit').length === 0", timeout=1500)
        page.wait_for_function("() => document.getElementById('wins-chip').textContent === '⚡ 30 · 🔥 1'", timeout=6000)

        # Several entries in one poll celebrate once.
        before = page.evaluate("window.__osc")
        _wins_chat(fx, [("w2", "✓ Correct. One."), ("w3", "✓ Correct. Two."), ("w4", "✗ Not quite. Three.")])
        page.wait_for_function(f"() => window.__osc > {before}", timeout=6000)
        page.wait_for_timeout(500)
        assert page.evaluate("window.__osc") - before == 3, "more than one celebration for one poll"

        # Not quite: a soft cue, no confetti.
        page.wait_for_function("() => document.querySelectorAll('#wins-fx .bit').length === 0", timeout=2000)
        before = page.evaluate("window.__osc")
        _wins_chat(fx, [("w5", "✗ Not quite. Try again.")])
        page.wait_for_function(f"() => window.__osc > {before}", timeout=6000)
        assert page.locator("#wins-fx .bit").count() == 0
        assert page.locator("#wins-fx .gentle").count() == 1

        # A chat-only tab for this lesson owns the sound: this tab stays visual-only.
        page.evaluate("(slug) => new BroadcastChannel('classroom').postMessage({type: 'chat-tab', lesson: slug})", fx.slug)
        page.wait_for_function("() => document.querySelectorAll('#wins-fx .gentle').length === 0", timeout=2000)
        before = page.evaluate("window.__osc")
        _wins_chat(fx, [("w6", "✓ Correct. Other tab.")])
        page.wait_for_selector("#wins-fx .bit", timeout=6000)
        assert page.evaluate("window.__osc") == before, "main tab played while a chat tab was open"

        # Mute persists across a reload and silences the chime.
        open_settings(page)  # the mute toggle lives in Settings (#43)
        page.click("#wins-mute")
        assert page.evaluate("localStorage.getItem('classroom.winsMuted')") == "true"
        _wins_open(page, fx, base, store)
        _wins_gesture(page)
        assert page.get_attribute("#wins-mute", "aria-pressed") == "true"
        _wins_chat(fx, [("w7", "✓ Correct. Again.")])
        page.wait_for_selector("#wins-fx .bit", timeout=6000)  # it was heard by the page
        assert page.evaluate("window.__osc") == 0, "muted but still played"
        open_settings(page)
        page.click("#wins-mute")
        assert page.get_attribute("#wins-mute", "aria-pressed") == "false"

        # Reduced motion: sound still plays, nothing animates.
        page.emulate_media(reduced_motion="reduce")
        _wins_open(page, fx, base, store)
        _wins_gesture(page)
        _wins_chat(fx, [("w8", "✓ Correct. Calm.")])
        page.wait_for_function("() => window.__osc >= 3", timeout=6000)
        assert page.locator("#wins-fx .bit").count() == 0, "confetti under reduced motion"
        page.emulate_media(reduced_motion="no-preference")

        # No concepts file: no chip.
        store["body"] = None
        _wins_open(page, fx, base, store)
        page.wait_for_timeout(1000)
        assert not page.locator("#wins-chip").is_visible()
    finally:
        page.emulate_media(reduced_motion="no-preference")
        page.unroute("**/progress/concepts.json")
        lesson.write_bytes(saved_lesson)
        if saved_chat is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_bytes(saved_chat)
        page.evaluate(clear)


# --- 45 focus mode ---


FOCUS_LESSON = ("# Focus lesson\n\n## Alpha step\n\nAlpha text here.\n\n"
                "## Beta step\n\nBeta text here.\n\n## Gamma step\n\nGamma text here.\n")


class focus_page:
    """Own browser context for focus checks; restores lesson.md afterwards.

    auto sets classroom.auto (read new sections aloud); chat_full starts the
    chat in full-page mode; pos is the saved place ("Alpha step#1" by default,
    None for no saved place). Own context keeps localStorage (and so the saved
    place) off the shared page.
    """

    def __init__(self, page, fx, lesson=FOCUS_LESSON, auto=True, chat_full=False, pos="Alpha step#1"):
        self.browser, self.fx, self.lesson, self.auto, self.chat_full, self.pos = \
            page.context.browser, fx, lesson, auto, chat_full, pos

    def __enter__(self):
        self.lesson_file = self.fx.dir / "lesson.md"
        self.saved = self.lesson_file.read_text(encoding="utf-8")
        self.lesson_file.write_text(self.lesson, encoding="utf-8")
        self.ctx = self.browser.new_context(viewport={"width": 1300, "height": 900})
        p = self.ctx.new_page()
        p.add_init_script(FAKE_SPEECH)
        seed = json.dumps({"k": self.pos, "i": 0}) if self.pos else None
        full = 'localStorage.setItem("classroom.chatFull", "true");' if self.chat_full else ""
        place = f'localStorage.setItem("classroom.focus.pos.{self.fx.slug}", {json.dumps(seed)});' if seed else ""
        p.add_init_script(f"""
            if (!sessionStorage.__fxinit) {{ sessionStorage.__fxinit = 1;
              localStorage.setItem("classroom.auto", "{str(self.auto).lower()}");
              {full}
              {place} }}
        """)
        return p

    def __exit__(self, *exc):
        self.ctx.close()
        self.lesson_file.write_text(self.saved, encoding="utf-8")


def fx_load(p, fx, base, n=3):
    p.goto(f"{base}/viewer/?lesson={fx.slug}")
    p.wait_for_function(f"() => document.querySelectorAll('main section .fx-row').length === {n}", timeout=10000)


def fx_state(page):
    """For each section: 'full' (heading visible), 'row' (only the fold row), or 'hidden'."""
    return page.evaluate("""() => [...document.querySelectorAll('main section')].map(s => {
        const shown = (el) => !!el && el.getClientRects().length > 0;
        if (!shown(s)) return 'hidden';
        return shown(s.querySelector('h2')) ? 'full' : 'row';
    })""")


def check_focus_default_on_only_current_visible(page, fx, base):
    with focus_page(page, fx) as p:
        fx_load(p, fx, base)
        assert p.evaluate("localStorage.getItem('classroom.focus')") is None
        assert p.locator("#btn-focus").get_attribute("aria-pressed") == "true"
        assert fx_state(p) == ["full", "hidden", "hidden"], fx_state(p)
        assert p.locator("main section").first.locator(".fx-next-btn").is_visible()
        assert not p.locator("main section").nth(1).locator(".fx-next-btn").is_visible()


def check_focus_opens_at_newest_section_without_saved_place(page, fx, base):
    with focus_page(page, fx, pos=None) as p:
        fx_load(p, fx, base)
        assert fx_state(p) == ["row", "row", "full"], fx_state(p)


def check_focus_next_folds_and_advances(page, fx, base):
    with focus_page(page, fx) as p:
        fx_load(p, fx, base)
        p.evaluate("() => { window.__spoken.length = 0; }")
        p.locator("main section").first.locator(".fx-next-btn").click()
        assert fx_state(p) == ["row", "full", "hidden"], fx_state(p)
        row = p.locator("main section").first.locator(".fx-row")
        assert row.inner_text().startswith("✓ Step 1 · Alpha step"), row.inner_text()
        # auto-read starts the next section, and only that one
        p.wait_for_function("() => window.__spoken.includes('Beta text here.')", timeout=5000)
        p.wait_for_timeout(300)
        spoken = p.evaluate("window.__spoken")
        assert "Gamma text here." not in spoken and "Alpha text here." not in spoken, spoken
        p.locator("main section").nth(1).locator(".fx-next-btn").click()
        assert fx_state(p) == ["row", "row", "full"], fx_state(p)
        # The last section has no Next; it waits for Claude.
        last = p.locator("main section").last
        assert not last.locator(".fx-next-btn").is_visible()
        assert "Waiting for Claude's next step" in last.locator(".fx-wait").inner_text()
        assert last.locator(".fx-wait").is_visible()


def check_focus_reading_stops_at_next(page, fx, base):
    """Header Play and a programmatic read-on stay inside the current section."""
    with focus_page(page, fx, auto=False) as p:
        fx_load(p, fx, base)
        p.evaluate("() => { window.__spoken.length = 0; }")
        p.click("#btn-play")
        p.wait_for_function("() => window.__spoken.includes('Alpha text here.')", timeout=5000)
        p.wait_for_timeout(500)
        spoken = p.evaluate("window.__spoken")
        assert "Beta text here." not in spoken and "Beta step" not in spoken, spoken
        assert fx_state(p) == ["full", "hidden", "hidden"], fx_state(p)
        # Reading on into a later, unreached section stops at the boundary too.
        p.evaluate("() => { window.__spoken.length = 0; Classroom.playFrom(1); }")
        p.wait_for_timeout(600)
        assert "Beta text here." not in p.evaluate("window.__spoken")
        assert fx_state(p) == ["full", "hidden", "hidden"], fx_state(p)


def check_focus_live_section_read_aloud_becomes_current(page, fx, base):
    one = "# Focus lesson\n\n## Alpha step\n\nAlpha text here.\n"
    with focus_page(page, fx, lesson=one, auto=True) as p:
        fx_load(p, fx, base, n=1)
        (fx.dir / "lesson.md").write_text(one + "\n## Beta step\n\nBeta text here.\n", encoding="utf-8")
        p.wait_for_function("() => window.__spoken.includes('Beta text here.')", timeout=8000)
        p.wait_for_timeout(200)
        assert fx_state(p) == ["row", "full"], fx_state(p)


def check_focus_reopen_folded_section(page, fx, base):
    with focus_page(page, fx, auto=False) as p:
        fx_load(p, fx, base)
        p.locator("main section").first.locator(".fx-next-btn").click()
        assert fx_state(p) == ["row", "full", "hidden"], fx_state(p)
        p.locator("main section").first.locator(".fx-row").click()
        assert fx_state(p)[:2] == ["full", "full"], fx_state(p)
        assert "Alpha text here." in p.locator("main section").first.inner_text()
        p.locator("main section").first.locator(".fx-row").click()  # fold it again
        assert fx_state(p) == ["row", "full", "hidden"], fx_state(p)
        # Folding never replaces the section elements (speech items point at them).
        p.evaluate("() => { window.__sec = document.querySelector('main section'); }")
        p.locator("main section").nth(1).locator(".fx-next-btn").click()
        assert p.evaluate("() => window.__sec === document.querySelector('main section')")


def check_focus_new_section_waits_behind_next(page, fx, base):
    one = "# Focus lesson\n\n## Alpha step\n\nAlpha text here.\n"
    with focus_page(page, fx, lesson=one, auto=False) as p:
        fx_load(p, fx, base, n=1)
        p.wait_for_selector(".fx-wait", state="visible", timeout=5000)
        (fx.dir / "lesson.md").write_text(one + "\n## Beta step\n\nBeta text here.\n", encoding="utf-8")
        p.wait_for_function("() => document.querySelectorAll('main section').length === 2", timeout=8000)
        p.wait_for_selector(".fx-next-btn.fx-pulse", state="visible", timeout=5000)
        assert fx_state(p) == ["full", "hidden"], fx_state(p)
        assert not p.locator(".fx-wait").first.is_visible()
        p.locator(".fx-next-btn.fx-pulse").click(force=True)  # pulsing is never "stable"
        assert fx_state(p) == ["row", "full"], fx_state(p)
        assert p.locator(".fx-next-btn.fx-pulse").count() == 0
        # A re-render (lesson change) keeps the place by heading.
        (fx.dir / "lesson.md").write_text(one + "\n## Beta step\n\nBeta text here, edited.\n", encoding="utf-8")
        p.wait_for_function("() => document.querySelector('main').innerText.includes('edited')", timeout=8000)
        assert fx_state(p) == ["row", "full"], fx_state(p)


def check_focus_remembers_place_across_reload(page, fx, base):
    with focus_page(page, fx, auto=False) as p:
        fx_load(p, fx, base)
        p.locator("main section").first.locator(".fx-next-btn").click()
        p.locator("main section").nth(1).locator(".fx-next-btn").click()
        assert fx_state(p) == ["row", "row", "full"], fx_state(p)
        p.reload()
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 3", timeout=10000)
        assert fx_state(p) == ["row", "row", "full"], fx_state(p)
        # Reopening an earlier step doesn't move the saved place.
        p.locator("main section").first.locator(".fx-row").click()
        p.reload()
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 3", timeout=10000)
        assert fx_state(p) == ["row", "row", "full"], fx_state(p)


def check_focus_duplicate_headings_stay_apart(page, fx, base):
    dup = "# Dup lesson\n\n## Same\n\nFirst one.\n\n## Same\n\nSecond one.\n\n## Same\n\nThird one.\n"
    with focus_page(page, fx, lesson=dup, auto=False, pos="Same#1") as p:
        fx_load(p, fx, base)
        p.locator("main section").first.locator(".fx-next-btn").click()
        assert fx_state(p) == ["row", "full", "hidden"], fx_state(p)
        p.reload()
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 3", timeout=10000)
        assert fx_state(p) == ["row", "full", "hidden"], fx_state(p)


def check_focus_recap_says_lesson_complete(page, fx, base):
    done = "# Done lesson\n\n## Alpha step\n\nAlpha text here.\n\n## Recap\n\nThat was it.\n"
    with focus_page(page, fx, lesson=done, auto=False) as p:
        fx_load(p, fx, base, n=2)
        p.locator("main section").first.locator(".fx-next-btn").click()
        wait = p.locator("main section").last.locator(".fx-wait")
        assert "Lesson complete" in wait.inner_text()
        assert "fx-done" in wait.get_attribute("class")


def check_focus_does_not_flash_other_sections_on_rerender(page, fx, base):
    with focus_page(page, fx, auto=False) as p:
        fx_load(p, fx, base)
        p.evaluate("""() => {
            window.__most = 0;
            new MutationObserver(() => {
                const n = [...document.querySelectorAll('main section')].filter(s => s.offsetHeight > 0).length;
                window.__most = Math.max(window.__most, n);
            }).observe(document.getElementById('main'), { childList: true });
        }""")
        (fx.dir / "lesson.md").write_text(FOCUS_LESSON + "\n## Delta step\n\nDelta text.\n", encoding="utf-8")
        p.wait_for_function("() => document.querySelectorAll('main section').length === 4", timeout=8000)
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 4", timeout=3000)
        assert p.evaluate("window.__most") <= 1, p.evaluate("window.__most")
        assert fx_state(p) == ["full", "hidden", "hidden", "hidden"], fx_state(p)


def check_focus_toggle_off_restores_and_persists(page, fx, base):
    with focus_page(page, fx, auto=False) as p:
        fx_load(p, fx, base)
        assert fx_state(p) == ["full", "hidden", "hidden"]
        open_settings(p)  # the Focus toggle lives in Settings (#43)
        p.locator("#btn-focus").click()
        assert fx_state(p) == ["full", "full", "full"], fx_state(p)
        assert p.locator(".fx-next-btn:visible, .fx-row:visible, .fx-wait:visible").count() == 0
        assert p.locator("#btn-focus").get_attribute("aria-pressed") == "false"
        assert p.evaluate("localStorage.getItem('classroom.focus')") == "false"
        p.reload()
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 3", timeout=10000)
        assert fx_state(p) == ["full", "full", "full"], fx_state(p)
        open_settings(p)
        p.locator("#btn-focus").click()
        p.reload()
        p.wait_for_function("() => document.querySelectorAll('main section .fx-row').length === 3", timeout=10000)
        assert fx_state(p) == ["full", "hidden", "hidden"], fx_state(p)  # the saved place, step 1
        assert p.evaluate("localStorage.getItem('classroom.focus')") == "true"


def check_focus_with_chat_new_section_banner(page, fx, base):
    with focus_page(page, fx, auto=False, chat_full=True) as p:
        fx_load(p, fx, base)
        (fx.dir / "lesson.md").write_text(
            FOCUS_LESSON + "\n## Delta step\n\nDelta text.\n\n## Echo step\n\nEcho text.\n", encoding="utf-8")
        p.wait_for_selector("#chat-banner:not([hidden])", timeout=8000)
        p.locator("#chat-banner button").click()
        p.wait_for_function("() => document.querySelector('main').getClientRects().length > 0", timeout=3000)
        # The first NEW section, not just the next one.
        assert fx_state(p) == ["row", "row", "row", "full", "hidden"], fx_state(p)


# --- 46 code format ---


def _cf_lesson(fx, name, body):
    slug = f"{fx.slug}-{name}"
    lesson = ROOT / "lessons" / slug
    shutil.rmtree(lesson, ignore_errors=True)
    lesson.mkdir(parents=True)
    (lesson / "lesson.md").write_text(body, encoding="utf-8")
    return slug, lesson


def check_code_format(page, fx, base):
    """Code blocks: highlighted spans, line numbers, language label, copy without numbers,
    mermaid untouched, nothing new spoken, themes switch. Colour checks need the highlight.js CDN."""
    slug, lesson = _cf_lesson(fx, "cf", (
        "# CF\n\n## Code\n\nHere is code.\n\n"
        "```python\ndef add(a, b):\n    return a + b  # " + "x" * 200 + "\n\nprint(add(1, 2))\n```\n\n"
        "```\nfunction f() { return 1; }\n```\n\n"
        "```mermaid\ngraph LR\n  A --> B\n```\n"))
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".cf-block", timeout=10000)
        assert page.locator(".cf-block").count() == 2, "python and untagged blocks only; mermaid untouched"
        assert page.locator(".mermaid").count() >= 1 and page.locator(".cf-block .mermaid").count() == 0
        assert page.locator(".cf-lang").first.inner_text().strip().lower() == "python"
        gutter = page.locator(".cf-block").first.locator(".cf-gutter")
        assert gutter.inner_text().split() == ["1", "2", "3", "4"], gutter.inner_text()
        assert gutter.get_attribute("aria-hidden") == "true"
        assert page.evaluate("() => getComputedStyle(document.querySelector('.cf-gutter')).userSelect") == "none"
        assert "Cascadia Code" in page.evaluate("() => getComputedStyle(document.querySelector('.cf-block code')).fontFamily")
        # long lines scroll, they do not wrap
        assert page.evaluate("() => { const p = document.querySelector('.cf-block pre'); return p.scrollWidth > p.clientWidth; }")
        # nothing added is read aloud
        spoken = page.evaluate("() => Classroom.itemsFor(document.getElementById('main')).map(i => i.text).join(' | ')")
        for bad in ("Copy", "Wrap", "def add", "function f", "python"):
            assert bad not in spoken, f"{bad!r} would be spoken: {spoken}"
        # Wrap toggle
        page.locator(".cf-wrap").first.click()
        assert page.evaluate("() => { const p = document.querySelector('.cf-block pre'); return p.scrollWidth <= p.clientWidth; }")
        page.locator(".cf-wrap").first.click()
        # Copy puts the code, without line numbers, on the clipboard
        page.evaluate("""() => { window.__clip = null;
          Object.defineProperty(navigator, 'clipboard', { value: { writeText: (t) => { window.__clip = t; return Promise.resolve(); } }, configurable: true }); }""")
        try:
            page.locator(".cf-copy").first.click()
            page.wait_for_function("() => window.__clip !== null", timeout=3000)
            clip = page.evaluate("() => window.__clip")
            assert clip.startswith("def add(a, b):\n    return a + b") and "print(add(1, 2))" in clip, clip
            assert not clip.startswith("1") and "\n2\n" not in clip
            assert page.locator(".cf-copy").first.inner_text() == "Copied ✓"
        finally:
            page.evaluate("() => { delete navigator.clipboard; delete window.__clip; }")
        # Highlighting needs the CDN; only assert it when it loaded.
        try:
            page.wait_for_function("() => window.hljs", timeout=15000)
        except Exception:
            print("  (highlight.js CDN not reachable: colour checks skipped)")
            return
        page.wait_for_selector(".cf-block pre code .hljs-keyword", timeout=5000)
        assert page.locator(".cf-block").first.locator("pre code span[class^='hljs-']").count() > 2
        # the untagged block is auto-detected and its label updated
        assert page.locator(".cf-lang").nth(1).inner_text().strip() not in ("", "code")
        # theme follows the page: dark -> vs2015, light -> vs
        def sheets():
            return page.evaluate("() => ['cf-css-dark', 'cf-css-light'].map(id => document.getElementById(id).media === 'not all')")
        try:
            page.emulate_media(color_scheme="dark")
            page.wait_for_function("() => document.getElementById('cf-css-light').media === 'not all'", timeout=3000)
            assert sheets() == [False, True]
            assert page.evaluate("() => [...document.styleSheets].filter(s => s.href && s.href.includes('highlight')).filter(s => !s.media.mediaText.includes('not all')).map(s => s.href.split('/').pop()).join()") == 'vs2015.min.css'
            page.evaluate("() => document.documentElement.dataset.theme = 'light'")
            page.wait_for_function("() => document.getElementById('cf-css-dark').media === 'not all'", timeout=3000)
            assert sheets() == [True, False]
        finally:
            page.evaluate("() => delete document.documentElement.dataset.theme")
            page.emulate_media(color_scheme=None)
    finally:
        shutil.rmtree(lesson, ignore_errors=True)


def check_code_format_leaves_python_runner_alone(page, fx, base):
    """`python run` editors are not wrapped or reformatted; plain python blocks beside them are."""
    slug, lesson = _cf_lesson(fx, "cfpy", (
        "# P\n\n## Try\n\nRun.\n\n```python run\nprint('hi')\n```\n\n```python\nprint('plain')\n```\n"))
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".py-run textarea", timeout=15000)
        page.wait_for_selector(".cf-block", timeout=5000)
        assert page.locator(".py-run").count() == 1 and page.locator(".cf-block").count() == 1
        assert page.locator(".py-run .cf-block, .cf-block .py-run").count() == 0
        assert page.locator(".py-run .py-btn-run").count() == 1, "Run button still there"
        assert "print('hi')" in page.locator(".py-run textarea").input_value()
    finally:
        shutil.rmtree(lesson, ignore_errors=True)


def check_code_format_offline_and_plain(page, fx, base):
    """CDN blocked: one theme link, no growth in <script> tags over re-renders, blocks still numbered
    and copyable. `text` fences are never coloured."""
    md = "# O\n\n## Code\n\nHi {n}.\n\n```python\nprint(1)\n```\n\n```text\nplain output\n```\n"
    slug, lesson = _cf_lesson(fx, "cfoff", md.format(n=-1))
    page.route("**/cdnjs.cloudflare.com/**", lambda route: route.abort())
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".cf-block", timeout=10000)
        page.wait_for_timeout(500)
        scripts = page.evaluate("() => document.scripts.length")
        for n in range(3):
            (lesson / "lesson.md").write_text(md.format(n=n), encoding="utf-8")
            page.wait_for_function(f"() => document.querySelector('main p')?.textContent.includes('Hi {n}')", timeout=15000)
            page.wait_for_timeout(300)
        assert page.locator("#cf-css-dark").count() == 1 and page.locator("#cf-css-light").count() == 1
        assert page.evaluate("() => document.scripts.length") <= scripts, "scripts grew across re-renders"
        assert page.locator(".cf-block").count() == 2
        assert page.locator(".cf-gutter").first.inner_text().split() == ["1"] and page.locator(".cf-copy").count() == 2
        assert page.locator(".cf-block span[class^='hljs-']").count() == 0
    finally:
        page.unroute("**/cdnjs.cloudflare.com/**")
        shutil.rmtree(lesson, ignore_errors=True)


def check_code_format_text_fence_not_coloured(page, fx, base):
    """With the CDN reachable, a `text` fence stays uncoloured while python beside it is coloured."""
    slug, lesson = _cf_lesson(fx, "cftxt", "# T\n\n## Code\n\nHi.\n\n```python\nprint(1)\n```\n\n```text\nif x then return\n```\n")
    try:
        page.goto(f"{base}/viewer/?lesson={slug}")
        page.wait_for_selector(".cf-block", timeout=10000)
        try:
            page.wait_for_selector(".cf-block span[class^='hljs-']", timeout=15000)
        except Exception:
            print("  (highlight.js CDN not reachable: skipped)")
            return
        assert page.locator(".cf-block").nth(1).locator("span[class^='hljs-']").count() == 0
        assert page.locator(".cf-lang").nth(1).inner_text().strip() == "text"
    finally:
        shutil.rmtree(lesson, ignore_errors=True)


def check_code_format_in_chat(page, fx, base):
    """A fenced block in a chat message is formatted too. Restores chat.jsonl afterwards."""
    chat = fx.dir / "chat.jsonl"
    before = chat.read_bytes() if chat.exists() else None
    try:
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section", timeout=10000)
        fx.say("cf-chat", "Look:\n\n```python\nprint('chatty')\n```\n")
        fx.hook({"hook_event_name": "Stop"})
        page.wait_for_selector("#chat-log .cf-block", timeout=15000)
        assert page.locator("#chat-log .cf-gutter").count() >= 1
    finally:
        if before is None:
            chat.unlink(missing_ok=True)
        else:
            chat.write_bytes(before)


def _save_lesson_and_chat(fx):
    lesson, chat = fx.dir / "lesson.md", fx.dir / "chat.jsonl"
    return lesson, lesson.read_text(encoding="utf-8"), chat, (chat.read_text(encoding="utf-8") if chat.exists() else None)


def _restore_lesson_and_chat(page, fx, base, lesson, lesson_src, chat, chat_src):
    page.evaluate("() => Classroom.stop()")
    lesson.write_text(lesson_src, encoding="utf-8")
    if chat_src is None:
        chat.unlink(missing_ok=True)
    else:
        chat.write_text(chat_src, encoding="utf-8")
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")


def _learner_says(page, fx, text):
    _append_chat(fx, {"id": "you-" + text, "kind": "you", "text": text})
    page.wait_for_function("(t) => [...document.querySelectorAll('.msg.you')].some(m => m.textContent === t)", arg=text, timeout=5000)


# Records [time, text] for every item, each lasting 5 ms.
TIMED_ENGINE = """() => { window.__said = [];
    Classroom.setEngine({ speak(item, done) { window.__said.push([Date.now(), item.text]); setTimeout(done, 5); },
        cancel() {}, pause() {}, resume() {}, paused() { return false; } }); }"""


def check_read_again_keeps_held_section(page, fx, base):
    """Read again / Read whole chat while a section waits for the reply don't drop it."""
    lesson, lesson_src, chat, chat_src = _save_lesson_and_chat(fx)
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector(".msg.claude .reread")
    n0 = page.evaluate("() => document.querySelectorAll('main section').length")
    page.evaluate("() => { Classroom.settings.chatWait = 20000; window.__spoken.length = 0; }")
    try:
        _learner_says(page, fx, "And then?")
        lesson.write_text(lesson_src + "\n## Kept idea\n\nStill read.\n", encoding="utf-8")
        page.wait_for_function("(n) => document.querySelectorAll('main section').length === n", arg=n0 + 1, timeout=8000)
        page.locator(".msg.claude .reread").first.click()
        page.wait_for_function("() => window.__spoken.length > 0", timeout=2000)
        page.click("#btn-read-chat")
        page.wait_for_timeout(800)
        assert "Still read." not in page.evaluate("() => window.__spoken"), "still waiting for the reply"
        _append_chat(fx, {"id": "kept-reply", "kind": "claude", "text": "Here it is."})
        page.wait_for_function("() => window.__spoken.includes('Still read.')", timeout=8000)
        spoken = page.evaluate("() => window.__spoken")
        assert spoken.index("Here it is.") < spoken.index("Kept idea"), spoken
    finally:
        _restore_lesson_and_chat(page, fx, base, lesson, lesson_src, chat, chat_src)


def check_turn_without_reply_waits_once(page, fx, base):
    """A turn that writes no reply makes one section wait, not every section."""
    lesson, lesson_src, chat, chat_src = _save_lesson_and_chat(fx)
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    n0 = page.evaluate("() => document.querySelectorAll('main section').length")
    page.evaluate(TIMED_ENGINE)
    page.evaluate("() => { Classroom.settings.chatWait = 2500; }")
    try:
        _learner_says(page, fx, "Two more please.")
        lesson.write_text(lesson_src + "\n## Idea three\n\nGamma.\n", encoding="utf-8")
        page.wait_for_function("(n) => document.querySelectorAll('main section').length === n", arg=n0 + 1, timeout=8000)
        lesson.write_text(lesson_src + "\n## Idea three\n\nGamma.\n\n## Idea four\n\nDelta.\n", encoding="utf-8")
        page.wait_for_function("() => window.__said.some(x => x[1] === 'Delta.')", timeout=20000)
        said = page.evaluate("() => window.__said")
        gamma = next(t for t, s in said if s == "Gamma.")
        four = next(t for t, s in said if s == "Idea four")
        assert four - gamma < 1500, f"the second section waited again: {four - gamma} ms"
    finally:
        _restore_lesson_and_chat(page, fx, base, lesson, lesson_src, chat, chat_src)


def check_late_reply_and_next_message_in_one_poll(page, fx, base):
    """A flushed reply plus the next message, together: the reply ends this wait only."""
    lesson, lesson_src, chat, chat_src = _save_lesson_and_chat(fx)
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    n0 = page.evaluate("() => document.querySelectorAll('main section').length")
    page.evaluate("() => { Classroom.settings.chatWait = 15000; window.__spoken.length = 0; }")
    try:
        _learner_says(page, fx, "Go.")
        lesson.write_text(lesson_src + "\n## Late idea\n\nLate text.\n", encoding="utf-8")
        page.wait_for_function("(n) => document.querySelectorAll('main section').length === n", arg=n0 + 1, timeout=8000)
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "late-c", "kind": "claude", "text": "Late reply."}) + "\n")
            f.write(json.dumps({"id": "late-y", "kind": "you", "text": "Next one."}) + "\n")
        page.wait_for_function("() => window.__spoken.includes('Late text.')", timeout=5000)
        spoken = page.evaluate("() => window.__spoken")
        assert spoken.index("Late reply.") < spoken.index("Late idea"), spoken
        # The new message re-armed the wait for the next section.
        lesson.write_text(lesson_src + "\n## Late idea\n\nLate text.\n\n## Next idea\n\nNext text.\n", encoding="utf-8")
        page.wait_for_function("(n) => document.querySelectorAll('main section').length === n", arg=n0 + 2, timeout=8000)
        page.wait_for_timeout(800)
        assert "Next text." not in page.evaluate("() => window.__spoken"), "the next section waits for its reply"
        _append_chat(fx, {"id": "late-c2", "kind": "claude", "text": "Second reply."})
        page.wait_for_function("() => window.__spoken.includes('Next text.')", timeout=5000)
    finally:
        _restore_lesson_and_chat(page, fx, base, lesson, lesson_src, chat, chat_src)


def check_speech_error_does_not_wedge_queue(page, fx, base):
    """An interrupted or canceled utterance ends its item, exactly once."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    page.evaluate("""() => { window.__said = [];
        speechSynthesis.speak = (u) => { window.__said.push(u.text); setTimeout(() => {
            if (u.text === 'E1') u.onerror({ error: 'interrupted' });
            else if (u.text === 'E2') { u.onerror({ error: 'canceled' }); u.onend(); }
            else u.onend(); }, 5); };
        Classroom.enqueue(['E1', 'E2', 'E3', 'E4'].map(text => ({ text }))); }""")
    page.wait_for_timeout(500)
    assert page.evaluate("() => window.__said") == ["E1", "E2", "E3", "E4"], page.evaluate("() => window.__said")
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")


def check_play_does_not_talk_over_chat_tab(page, fx, base):
    """Header Play skips the wait for a reply, but not a chat tab that is talking."""
    focus_off(page)  # about core's queue; focus mode's Play reads only the current section
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("main section")
    beat = "(a) => __bc.postMessage({ type: 'chat-tab', speaking: a[1], item: 1, lesson: a[0] })"
    page.evaluate("() => { window.__spoken.length = 0; window.__bc = new BroadcastChannel('classroom'); }")
    try:
        page.evaluate(beat, [fx.slug, True])
        page.wait_for_timeout(300)
        page.evaluate("() => Classroom.enqueue([{ text: 'Over the chat tab', source: 'lesson' }])")
        page.click("#btn-play")
        page.wait_for_timeout(800)
        assert "Over the chat tab" not in page.evaluate("() => window.__spoken"), "Play talked over the chat tab"
        page.evaluate(beat, [fx.slug, False])
        page.wait_for_function("() => window.__spoken.includes('Over the chat tab')", timeout=3000)
    finally:
        page.evaluate("(slug) => { __bc.postMessage({ type: 'chat-tab-closed', lesson: slug }); __bc.close(); }", fx.slug)
        focus_default(page)
        page.goto(f"{base}/viewer/?lesson={fx.slug}")
        page.wait_for_selector("main section")


def check_next_keeps_queued_chat_reply(page, fx, base):
    """#56: Next drops the rest of the lesson queue but keeps a chat reply waiting to be read."""
    with focus_page(page, fx) as p:
        fx_load(p, fx, base)
        p.evaluate("""() => {
            window.__spoken.length = 0;
            const el = document.querySelector('main section p');
            Classroom.hold('t', true);
            Classroom.enqueue([{ el, text: 'Old lesson line', source: 'lesson' }]);
            Classroom.enqueue([{ text: 'Reply kept', source: 'chat' }]);
        }""")
        p.locator("main section").first.locator(".fx-next-btn").click()
        p.evaluate("() => Classroom.hold('t', false)")
        p.wait_for_function("() => window.__spoken.includes('Beta text here.')", timeout=5000)
        spoken = p.evaluate("window.__spoken")
        assert "Reply kept" in spoken, spoken
        assert spoken.index("Reply kept") < spoken.index("Beta text here."), spoken
        assert "Old lesson line" not in spoken, spoken


# Every check_* function defined above runs, in file order. Add new checks as
# functions; there is no list to edit.
def check_lighthouse_theme_default_icon_and_favicon(page, fx, base):
    """The lighthouse palette is on by default, with a lighthouse icon in the header and the tab."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.evaluate("() => localStorage.removeItem('classroom.palette')")
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("header .title .mark.lh svg", state="attached", timeout=5000)
    assert page.evaluate("() => document.documentElement.dataset.palette") == "lighthouse"
    page.emulate_media(color_scheme="light")
    assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") == "rgb(238, 244, 248)"
    page.emulate_media(color_scheme="dark")
    assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") == "rgb(11, 22, 34)"
    page.emulate_media(color_scheme="light")
    fav = page.evaluate("() => document.querySelector('link[rel=icon]')?.href || ''")
    assert fav.startswith("data:image/svg+xml"), fav


def check_lighthouse_theme_switch_persists(page, fx, base):
    """Settings > Theme switches to Classic (the original tokens) and the choice survives a reload."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    open_settings(page)
    page.select_option("#palette", "classic")
    assert page.evaluate("() => document.documentElement.dataset.palette") is None
    assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") == "rgb(245, 242, 255)"
    page.reload()
    page.wait_for_selector("#palette", state="attached")
    assert page.evaluate("() => document.getElementById('palette').value") == "classic"
    assert page.evaluate("() => document.documentElement.dataset.palette") is None
    page.evaluate("() => localStorage.removeItem('classroom.palette')")


def check_lighthouse_classic_contrast(page, fx, base):
    """The Classic palette still passes AA when chosen (the redesign check covers Lighthouse, the default)."""
    def run():
        page.evaluate("() => document.documentElement.removeAttribute('data-palette')")
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            for name, r in page.evaluate(CONTRAST_JS).items():
                assert r != "missing" and r >= 4.5, f"classic {scheme}: {name} {r}"
        page.evaluate("() => document.documentElement.setAttribute('data-palette', 'lighthouse')")
    _with_redesign_lesson(page, fx, base, run)


def check_lighthouse_beam_while_reading(page, fx, base):
    """The beam turns on while something is read aloud and off again when reading ends."""
    page.goto(f"{base}/viewer/?lesson={fx.slug}")
    page.wait_for_selector("header .title .mark.lh", state="attached")
    page.wait_for_selector("main section", timeout=10000)
    page.evaluate("""() => { window.__lhOn = false; const m = document.querySelector('header .title .mark');
        new MutationObserver(() => { if (m.classList.contains('on')) window.__lhOn = true; })
          .observe(m, { attributes: true, attributeFilter: ['class'] }); }""")
    page.click("#btn-play")
    page.wait_for_function("() => window.__lhOn === true", timeout=5000)
    page.click("#btn-stop")
    page.wait_for_function("() => !document.querySelector('header .title .mark').classList.contains('on')", timeout=5000)


CHECKS = [f for name, f in list(globals().items()) if name.startswith("check_") and callable(f)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, str(ROOT / "viewer" / "serve.py"), str(args.port)],
                              cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = 0
    try:
        time.sleep(0.8)
        with Fixture(args.port) as fx, sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1300, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(FAKE_SPEECH)
            for check in CHECKS:
                try:
                    check(page, fx, base)
                    print(f"PASS {check.__name__}")
                except Exception as e:
                    failures += 1
                    print(f"FAIL {check.__name__}: {e}")
            if errors:
                failures += 1
                print(f"FAIL page errors: {errors}")
            browser.close()
    finally:
        server.terminate()
    print(f"all {len(CHECKS)} checks passed" if not failures else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
