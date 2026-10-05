// Progress & wins (#44): a progress bar over the lesson's sections, a short
// celebration when Claude grades an answer, and a points / streak chip.
//
// Progress bar: one dot per lesson section written so far (a `##` heading; the
// intro before the first one is not a section). A section is done once it has
// been read aloud, scrolled past, or folded away by focus mode. The one being
// read is current, else the first one not yet done. Until the lesson has a
// "Recap" section a quiet "more coming" follows the dots. Click a dot to jump to
// its section. Done sections are remembered per lesson, by heading, in
// localStorage. Pressing play on a later section credits nothing before it.
//
// Celebration: a Claude chat message starting "✓ Correct" plays a short, soft
// WebAudio chime and a confetti burst; "✗ Not quite" plays a gentler cue with no
// colour flash. Entries delivered by the first chat load are history and never
// celebrate; several entries arriving together celebrate once. Sound needs a
// running AudioContext, created on the first click or key press, and is skipped
// otherwise (the visual stays). While a chat-only tab (?view=chat) for the same
// lesson is open, this tab stays silent and leaves the sound to it. Motion is
// skipped under prefers-reduced-motion. The mute toggle ("classroom.winsMuted")
// lives in Settings when the viewer has one.
//
// Points and streak come from the attempts in progress/concepts.json (one
// format, see progress/README.md): 10 per correct attempt, 5 for a correct one
// that needed hints (rating "hard", or "hints: N" in the note), and the streak
// is the run of consecutive local days with an attempt, ending today (or
// yesterday, so the streak is still alive in the morning). No file, no chip.
(() => {
  const C = window.Classroom;
  if (!C || !C.slug) return;

  const MUTE_KEY = "classroom.winsMuted";
  const DONE_KEY = "classroom.progressDone." + C.slug;
  const FILE = "../progress/concepts.json";
  const chatOnly = new URLSearchParams(location.search).get("view") === "chat";

  C.addStyle(`
    #wins-progress { display: inline-flex; align-items: center; gap: 6px; }
    #wins-progress[hidden], #wins-chip[hidden] { display: none; }
    #wins-progress .count { font-weight: 700; font-size: 13px; color: var(--text); white-space: nowrap; }
    #wins-progress .more { font-size: 12px; color: var(--muted); white-space: nowrap; }
    #wins-progress .dots { display: inline-flex; gap: 4px; align-items: center; }
    #wins-progress .dot { width: 12px; height: 12px; min-height: 0; padding: 0; border-radius: 50%; cursor: pointer;
      border: 2px solid var(--muted); background: transparent; transition: transform .15s, background .15s; }
    #wins-progress .dots.tight .dot { width: 8px; height: 8px; border-width: 1.5px; }
    #wins-progress .dot:hover { transform: scale(1.25); }
    #wins-progress .dot.done { background: var(--success, #2f9e5a); border-color: var(--success, #2f9e5a); }
    #wins-progress .dot.current { background: var(--accent-2, var(--accent)); border-color: var(--accent-2, var(--accent));
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-2, var(--accent)) 30%, transparent); }
    #wins-chip { font-size: 13px; font-weight: 700; padding: 2px 9px; border-radius: 999px;
      background: var(--surface); border: 2px solid var(--accent-2, var(--accent)); color: var(--text); white-space: nowrap; }
    #wins-mute { font-size: 15px; padding: 3px 10px; }
    #wins-fx { position: fixed; pointer-events: none; z-index: 50; width: 0; height: 0; }
    #wins-fx .bit { position: absolute; left: 0; top: 0; width: 10px; height: 10px; border-radius: 3px; }
    #wins-fx .gentle { position: absolute; left: -60px; top: 0; width: 120px; text-align: center; font-size: 15px;
      font-weight: 600; color: var(--text); background: var(--surface); border: 2px solid var(--accent, #2f6fdb);
      border-radius: var(--radius, 12px); padding: 4px 8px; }
    @media (prefers-reduced-motion: reduce) { #wins-progress .dot { transition: none; } }
  `);

  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const muted = () => C.load(MUTE_KEY, "false") === "true";

  // ---------- progress bar ----------
  const wrap = document.createElement("span");
  wrap.id = "wins-progress";
  wrap.hidden = true;
  wrap.setAttribute("aria-label", "Lesson progress");
  const count = document.createElement("span");
  count.className = "count";
  const more = document.createElement("span");
  more.className = "more";
  more.textContent = "more coming";
  const dotsEl = document.createElement("span");
  dotsEl.className = "dots";
  wrap.append(dotsEl, count, more);

  let done = new Set(); // heading texts of finished sections
  try { done = new Set(JSON.parse(C.load(DONE_KEY, "[]"))); } catch { /* start empty */ }
  let reading = -1;     // index of the section being read, or -1
  const saveDone = () => C.save(DONE_KEY, JSON.stringify([...done]));

  const headingOf = (s) => s.querySelector("h2")?.textContent?.trim() || "";
  // Only sections with a "##" heading; the intro before the first one is not one.
  const sections = () => [...document.querySelectorAll("main section")].filter((s) => s.querySelector("h2"));

  // Scrolled past: laid out (so hidden sections never count) and wholly above the header.
  function markScrolledPast() {
    const top = (document.querySelector("header")?.getBoundingClientRect().bottom || 0);
    let changed = false;
    for (const s of sections()) {
      if (!s.getClientRects().length) continue;
      const h = headingOf(s);
      if (h && !done.has(h) && s.getBoundingClientRect().bottom < top) { done.add(h); changed = true; }
    }
    return changed;
  }

  // Updates the dots in place, so a focused dot keeps keyboard focus.
  function drawBar() {
    const secs = sections();
    if (!secs.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    const heads = secs.map(headingOf);
    let current = reading;
    if (current < 0) current = heads.findIndex((h) => !done.has(h));
    while (dotsEl.children.length > secs.length) dotsEl.lastChild.remove();
    while (dotsEl.children.length < secs.length) {
      const b = document.createElement("button");
      dotsEl.appendChild(b);
    }
    dotsEl.classList.toggle("tight", secs.length > 12);
    [...dotsEl.children].forEach((b, i) => {
      const isDone = done.has(heads[i]);
      b.className = "dot" + (i === current ? " current" : isDone ? " done" : "");
      b.title = heads[i] || `Section ${i + 1}`;
      b.setAttribute("aria-label", b.title);
      b.onclick = () => secs[i].scrollIntoView({ block: "start", behavior: reduced() ? "auto" : "smooth" });
    });
    count.textContent = `${heads.filter((h) => done.has(h)).length} / ${secs.length}`;
    more.hidden = heads.some((h) => /^recap\b/i.test(h));
  }

  C.on("speechStart", (item) => {
    const s = item.el?.closest?.("section");
    const secs = sections();
    const i = s ? secs.indexOf(s) : -1;
    if (i < 0) return;
    // Moving on to the next section means the one before it was read; a jump
    // somewhere else credits nothing.
    if (reading >= 0 && i === reading + 1 && secs[reading] && headingOf(secs[reading])) {
      done.add(headingOf(secs[reading]));
      saveDone();
    }
    reading = i;
    drawBar();
  });
  C.on("idle", () => {
    // The queue ran dry: whatever was being read has been read to the end.
    const s = sections()[reading];
    if (s && headingOf(s)) { done.add(headingOf(s)); saveDone(); }
    reading = -1;
    drawBar();
  });
  C.on("render", () => {
    markScrolledPast();
    // Forget headings that are no longer in the lesson.
    const heads = new Set(sections().map(headingOf));
    const kept = [...done].filter((h) => heads.has(h));
    if (kept.length !== done.size) { done = new Set(kept); saveDone(); }
    drawBar();
  });
  let scrollTimer = 0;
  addEventListener("scroll", () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => { if (markScrolledPast()) { saveDone(); drawBar(); } }, 200);
  }, { passive: true });

  // Focus mode folds a section once the learner has moved past it: that is done.
  // Feature-detected on the class it sets (fx-folded); without focus mode this never fires.
  function watchFolds() {
    const main = document.getElementById("main");
    if (!main || typeof MutationObserver !== "function") return;
    new MutationObserver(() => {
      let changed = false;
      for (const s of sections()) {
        const h = headingOf(s);
        if (h && s.classList.contains("fx-folded") && !done.has(h)) { done.add(h); changed = true; }
      }
      if (changed) { saveDone(); drawBar(); }
    }).observe(main, { attributes: true, attributeFilter: ["class"], subtree: true });
  }

  // ---------- points and streak ----------
  const chip = document.createElement("span");
  chip.id = "wins-chip";
  chip.hidden = true;
  chip.title = "Points: 10 per correct answer, 5 when you needed hints. Streak: days in a row you practised.";

  const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

  function stats(concepts) {
    let points = 0;
    const days = new Set();
    for (const c of Object.values(concepts || {})) {
      for (const a of c.attempts || []) {
        const t = new Date(a.at);
        if (!isNaN(t)) days.add(dayKey(t));
        if (!a.correct) continue;
        const hinted = a.rating === "hard" || /hints?\s*:\s*[1-9]/i.test(typeof a.note === "string" ? a.note : "");
        points += hinted ? 5 : 10;
      }
    }
    let streak = 0;
    const day = new Date();
    if (!days.has(dayKey(day))) day.setDate(day.getDate() - 1); // still alive until today ends
    while (days.has(dayKey(day))) { streak++; day.setDate(day.getDate() - 1); }
    return { points, streak };
  }

  async function updateChip() {
    try {
      const res = await fetch(FILE, { cache: "no-store" });
      if (!res.ok) { chip.hidden = true; return; }
      const { points, streak } = stats((await res.json()).concepts);
      chip.textContent = `⚡ ${points} · 🔥 ${streak}`;
      chip.hidden = false;
    } catch { chip.hidden = true; }
  }

  // ---------- which tab makes the sound ----------
  // 15-chat-reading's chat-only tab sends a "chat-tab" heartbeat; while one is
  // alive for this lesson the main tab celebrates visually only.
  let chatTabSeen = 0;
  if (!chatOnly && typeof BroadcastChannel === "function") {
    const bc = new BroadcastChannel("classroom");
    bc.onmessage = (e) => {
      const m = e.data || {};
      if (m.lesson !== C.slug) return;
      if (m.type === "chat-tab") chatTabSeen = Date.now();
      else if (m.type === "chat-tab-closed") chatTabSeen = 0;
    };
  }
  const chatTabAlive = () => Date.now() - chatTabSeen < 5000;

  // ---------- sound and motion ----------
  // The AudioContext starts on the first click or key press (browsers keep it
  // suspended until then). Until it is running there is no sound, and nothing
  // is queued to play later.
  let audio = null; // { ctx, master }
  function unlockAudio() {
    if (audio) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const master = ctx.createGain();
      master.gain.value = 0.12;
      master.connect(ctx.destination);
      audio = { ctx, master };
      ctx.resume?.();
    } catch { /* no audio is fine */ }
  }
  for (const evt of ["pointerdown", "keydown"]) addEventListener(evt, unlockAudio, { capture: true, once: true });

  function tones(notes) {
    if (muted() || chatTabAlive() || !audio || audio.ctx.state !== "running") return;
    try {
      const { ctx, master } = audio;
      const t0 = ctx.currentTime;
      for (const [freq, at, len, vol] of notes) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t0 + at);
        gain.gain.exponentialRampToValueAtTime(vol, t0 + at + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + len);
        osc.connect(gain); gain.connect(master);
        osc.start(t0 + at); osc.stop(t0 + at + len + 0.05);
      }
    } catch { /* no audio is fine */ }
  }

  function fxHost() {
    let fx = document.getElementById("wins-fx");
    if (!fx) { fx = document.createElement("div"); fx.id = "wins-fx"; document.body.appendChild(fx); }
    // Anchor near the chat panel (or the middle of the page without one).
    const r = document.querySelector("aside")?.getBoundingClientRect();
    fx.style.left = (r && r.width ? r.left + r.width / 2 : innerWidth / 2) + "px";
    fx.style.top = (r && r.height ? r.top + Math.min(r.height / 2, 200) : 160) + "px";
    return fx;
  }

  function burst() {
    if (reduced()) return;
    const fx = fxHost();
    const colors = ["var(--accent, #2f6fdb)", "var(--accent-2, #f5a623)", "var(--success, #2f9e5a)", "#ff6fb1", "#ffd23f"];
    for (let i = 0; i < 18; i++) {
      const bit = document.createElement("span");
      bit.className = "bit";
      bit.style.background = colors[i % colors.length];
      fx.appendChild(bit);
      const a = (Math.PI * 2 * i) / 18 + Math.random() * 0.3, d = 70 + Math.random() * 70;
      const anim = bit.animate([
        { transform: "translate(0,0) scale(1)", opacity: 1 },
        { transform: `translate(${Math.cos(a) * d}px, ${Math.sin(a) * d - 30}px) rotate(${Math.random() * 360}deg) scale(.6)`, opacity: 0 },
      ], { duration: 800, easing: "cubic-bezier(.2,.7,.3,1)" });
      anim.onfinish = () => bit.remove();
    }
  }

  function gentle() {
    if (reduced()) return;
    const fx = fxHost();
    const note = document.createElement("div");
    note.className = "gentle";
    note.textContent = "Good try, keep going";
    fx.appendChild(note);
    const anim = note.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)", offset: .25 },
      { opacity: 1, transform: "translateY(0)", offset: .8 }, { opacity: 0 }], { duration: 900 });
    anim.onfinish = () => note.remove();
  }

  function celebrate() {
    // Rising major triad: C5 E5 G5, short and soft (the master gain keeps it quiet).
    tones([[523.25, 0, 0.2, 0.6], [659.25, 0.09, 0.2, 0.6], [783.99, 0.18, 0.34, 0.65]]);
    burst();
  }
  function encourage() {
    // Two low, warm notes, falling gently. No buzzer.
    tones([[392, 0, 0.25, 0.4], [349.23, 0.15, 0.32, 0.4]]);
    gentle();
  }

  // ---------- chat entries ----------
  // Everything the first chat load delivers is history. That load is a single
  // synchronous batch, so "the first tick" covers it. If the first entry only
  // turns up well after the page loaded, the chat was empty then and it is live.
  const READY_GRACE_MS = 4000;
  let readyAt = Date.now();
  let first = true;       // no chatEntry seen yet
  let history = false;    // currently inside the history batch
  let pending = null;     // outcome of the live batch: "ok", "miss" or null
  C.on("ready", () => { readyAt = Date.now(); });

  C.on("chatEntry", (entry) => {
    if (first) {
      first = false;
      if (Date.now() - readyAt < READY_GRACE_MS) {
        history = true;
        setTimeout(() => { history = false; }, 0);
      }
    }
    if (history || entry.kind !== "claude") return;
    const text = String(entry.text || "").trim();
    const ok = /^✓\s*correct/i.test(text), miss = /^✗\s*not quite/i.test(text);
    if (!ok && !miss) return;
    const start = pending === null;
    pending = ok || pending === "ok" ? "ok" : "miss";
    if (!start) return;
    // Entries that arrive together (one poll) celebrate once.
    setTimeout(() => {
      const outcome = pending;
      pending = null;
      if (outcome === "ok") {
        celebrate();
        updateChip();
        setTimeout(updateChip, 2500); // Claude records the attempt around now
      } else if (outcome === "miss") {
        encourage();
      }
    }, 0);
  });

  // ---------- header ----------
  C.on("ready", () => {
    const mute = document.createElement("button");
    mute.id = "wins-mute";
    const paint = () => {
      mute.textContent = muted() ? "🔇 Wins sound" : "🔊 Wins sound";
      mute.title = muted() ? "Celebration sounds are off. Click to turn them on." : "Celebration sounds are on. Click to mute.";
      mute.setAttribute("aria-label", mute.title);
      mute.setAttribute("aria-pressed", String(muted()));
    };
    mute.onclick = () => { C.save(MUTE_KEY, String(!muted())); paint(); };
    paint();
    C.addControl(wrap);
    C.addControl(chip);
    (C.addSetting ? C.addSetting : C.addControl)(mute);
    watchFolds();
    updateChip();
    setInterval(updateChip, 15000);
  });
})();
