// Chat reading: full-page chat, re-read buttons, readable formatting (#31).
//
// - "Full-page chat" header toggle (and the `c` key) hides the lesson and lets
//   the chat fill the page. Remembered in classroom.chatFull.
// - "Chat in new tab" opens this viewer URL with &view=chat: chat only.
// - Every Claude and quiz message gets a 🔊 button that reads it again, and
//   the chat toolbar has "Read whole chat". The buttons are .no-read.
// - New chat messages scroll into view in full / chat-only mode (unless the
//   learner has scrolled up). A view=chat tab never reads lesson sections, and
//   while one is open the main tab leaves chat reading to it (BroadcastChannel).
//   A new lesson section shows a "Show lesson" banner while in full mode.
// - Chat messages get roomier formatting; "Easy-read font" loads Atkinson
//   Hyperlegible for the lesson and chat (classroom.easyFont, off by default).
(() => {
  const C = window.Classroom;
  if (!C || !C.slug) return;

  const chatOnly = new URLSearchParams(location.search).get("view") === "chat";
  const itemsOf = new WeakMap(); // message div -> its speech items
  let full = chatOnly || C.load("classroom.chatFull", "false") === "true";
  let easy = C.load("classroom.easyFont", "false") === "true";
  let toggle, layout, banner;
  if (chatOnly) C.settings.auto = false; // this tab never reads lesson sections; not saved

  C.addStyle(`
    .msg { line-height: 1.7; }
    .msg.claude, .msg.quiz { color: var(--text); }
    .msg p { margin: 0 0 .9em; }
    .msg ul, .msg ol { margin: 0 0 .9em; padding-left: 1.4em; }
    .msg li { margin: .5em 0; }
    .msg h1, .msg h2, .msg h3, .msg h4 { margin: 1em 0 .4em; line-height: 1.3; font-weight: 700;
      border-bottom: 2px solid var(--border); padding-bottom: .15em; }
    .msg h1 { font-size: 1.35em; } .msg h2 { font-size: 1.25em; } .msg h3, .msg h4 { font-size: 1.1em; }
    .msg strong { font-weight: 700; }
    .msg.claude a, .msg.quiz a { color: var(--accent); }
    .quiz-q li { margin: .5em 0; }
    .reread { display: block; margin-top: 8px; font-size: 14px; padding: 3px 10px; }
    #chat-tools { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 14px; border-bottom: 1px solid var(--border); }
    #chat-tools button { font-size: 14px; padding: 3px 10px; }
    a.btn-link { font: inherit; font-size: 15px; border: 1px solid var(--border); background: var(--surface);
      color: var(--text); border-radius: 8px; padding: 5px 12px; text-decoration: none; }

    .layout.chat-full { grid-template-columns: minmax(0, 1fr); max-width: none; }
    .layout.chat-full main { display: none; }
    .layout.chat-full aside { position: static; height: auto; max-height: none; margin-bottom: 40px; }
    .layout.chat-full #chat-log { overflow: visible; flex: none; font-size: 20px; line-height: 1.55; padding: 12px 16px 24px; }
    .layout.chat-full #chat-log .hint { text-align: center; font-size: 17px; }
    /* Full page: wide and short rather than narrow and tall. */
    .layout.chat-full .msg, .layout.chat-full .msg.you { max-width: 1200px; margin: 10px auto; }
    .layout.chat-full .msg { padding: 10px 20px; line-height: 1.55; }
    .layout.chat-full .msg p { margin: 0 0 .5em; }
    .layout.chat-full .msg ul, .layout.chat-full .msg ol { margin: 0 0 .5em; }
    .layout.chat-full .msg li { margin: .2em 0; }
    .layout.chat-full .quiz-q ol { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 4px 28px; list-style-position: inside; padding-left: 0; }
    .layout.chat-full .quiz-q li { margin: 0; }
    .layout.chat-full .quiz-q li small { padding-left: 1.3em; }
    .layout.chat-full #chat-tools { justify-content: center; position: sticky; top: var(--header-h, 60px);
      z-index: 5; background: var(--surface); border-radius: 12px 12px 0 0; }
    #chat-banner { flex-basis: 100%; display: flex; gap: 10px; align-items: center; justify-content: center;
      background: var(--speaking); border-radius: 8px; padding: 6px 12px; font-size: 16px; }
    #chat-banner[hidden] { display: none; }

    html.easy-font body, html.easy-font button, html.easy-font select, html.easy-font input,
    html.easy-font textarea { font-family: "Atkinson Hyperlegible", system-ui, -apple-system, "Segoe UI", sans-serif; }
  `);

  function applyFull() {
    layout.classList.toggle("chat-full", full);
    if (toggle) toggle.textContent = full ? "Side chat" : "Full-page chat";
    if (!full && banner) banner.hidden = true;
  }

  function setFull(v) {
    full = v;
    C.save("classroom.chatFull", v);
    applyFull();
    // Going full-page lands on the newest message, not the top of the chat.
    if (v) [...document.querySelectorAll("#chat-log .msg")].pop()?.scrollIntoView({ block: "end" });
  }

  function applyFont() {
    document.documentElement.classList.toggle("easy-font", easy);
    if (easy && !document.getElementById("easy-font-css")) {
      const l = document.createElement("link");
      l.id = "easy-font-css"; l.rel = "stylesheet";
      l.href = "https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400;1,700&display=swap";
      document.head.appendChild(l);
    }
  }

  const readable = (div) => div.classList.contains("claude") || div.classList.contains("quiz");

  // A learner's request: "ui" copies play next, even while a new lesson section
  // waits for Claude's reply, and whatever was queued (that section) stays queued.
  function readAgain(items) { C.enqueue(items.map(i => ({ ...i, source: "ui" }))); }

  function readAll() {
    const items = [...document.querySelectorAll("#chat-log .msg")]
      .filter(readable).flatMap(d => itemsOf.get(d) || []);
    readAgain(items);
  }

  // Keep the newest message in view in full / chat-only mode, unless the
  // learner has scrolled up to reread. Measured when the entry arrives: the page
  // gap below the viewport, less the entry's own height, is how far from the
  // bottom the learner was before it was added.
  const wasNearBottom = (div) =>
    document.documentElement.scrollHeight - (scrollY + innerHeight) - div.offsetHeight - 20 <= 150;

  C.on("chatEntry", (entry, div, items) => {
    if (!div) return;
    const near = wasNearBottom(div); // before the re-read button makes it taller
    if (entry.kind === "claude" || entry.kind === "quiz") {
      itemsOf.set(div, items);
      if (!div.querySelector(":scope > .reread")) {
        const b = document.createElement("button");
        b.className = "reread no-read";
        b.textContent = "\u{1F50A} Read again";
        b.title = "Read this message aloud again";
        b.setAttribute("aria-label", "Read this message aloud again");
        b.onclick = () => readAgain(itemsOf.get(div) || []);
        div.appendChild(b);
      }
    }
    if (entry.kind !== "answer" && (full || chatOnly) && near) {
      div.scrollIntoView({ block: div.offsetHeight > innerHeight ? "start" : "end" });
    }
  });

  // Two tabs reading the chat aloud would talk over each other. While a
  // view=chat tab for this lesson is open, the main tab leaves chat to it.
  const bc = typeof BroadcastChannel === "function" ? new BroadcastChannel("classroom") : null;
  let chatTabSeen = 0;
  const chatTabAlive = () => Date.now() - chatTabSeen < 5000;
  function syncChatReading() {
    if (chatOnly) return;
    const box = document.getElementById("auto-chat");
    C.settings.chat = chatTabAlive() ? false : !!(box && box.checked);
  }
  // The chat tab's heartbeat also says whether it is talking right now (paused
  // counts as not talking), and the main tab holds its lesson reading while it
  // is, so the two voices never overlap. The tabs poll on their own clocks, so a
  // new Claude message also holds the main tab for 4 seconds, giving the chat
  // tab time to start reading it. A chat tab stuck on one item for a minute
  // (a wedged speech engine) stops holding the main tab.
  const STUCK_MS = 60000;
  let chatTabSpeaking = false;
  let chatTabItem = null, chatTabItemAt = 0;
  let expectChatUntil = 0;
  function syncHold() {
    const talking = chatTabSpeaking && Date.now() - chatTabItemAt < STUCK_MS;
    C.hold("chat-tab", chatTabAlive() && (talking || Date.now() < expectChatUntil));
  }
  if (bc) {
    if (chatOnly) {
      let started = 0;
      const beat = () => bc.postMessage({ type: "chat-tab", speaking: C.speaking && !C.paused, item: started, lesson: C.slug });
      // Deferred, so a stop followed at once by new speech (Read again) sends one beat.
      const soon = () => setTimeout(beat, 0);
      beat(); setInterval(beat, 2000);
      C.on("speechStart", () => { started++; soon(); });
      C.on("idle", soon);
      C.on("stopped", soon);
      C.on("ready", () => ["btn-pause", "btn-play"].forEach(id => document.getElementById(id)?.addEventListener("click", soon)));
      addEventListener("pagehide", () => bc.postMessage({ type: "chat-tab-closed", lesson: C.slug }));
    } else {
      bc.onmessage = (e) => {
        const m = e.data || {};
        if (m.lesson !== C.slug) return;
        if (m.type === "chat-tab") {
          chatTabSeen = Date.now(); chatTabSpeaking = !!m.speaking;
          if (m.item !== chatTabItem) { chatTabItem = m.item; chatTabItemAt = Date.now(); }
        }
        else if (m.type === "chat-tab-closed") { chatTabSeen = 0; chatTabSpeaking = false; }
        syncChatReading(); syncHold();
      };
      setInterval(syncChatReading, 1000);
      setInterval(syncHold, 250);
      C.on("chatEntry", (entry) => {
        if (chatTabAlive() && (entry.kind === "claude" || entry.kind === "quiz")) {
          expectChatUntil = Date.now() + 4000; syncHold();
        }
      });
    }
  }

  // A new lesson section while the chat is full-page: say so, without speaking.
  let knownH2 = null;
  C.on("render", (main) => {
    const heads = [...main.querySelectorAll("section")].map(sec => sec.querySelector("h2")?.textContent ?? "");
    const fresh = knownH2 === null ? -1 : heads.findIndex(h => !knownH2.includes(h));
    knownH2 = heads;
    if (fresh === -1 || !full || chatOnly || !banner) return;
    banner.hidden = false;
    banner.querySelector("button").onclick = () => {
      setFull(false);
      main.querySelectorAll("section")[fresh]?.scrollIntoView({ block: "start" });
    };
  });

  C.on("ready", () => {
    layout = document.getElementById("layout");

    if (!chatOnly) {
      toggle = document.createElement("button");
      toggle.id = "btn-chat-full";
      toggle.title = "Make the chat fill the page (shortcut: c)";
      toggle.onclick = () => setFull(!full);
      C.addControl(toggle);

      const link = document.createElement("a");
      link.id = "link-chat-tab"; link.className = "btn-link";
      link.textContent = "Chat in new tab"; link.target = "_blank"; link.rel = "noopener";
      const u = new URL(location.href); u.searchParams.set("view", "chat");
      link.href = u.href;
      C.addControl(link);

      document.addEventListener("keydown", (e) => {
        if (e.key !== "c" || e.ctrlKey || e.metaKey || e.altKey) return;
        const t = e.target;
        if (t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName))) return;
        setFull(!full);
      });
    } else {
      document.getElementById("btn-play")?.setAttribute("hidden", "");
      document.getElementById("auto")?.closest("label")?.setAttribute("hidden", "");
    }

    const font = document.createElement("label");
    font.title = "Use the Atkinson Hyperlegible font, designed to be easy to read";
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.id = "easy-font"; cb.checked = easy;
    cb.onchange = () => { easy = cb.checked; C.save("classroom.easyFont", easy); applyFont(); };
    font.append(cb, " Easy-read font");
    C.addSetting(font);

    const tools = document.createElement("div");
    tools.id = "chat-tools";
    const all = document.createElement("button");
    all.id = "btn-read-chat"; all.className = "no-read";
    all.textContent = "🔊 Read whole chat";
    all.title = "Read every message in the chat aloud, in order";
    all.onclick = readAll;
    tools.appendChild(all);
    banner = document.createElement("div");
    banner.id = "chat-banner"; banner.className = "no-read"; banner.hidden = true;
    const bt = document.createElement("span"); bt.textContent = "New lesson section.";
    const bb = document.createElement("button"); bb.textContent = "Show lesson";
    banner.append(bt, bb);
    tools.prepend(banner);
    document.querySelector("aside h3").after(tools);
    const header = document.querySelector("header");
    const setH = () => document.documentElement.style.setProperty("--header-h", header.offsetHeight + "px");
    setH();
    if (window.ResizeObserver) new ResizeObserver(setH).observe(header);

    applyFull();
    applyFont();
  });
})();
