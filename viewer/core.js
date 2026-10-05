// Classroom viewer core: lesson rendering, chat panel, speech queue.
//
// Features live in viewer/features/NN-*.js, one file each, loaded after this
// one (see index.html). They extend the viewer only through window.Classroom:
//
//   Classroom.on(event, fn)        subscribe; events listed below
//   Classroom.enqueue(items)       add speech items to the end of the queue. Items with
//                                  source "chat" go ahead of queued lesson items instead
//                                  (after the paragraph being read); source "ui" goes first.
//   Classroom.hold(key, on)        while any hold is on, no new item starts (the one
//                                  being read finishes; "ui" items still play);
//                                  releasing the last one resumes. Play and stop() end
//                                  core's own wait for Claude's reply, never a feature's hold.
//   Classroom.playFrom(i) / stop()
//   Classroom.cancelWhere(pred)    drop queued items (and the one being read) where
//                                  pred(item) is true; the rest carries on in order
//   Classroom.itemsFor(el)         speech items for every readable element in el
//   Classroom.decorateItems(fn)    fn(item) may change every item itemsFor builds
//   Classroom.sentences(text) / speechText(el)
//   Classroom.setEngine(engine, {keep})  replace the speech engine (see defaultEngine).
//                                  Stops speech, unless keep is true: then the item being
//                                  read and the rest of the queue carry on with the new engine.
//   Classroom.peek()               the next queued speech item (for prefetching), or undefined
//   Classroom.speaking / paused    an item is being read / the engine is paused mid-item
//   Classroom.addControl(el)       append a control to the header bar
//   Classroom.addSetting(el)       append a secondary control to the Settings popover
//   Classroom.addStyle(css)        inject feature CSS
//   Classroom.settings / load(key, fallback) / save(key, value)
//   Classroom.slug                 current lesson slug, or null on the index page
//   Classroom.lessonUrl(file)      URL of a file inside the current lesson folder
//
// Events:
//   "ready"                        features registered, before the first fetch
//   "render"      (mainEl, md)     lesson re-rendered (after mermaid has run)
//   "chatEntry"   (entry, div, items)  a chat entry was rendered; div may be null.
//                                  items are its speech items (empty for the learner's own)
//   "diagram"     (div)            mermaid finished rendering a .mermaid div (lesson or chat)
//   "quizQuestion" (q, qd, ctx)    one quiz question was rendered into qd, before
//                                  it is queued for speech. A feature may restyle
//                                  qd, and set ctx.speak (a string, spoken as
//                                  one item) to replace how it is read aloud.
//   "speechStart" (item)           an item began speaking
//   "speechEnd"   (item)           an item finished
//   "idle"                         nothing is being read: the queue ran dry, or a hold
//                                  is keeping the rest waiting
//   "stopped"                      stop() cleared the queue
//   "index"       (listEl, names)  the lesson index page was drawn (no ?lesson=);
//                                  listEl is the <ul> (null if no lessons),
//                                  names the lesson slugs, newest first
//
// A speech item is { el, text } plus optional fields engines may honour:
//   voice: a voice name, or "A" / "B" for a feature-defined voice role
//   voiceName: a concrete voice name; wins over voice (lets a role pick a voice)
//   rate:  multiplier on top of the speed setting
//   pitch: speech pitch for the default engine (1 is normal)
//   source: "chat" or "lesson" for items core queues automatically; "ui" for a sound
//           the learner asked for right now (e.g. a voice test): it goes to the front
//           of the queue and plays even while a hold is on
(() => {
  const $ = (id) => document.getElementById(id);
  const synth = window.speechSynthesis;
  const params = new URLSearchParams(location.search);
  const slug = params.get("lesson");

  // ---------- events ----------
  const listeners = {};
  const on = (evt, fn) => (listeners[evt] ||= []).push(fn);
  const emit = (evt, ...args) => {
    for (const fn of listeners[evt] || []) {
      try { fn(...args); } catch (e) { console.error(`[${evt}]`, e); }
    }
  };

  // ---------- settings (per-viewer convenience only) ----------
  function load(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } }
  function save(key, val) { try { localStorage.setItem(key, val); } catch {} }

  const settings = {
    voice: load("classroom.voice", ""),
    rate: parseFloat(load("classroom.rate", "1")),
    auto: load("classroom.auto", "true") === "true",
    chat: load("classroom.chat", "true") === "true",
    // How long a new lesson section waits for Claude's chat reply before reading.
    chatWait: parseInt(load("classroom.chatWait", "30000"), 10) || 30000,
  };

  function bindControls() {
    $("rate").value = settings.rate; $("rate-val").textContent = settings.rate + "×";
    $("auto").checked = settings.auto;
    $("auto-chat").checked = settings.chat;
    $("rate").oninput = (e) => { settings.rate = parseFloat(e.target.value); $("rate-val").textContent = settings.rate + "×"; save("classroom.rate", settings.rate); };
    $("auto").onchange = (e) => { settings.auto = e.target.checked; save("classroom.auto", settings.auto); };
    $("auto-chat").onchange = (e) => { settings.chat = e.target.checked; save("classroom.chat", settings.chat); };
    $("voice").onchange = (e) => { settings.voice = e.target.value; save("classroom.voice", settings.voice); };
    $("btn-play").onclick = () => {
      if (engine.paused?.()) { engine.resume(); return; }
      if (!speaking && holds.size && queue.length) { release(); next(); return; } // skip the wait
      playFrom(sectionIndexOf(lastRead));
    };
    $("btn-pause").onclick = () => engine.pause();
    $("btn-stop").onclick = stop;
  }

  function fillVoices() {
    const voices = synth.getVoices().filter(v => v.lang.startsWith("en"));
    if (!voices.length) return;
    // Prefer natural/neural voices (Edge exposes "Online (Natural)" ones).
    voices.sort((a, b) => (/natural|neural/i.test(b.name) - /natural|neural/i.test(a.name)));
    const sel = $("voice");
    sel.innerHTML = "";
    for (const v of voices) {
      const o = document.createElement("option");
      o.value = v.name; o.textContent = v.name.replace(/^Microsoft /, "");
      sel.appendChild(o);
    }
    if (voices.some(v => v.name === settings.voice)) sel.value = settings.voice;
    else settings.voice = sel.value;
  }

  // ---------- speech engine ----------
  // An engine speaks one item and calls done() when it finishes (or fails).
  let current = null; // keep a reference so Chromium doesn't GC the utterance
  const defaultEngine = {
    name: "browser",
    speak(item, done) {
      const u = new SpeechSynthesisUtterance(item.text);
      const voiceName = item.voiceName || (item.voice && item.voice.length > 1 ? item.voice : settings.voice);
      const voice = synth.getVoices().find(v => v.name === voiceName);
      if (voice) u.voice = voice;
      u.rate = settings.rate * (item.rate || 1);
      if (item.pitch) u.pitch = item.pitch;
      // Every error ends the item too, "interrupted" and "canceled" included (another
      // tab's cancel() can cause them); core ignores callbacks from items it cancelled.
      let ended = false;
      u.onend = u.onerror = () => { if (!ended) { ended = true; done(); } };
      current = u;
      synth.speak(u);
    },
    cancel() { synth.cancel(); },
    pause() { if (synth.speaking) synth.pause(); },
    resume() { synth.resume(); },
    paused() { return synth.paused; },
  };
  let engine = defaultEngine;
  function setEngine(e, opts) {
    const rest = opts?.keep && (speaking || queue.length) ? [currentItem, ...queue].filter(Boolean) : [];
    halt(); // a section still waiting for chat keeps waiting
    engine = e || defaultEngine;
    if (rest.length) enqueue(rest);
  }

  // ---------- speech queue ----------
  let queue = [];
  let speaking = false;
  let pendingRender = false;
  let lastRead = null;
  let currentItem = null; // item being spoken now
  let token = 0; // invalidates done() callbacks from cancelled items

  function speechText(el) {
    let node = el;
    if (el.tagName === "LI") { node = el.cloneNode(true); node.querySelectorAll("ul,ol").forEach(n => n.remove()); }
    return node.textContent
      .replace(/→|->/g, " leads to ")
      .replace(/≠/g, " is not equal to ")
      .replace(/≈/g, " is about ")
      .replace(/[✓✔✗✘]/g, " ")
      .replace(/\s+/g, " ").trim();
  }

  function sentences(text) {
    // Split only where punctuation is followed by a space, so "example.com" and "3.14" stay whole.
    const parts = text.split(/(?<=[.!?]["')\]]*)\s+/);
    const out = [];
    for (let p of parts) {
      p = p.trim();
      // Chromium drops utterances that run past ~15 seconds; split long ones at pauses.
      while (p.length > 220) {
        let cut = Math.max(p.lastIndexOf(", ", 220), p.lastIndexOf("; ", 220), p.lastIndexOf(": ", 220));
        if (cut < 60) cut = p.lastIndexOf(" ", 220);
        out.push(p.slice(0, cut + 1)); p = p.slice(cut + 1).trim();
      }
      if (p) out.push(p);
    }
    return out;
  }

  function readableIn(container) {
    return [...container.querySelectorAll("h1,h2,h3,h4,p,li")].filter(el =>
      !el.closest("pre,.mermaid,table,details,.no-read") &&
      !(el.tagName === "LI" && el.querySelector(":scope > p")) &&
      speechText(el));
  }

  const decorators = [];
  function itemsFor(container) {
    const items = readableIn(container).flatMap(el => sentences(speechText(el)).map(text => ({ el, text })));
    for (const fn of decorators) items.forEach(it => { try { fn(it); } catch (e) { console.error("[decorateItems]", e); } });
    return items;
  }

  // Add to the end of what's being read, so nothing gets cut off mid-sentence.
  // Chat goes first: Claude's reply is read before any lesson still queued, once
  // the paragraph being read is finished. A "ui" item goes before everything.
  function enqueue(items) {
    const all = (src) => items.length && items.every(i => i.source === src);
    const reading = speaking && currentItem?.el;
    const at = all("ui") ? 0 : all("chat")
      ? queue.findIndex(i => i.source !== "chat" && i.source !== "ui" && (!reading || i.el !== reading)) : -1;
    if (at === -1) queue.push(...items); else queue.splice(at, 0, ...items);
    if (!speaking) next();
  }

  const holds = new Set();
  function hold(key, on) {
    if (on) { holds.add(key); return; }
    holds.delete(key);
    if (!speaking && !holds.size && queue.length) next();
  }

  // A new lesson section is usually written before Claude's chat reply arrives
  // (that comes at the end of the turn), so hold the section until the reply
  // has been queued, or until settings.chatWait passes without one. A turn
  // waits at most once: after a timeout, its later sections don't wait.
  let chatWaitTimer = null;
  let awaitingReply = false; // the learner spoke last in chat, so Claude's reply is still to come
  function awaitChat(on) {
    clearTimeout(chatWaitTimer);
    if (on) chatWaitTimer = setTimeout(() => { awaitingReply = false; hold("await-chat", false); }, settings.chatWait);
    hold("await-chat", on);
  }

  // End core's wait for a reply. A feature's hold (a chat tab talking) stays on.
  function release() { clearTimeout(chatWaitTimer); holds.delete("await-chat"); }

  function playFrom(sectionIndex) {
    stop(); // pressing play never waits for the reply
    const sections = [...document.querySelectorAll("main section")];
    enqueue(sections.slice(sectionIndex).flatMap(itemsFor));
  }

  function next() {
    document.querySelectorAll(".speaking").forEach(n => n.classList.remove("speaking"));
    if (holds.size && queue.length && queue[0].source !== "ui") {
      speaking = false; currentItem = null;
      emit("idle");
      return;
    }
    const item = queue.shift();
    if (!item) {
      speaking = false; current = null;
      emit("idle");
      if (pendingRender) refresh(true);
      return;
    }
    speaking = true; currentItem = item;
    if (item.el && $("main").contains(item.el)) lastRead = item.el;
    item.el?.classList.add("speaking");
    item.el?.scrollIntoView({ block: "center", behavior: "smooth" });
    emit("speechStart", item);
    const mine = ++token;
    engine.speak(item, () => {
      if (mine !== token) return;
      emit("speechEnd", item);
      next();
    });
  }

  // Drop the queued items that match pred, and the one being read if it matches;
  // everything else carries on in order.
  function cancelWhere(pred) {
    queue = queue.filter(i => !pred(i));
    if (speaking && currentItem && pred(currentItem)) {
      token++; engine.cancel();
      speaking = false; currentItem = null;
      next();
    }
  }

  function halt() {
    queue = []; speaking = false; token++;
    engine.cancel();
    document.querySelectorAll(".speaking").forEach(n => n.classList.remove("speaking"));
  }

  function stop() {
    halt();
    release(); // nothing left to wait for
    emit("stopped");
  }

  // The section that el was in, found again by its heading if the page has been
  // re-rendered since; 0 if there is none.
  function sectionIndexOf(el) {
    const sections = [...document.querySelectorAll("main section")];
    const sec = el?.closest("section");
    if (!sec) return 0;
    if (sections.includes(sec)) return sections.indexOf(sec);
    const h = sec.querySelector("h2")?.textContent;
    return Math.max(0, sections.findIndex(s => s.querySelector("h2")?.textContent === h));
  }

  // ---------- rendering ----------
  const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({ startOnLoad: false, theme: dark() ? "dark" : "default", securityLevel: "strict" });

  const lessonUrl = (file) => `../lessons/${encodeURIComponent(slug)}/${file}`;
  let lastSource = null;
  let knownHeadings = null;

  function mermaidBlocks(root) {
    root.querySelectorAll("pre > code.language-mermaid").forEach(code => {
      const div = document.createElement("div");
      div.className = "mermaid";
      div.textContent = code.textContent;
      code.parentElement.replaceWith(div);
    });
  }

  function sectionize(html) {
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    const out = document.createElement("div");
    let section = null;
    for (const node of [...tmp.childNodes]) {
      if (node.nodeName === "H1") { out.appendChild(node); section = null; continue; }
      if (!section && node.nodeType === Node.TEXT_NODE && !node.textContent.trim()) continue;
      if (node.nodeName === "H2" || !section) {
        section = document.createElement("section");
        out.appendChild(section);
        if (node.nodeName === "H2") {
          const head = document.createElement("div");
          head.className = "section-head";
          const btn = document.createElement("button");
          btn.textContent = "▶"; btn.title = "Read this section aloud";
          btn.className = "play-section";
          head.append(node, btn);
          section.appendChild(head);
          continue;
        }
      }
      section.appendChild(node);
    }
    return out;
  }

  async function render(md) {
    const root = sectionize(marked.parse(md, { gfm: true }));
    mermaidBlocks(root);
    const main = $("main");
    main.replaceChildren(...root.childNodes);
    const h1 = main.querySelector("h1");
    $("title").textContent = h1 ? h1.textContent : slug;
    document.title = h1 ? h1.textContent + " · Classroom" : "Classroom";
    main.querySelectorAll("section").forEach((s, i) => {
      const b = s.querySelector(".play-section");
      if (b) b.onclick = () => playFrom(i);
    });
    try { await mermaid.run({ nodes: main.querySelectorAll(".mermaid") }); } catch (e) { console.error(e); }
    main.querySelectorAll(".mermaid").forEach(d => emit("diagram", d));
    emit("render", main, md);
  }

  async function refresh(force = false) {
    let md;
    try {
      const res = await fetch(lessonUrl("lesson.md"), { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      md = await res.text();
    } catch {
      if (lastSource === null) $("main").innerHTML = `<p class="empty">Waiting for lessons/${slug}/lesson.md…</p>`;
      return;
    }
    if (md === lastSource && !force) return;
    // Busy while reading, or while a hold keeps items queued: re-rendering would
    // detach their elements. The idle after they are read renders it.
    if (speaking || (holds.size && queue.length)) { pendingRender = true; return; }
    pendingRender = false;
    lastSource = md;
    await render(md);

    const sections = [...document.querySelectorAll("main section")];
    const headings = sections.map(s => s.querySelector("h2")?.textContent ?? "");
    if (knownHeadings !== null && settings.auto) {
      const firstNew = headings.findIndex(h => !knownHeadings.includes(h));
      if (firstNew !== -1) {
        const items = sections.slice(firstNew).flatMap(itemsFor);
        items.forEach(i => { i.source = "lesson"; });
        if (awaitingReply) awaitChat(true);
        enqueue(items);
      }
    }
    knownHeadings = headings;
  }

  // ---------- chat panel (fed by .claude/hooks/chat_feed.py) ----------
  let chatCount = null; // lines already rendered; null until the first load

  function quizItems(div, q) {
    const opts = (q.options || []).map((o, i) => `Option ${i + 1}: ${o.label}.`).join(" ");
    return sentences(`Question. ${q.question} ${opts}`).map(text => ({ el: div, text }));
  }

  function renderChatEntry(entry) {
    const log = $("chat-log");
    const div = document.createElement("div");
    if (entry.kind === "claude") {
      div.className = "msg claude";
      div.innerHTML = marked.parse(entry.text, { gfm: true });
      mermaidBlocks(div);
      log.appendChild(div);
      mermaid.run({ nodes: div.querySelectorAll(".mermaid") })
        .then(() => div.querySelectorAll(".mermaid").forEach(d => emit("diagram", d)))
        .catch(console.error);
      const items = itemsFor(div);
      emit("chatEntry", entry, div, items);
      return items;
    }
    if (entry.kind === "you") {
      div.className = "msg you";
      div.textContent = entry.text;
      log.appendChild(div);
      emit("chatEntry", entry, div, []);
      return [];
    }
    if (entry.kind === "quiz") {
      div.className = "msg quiz";
      const items = [];
      for (const q of entry.questions || []) {
        const qd = document.createElement("div");
        qd.className = "quiz-q";
        qd.dataset.question = q.question;
        qd.innerHTML = `<span class="chip"></span><p></p><ol></ol>`;
        qd.querySelector(".chip").textContent = q.header || "Question";
        qd.querySelector("p").textContent = q.question;
        for (const o of q.options || []) {
          const li = document.createElement("li");
          li.dataset.label = o.label;
          li.textContent = o.label;
          if (o.description) { const s = document.createElement("small"); s.textContent = o.description; li.appendChild(s); }
          qd.querySelector("ol").appendChild(li);
        }
        div.appendChild(qd);
        const ctx = { speak: null };
        emit("quizQuestion", q, qd, ctx);
        items.push(...(ctx.speak == null ? quizItems(qd, q) : [{ el: qd, text: ctx.speak }]));
      }
      const hint = document.createElement("div");
      hint.className = "answer-here"; hint.textContent = "Answer in the terminal.";
      div.appendChild(hint);
      log.appendChild(div);
      emit("chatEntry", entry, div, items);
      return items;
    }
    if (entry.kind === "answer") {
      const quizzes = [...log.querySelectorAll(".msg.quiz")];
      const quiz = quizzes[quizzes.length - 1];
      if (quiz) {
        quiz.querySelector(".answer-here")?.remove();
        for (const [question, answer] of Object.entries(entry.answers || {})) {
          const qd = [...quiz.querySelectorAll(".quiz-q")].find(n => n.dataset.question === question);
          if (!qd) continue;
          const picked = String(answer).split(", ");
          let matched = false;
          qd.querySelectorAll("li").forEach(li => { if (picked.includes(li.dataset.label)) { li.classList.add("chosen"); matched = true; } });
          if (!matched) {
            const p = document.createElement("div");
            p.className = "other"; p.textContent = "Your answer: " + answer;
            qd.appendChild(p);
          }
        }
      }
      emit("chatEntry", entry, quiz || null, []);
      return [];
    }
    emit("chatEntry", entry, null, []); // unknown kinds are left to features
    return [];
  }

  async function refreshChat() {
    let lines;
    try {
      const res = await fetch(lessonUrl("chat.jsonl"), { cache: "no-store" });
      if (res.status === 404) { if (chatCount === null) chatCount = 0; return; } // no chat yet
      if (!res.ok) return;
      lines = (await res.text()).split("\n").filter(Boolean);
    } catch { return; }
    const firstLoad = chatCount === null;
    if (firstLoad || lines.length < chatCount) { $("chat-log").replaceChildren(); chatCount = 0; awaitingReply = false; }
    if (lines.length === chatCount) return;
    $("chat-log").querySelector(".hint")?.remove();
    const toRead = [];
    let replied = false;
    for (const line of lines.slice(chatCount)) {
      try {
        const entry = JSON.parse(line);
        // A turn starts with the learner's message (or quiz answer) and ends with
        // Claude's reply; a new lesson section mid-turn waits for that reply.
        if (entry.kind === "you" || entry.kind === "answer") awaitingReply = true;
        else if (entry.kind === "claude" || entry.kind === "quiz") { awaitingReply = false; replied = true; }
        toRead.push(...renderChatEntry(entry));
      } catch (e) { console.error(e); }
    }
    chatCount = lines.length;
    const log = $("chat-log");
    log.scrollTop = log.scrollHeight;
    toRead.forEach(i => { i.source = "chat"; });
    if (!firstLoad && settings.chat) enqueue(toRead);
    // After the reply is queued. A "you" after it in the same poll arms the next wait.
    if (replied && holds.has("await-chat")) awaitChat(false);
  }

  async function listLessons() {
    $("title").textContent = "Classroom";
    $("layout").classList.add("no-chat");
    document.querySelectorAll("header button, header label, #home").forEach(n => n.hidden = true);
    try {
      const html = await (await fetch("../lessons/", { cache: "no-store" })).text();
      const names = [...html.matchAll(/href="([^"?/]+)\/"/g)].map(m => decodeURIComponent(m[1])).sort().reverse();
      $("main").innerHTML = names.length
        ? "<h1>Lessons</h1><ul class='lesson-list'>" + names.map(n => `<li><a href="?lesson=${encodeURIComponent(n)}">${n}</a></li>`).join("") + "</ul>"
        : "<p class='empty'>No lessons yet. Ask Claude to teach you something.</p>";
      emit("index", $("main").querySelector("ul.lesson-list"), names);
    } catch {
      $("main").innerHTML = "<p class='empty'>Couldn't list lessons. Is the local server running?</p>";
    }
  }

  // ---------- public API ----------
  window.Classroom = {
    slug, settings, load, save, on, emit, lessonUrl,
    enqueue, hold, playFrom, stop, cancelWhere, itemsFor, decorateItems: (fn) => decorators.push(fn), sentences, speechText, setEngine, defaultEngine,
    addControl(el) { $("controls").appendChild(el); },
    addSetting(el) { $("settings-body").appendChild(el); },
    addStyle(css) { const s = document.createElement("style"); s.textContent = css; document.head.appendChild(s); },
    peek() { return queue[0]; },
    get speaking() { return speaking; },
    get paused() { return !!engine.paused?.(); },
  };

  // Deferred feature scripts run before DOMContentLoaded, so they have all
  // registered their listeners by the time this starts.
  document.addEventListener("DOMContentLoaded", () => {
    bindControls();
    fillVoices();
    synth.onvoiceschanged = fillVoices;
    emit("ready");
    if (slug) {
      refresh(); refreshChat();
      setInterval(refresh, 3000);
      setInterval(refreshChat, 1500);
    } else {
      listLessons();
    }
  });
})();
