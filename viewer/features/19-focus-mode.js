// Focus mode: one section at a time with a Next button (#45).
//
// - "🎯 Focus" toggle (Settings if Classroom.addSetting exists, else the header),
//   remembered in classroom.focus, ON by default.
// - On: only the current section is shown in full. Finished sections fold into a
//   slim "✓ Step 2 · Title" row (click to reopen / fold again). Sections not yet
//   reached are hidden. Off: the page is exactly as before.
// - The place is remembered per lesson (classroom.focus.pos.<slug>); with nothing
//   saved the lesson opens at its newest section.
// - A big "Next →" at the end of the current section folds it and moves on:
//   scrolls to the section top and reads that section only, if auto-read is on.
//   With no next section yet it says "Waiting for Claude's next step…", or
//   "Lesson complete" after a Recap.
// - Header Play reads the current section only, then stops at Next.
// - A section added while the learner is mid-section stays hidden and Next pulses,
//   unless auto-read starts reading it, in which case it becomes current.
// - Only classes are toggled (never detaching section elements, which speech items
//   point at). core re-renders <main> on every lesson change, so the state is kept
//   by heading (plus occurrence number) and re-applied after each "render".
(() => {
  const C = window.Classroom;
  if (!C || !C.slug) return;
  if (new URLSearchParams(location.search).get("view") === "chat") return; // chat-only tab

  const posKey = "classroom.focus.pos." + C.slug;
  let on = C.load("classroom.focus", "true") !== "false";
  let curKey = null;       // heading#n of the current section
  let curIdx = 0;
  const open = new Set();  // keys of folded sections the learner reopened
  const fresh = new Set(); // keys of sections that arrived live and are not reached yet
  let known = null;        // keys seen at the previous render
  let topPending = null;   // section to scroll to once its first line starts
  let paused = false;      // Pause pressed (fallback when core has no Classroom.paused)
  let toggle;

  const mainEl = () => document.getElementById("main");
  const secs = () => [...mainEl().querySelectorAll(":scope > section")];
  const head = (s) => s.querySelector("h2")?.textContent ?? "";
  // Heading plus occurrence number, so two sections with the same title stay apart.
  const keysOf = (list) => {
    const seen = {};
    return list.map(s => { const h = head(s); seen[h] = (seen[h] || 0) + 1; return h + "#" + seen[h]; });
  };
  const isRecap = (s) => /^\s*recap\b/i.test(head(s));

  C.addStyle(`
    main section { scroll-margin-top: calc(var(--header-h, 70px) + 10px); }
    .fx-row, .fx-next { display: none; }
    /* Until focus has marked a freshly rendered section, keep it out of sight (no flash of the
       whole lesson while diagrams render). Collapsed rather than display:none so mermaid can measure. */
    main.fx-on section:not([data-fx]) { visibility: hidden; height: 0; overflow: hidden; margin: 0;
      padding-top: 0; padding-bottom: 0; border-width: 0; }
    main.fx-on section.fx-hidden { display: none; }
    main.fx-on section.fx-folded { padding: 0; }
    main.fx-on section.fx-folded > :not(.fx-row) { display: none; }
    main.fx-on section.fx-folded > .fx-row, main.fx-on section.fx-open > .fx-row { display: flex; }
    .fx-row { width: 100%; align-items: center; gap: 10px; text-align: left; border: 0; background: transparent;
      color: var(--text); font-weight: 600; padding: 12px 4px; border-radius: var(--radius, 12px); }
    main.fx-on section.fx-folded > .fx-row { padding: 14px 20px; }
    .fx-row .fx-chev { margin-left: auto; color: var(--muted); font-weight: 400; font-size: .85em; }
    main.fx-on section.fx-open > .fx-row { font-size: .85em; color: var(--muted); padding: 8px 0 0; }
    main.fx-on section.fx-current > .fx-next { display: flex; justify-content: center; padding: 20px 0 10px; }
    .fx-next-btn { font-size: 1.2em; font-weight: 700; padding: 14px 40px; border: 0; cursor: pointer;
      border-radius: calc(var(--radius, 12px) * 2); background: var(--accent-2, var(--accent)); color: var(--accent-text, #fff);
      box-shadow: var(--card-shadow, 0 3px 0 rgba(0, 0, 0, .18)); }
    .fx-next-btn[hidden], .fx-wait[hidden] { display: none; }
    .fx-wait { color: var(--muted); font-size: 1.05em; animation: fx-fade 1.8s ease-in-out infinite; }
    .fx-wait.fx-done { animation: none; color: var(--text); font-weight: 700; }
    .fx-next-btn.fx-pulse { animation: fx-pulse 1.2s ease-in-out infinite; }
    @keyframes fx-pulse { 50% { transform: scale(1.07); box-shadow: 0 0 0 8px var(--speaking); } }
    @keyframes fx-fade { 50% { opacity: .4; } }
    @media (prefers-reduced-motion: reduce) { .fx-wait, .fx-next-btn.fx-pulse { animation: none; } }
    #btn-focus[aria-pressed="true"] { background: var(--accent); color: var(--accent-text, #fff); border-color: var(--accent); }
  `);

  function ensureParts(sec) {
    if (!sec.querySelector(":scope > .fx-row")) {
      const row = document.createElement("button");
      row.className = "fx-row no-read";
      row.onclick = () => {
        const k = keysOf(secs())[secs().indexOf(sec)];
        if (open.has(k)) open.delete(k); else open.add(k);
        apply();
      };
      sec.prepend(row);
    }
    if (!sec.querySelector(":scope > .fx-next")) {
      const box = document.createElement("div");
      box.className = "fx-next no-read";
      const btn = document.createElement("button");
      btn.className = "fx-next-btn"; btn.textContent = "Next →";
      btn.title = "Fold this step and go on to the next one";
      btn.onclick = next;
      const wait = document.createElement("span");
      wait.className = "fx-wait";
      box.append(btn, wait);
      sec.appendChild(box);
    }
  }

  function savePos() { C.save(posKey, JSON.stringify({ k: curKey, i: curIdx })); }

  function apply() {
    const main = mainEl();
    main.classList.toggle("fx-on", on);
    const s = secs();
    if (toggle) toggle.setAttribute("aria-pressed", String(on));
    if (!s.length) return;
    const keys = keysOf(s);
    if (curKey === null) {
      // First look at this lesson: where the learner got to, else its newest section.
      let pos = null;
      try { pos = JSON.parse(C.load(posKey, "null")); } catch {}
      const at = pos ? keys.indexOf(pos.k) : -1;
      curIdx = at >= 0 ? at : pos && Number.isInteger(pos.i) ? Math.min(pos.i, s.length - 1) : s.length - 1;
      curKey = keys[curIdx];
      savePos();
    } else {
      const f = keys.indexOf(curKey);
      curIdx = f >= 0 ? f : Math.min(curIdx, s.length - 1);
      curKey = keys[curIdx];
    }
    const pulse = [...fresh].some(k => keys.indexOf(k) > curIdx);
    let step = 0;
    s.forEach((sec, i) => {
      ensureParts(sec);
      sec.dataset.fx = "1";
      const k = keys[i];
      const hasHead = !!sec.querySelector("h2");
      if (hasHead) step++;
      const isOpen = i < curIdx && open.has(k);
      sec.classList.toggle("fx-current", i === curIdx);
      // Only while on: 18-progress-wins counts an fx-folded section as done.
      sec.classList.toggle("fx-folded", on && i < curIdx && !isOpen);
      sec.classList.toggle("fx-open", isOpen);
      sec.classList.toggle("fx-hidden", i > curIdx);
      const row = sec.querySelector(":scope > .fx-row");
      row.replaceChildren(
        document.createTextNode(`✓ ${hasHead ? "Step " + step + " · " + head(sec) : "Intro"}`),
        Object.assign(document.createElement("span"), { className: "fx-chev", textContent: isOpen ? "▾ Hide" : "▸ Show" }));
      row.setAttribute("aria-expanded", String(isOpen));
      const hasNext = i < s.length - 1;
      const btn = sec.querySelector(":scope > .fx-next > .fx-next-btn");
      btn.hidden = !hasNext;
      btn.classList.toggle("fx-pulse", hasNext && pulse);
      const wait = sec.querySelector(":scope > .fx-next > .fx-wait");
      wait.hidden = hasNext;
      const done = isRecap(sec);
      wait.classList.toggle("fx-done", done);
      wait.textContent = done ? "Lesson complete 🎉" : "Waiting for Claude's next step…";
    });
  }

  function setCurrent(i) {
    const s = secs();
    if (i < 0 || i >= s.length) return;
    const keys = keysOf(s);
    curIdx = i; curKey = keys[i];
    open.clear();
    for (const k of [...fresh]) if (keys.indexOf(k) <= i) fresh.delete(k);
    savePos();
    apply();
  }

  const scrollTop = (sec, smooth = true) => sec?.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" });

  // Items of one section, tagged as lesson speech.
  function lessonItems(sec) {
    const items = C.itemsFor(sec);
    items.forEach(i => { i.source = "lesson"; });
    return items;
  }

  // Start reading just this section. Drops what is queued (chat replies too unless core
  // offers cancelWhere), and doesn't wait for chat: pressing Next or Play never does.
  function readOnly(sec) {
    const inMain = (it) => !!it.el && mainEl().contains(it.el);
    if (C.cancelWhere) C.cancelWhere(inMain); else C.stop();
    if (C.hold) C.hold("await-chat", false);
    C.enqueue(lessonItems(sec));
  }

  function next() {
    const s = secs();
    if (curIdx >= s.length - 1) return;
    setCurrent(curIdx + 1);
    const sec = secs()[curIdx];
    if (C.settings.auto) { topPending = sec; readOnly(sec); }
    else { if (C.cancelWhere) C.cancelWhere(it => !!it.el && mainEl().contains(it.el)); else C.stop(); scrollTop(sec); }
  }

  function setOn(v) {
    on = v;
    C.save("classroom.focus", v);
    apply();
    if (on) scrollTop(secs()[curIdx], false);
  }

  C.on("render", () => {
    const keys = keysOf(secs());
    if (known !== null) keys.forEach((k, i) => { if (!known.includes(k) && i > curIdx) fresh.add(k); });
    known = keys;
    apply();
  });

  C.on("speechStart", (item) => {
    paused = false;
    const sec = item.el?.closest?.("main > section");
    if (!sec) return;
    const s = secs();
    const i = s.indexOf(sec);
    if (i === -1) return;
    const keys = keysOf(s);
    if (i > curIdx) {
      if (on && !fresh.has(keys[i])) {
        // Reading ran on past the current section (Play, a section's ▶): stop at the Next.
        const beyond = (it) => {
          const x = it.el?.closest?.("main > section");
          const j = x ? secs().indexOf(x) : -1;
          return j > curIdx && !fresh.has(keysOf(secs())[j]);
        };
        // core speaks this item right after this event, so stop a tick later (inaudible).
        queueMicrotask(() => { if (C.cancelWhere) C.cancelWhere(beyond); else C.stop(); });
        return;
      }
      setCurrent(i); // a section the reader started on its own (live arrival): follow it
      if (on) { scrollTop(sec); topPending = null; }
    } else if (i < curIdx && on && !open.has(keys[i])) {
      open.add(keys[i]); // read again on request: show it while it is read
      apply();
      item.el.scrollIntoView({ block: "center", behavior: "smooth" });
    } else if (topPending === sec) {
      topPending = null;
      scrollTop(sec); // one scroll, to the section top, after core's own scroll to the line
    }
  });

  C.on("ready", () => {
    mainEl().classList.toggle("fx-on", on); // hides sections from the very first render
    toggle = document.createElement("button");
    toggle.id = "btn-focus";
    toggle.textContent = "🎯 Focus";
    toggle.title = "Show one section at a time, with a Next button";
    toggle.onclick = () => setOn(!on);
    toggle.setAttribute("aria-pressed", String(on));
    C.addSetting ? C.addSetting(toggle) : C.addControl(toggle);

    // Header Play reads the current section only (Resume after Pause is left to core).
    document.getElementById("btn-pause")?.addEventListener("click", () => { paused = true; });
    document.getElementById("btn-stop")?.addEventListener("click", () => { paused = false; });
    document.getElementById("btn-play")?.addEventListener("click", (e) => {
      if (!on || !secs().length || (C.paused ?? paused)) return;
      e.stopImmediatePropagation();
      const sec = secs()[curIdx];
      readOnly(sec);
    }, true);

    // The chat's "New lesson section / Show lesson" banner (15) scrolls to the new
    // section, which focus mode hides until reached: go to the first new one.
    document.addEventListener("click", (e) => {
      if (!e.target.closest?.("#chat-banner button") || !on) return;
      const keys = keysOf(secs());
      const at = keys.findIndex((k, i) => i > curIdx && fresh.has(k));
      if (at !== -1) {
        setCurrent(at);
        setTimeout(() => scrollTop(secs()[curIdx], false), 0);
      }
    });
  });
})();
