// Feature slot: progress map.
//
// A "Progress" button in the header toggles a panel with a mermaid graph of
// the current lesson's concepts, built from progress/concepts.json. An arrow
// runs from each prerequisite to the concept that requires it. Each node is
// one of four states:
//   mastered  mastery >= 0.8
//   locked    not mastered, and some prerequisite is not mastered
//   learning  has attempts, mastery < 0.8
//   new       no attempts yet
// `mastery` is written by progress/mastery.py; this file only reads it. The
// file is polled, so the map follows the lesson as checks are recorded.
(() => {
  const C = window.Classroom;
  if (!C || !C.slug) return;

  const MASTERED = 0.8;
  const FILE = "../progress/concepts.json";
  const POLL_MS = 2000;
  let concepts = {};
  let lastText = null;
  let open = C.load("classroom.progressMap", "false") === "true";
  let dirty = true;
  let renderId = 0;
  let panel, body, button;

  C.addStyle(`
    #progress-panel { background: var(--surface); border-bottom: 1px solid var(--border); padding: 10px 16px 14px; }
    #progress-panel[hidden] { display: none; }
    #progress-panel .map { overflow-x: auto; text-align: center; min-height: 40px; }
    #progress-panel .map svg { max-width: 100%; height: auto; }
    #progress-panel .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; justify-content: center;
      font-size: 14px; color: var(--muted); margin-top: 6px; }
    #progress-panel .legend span::before { content: ""; display: inline-block; width: 12px; height: 12px;
      border-radius: 3px; margin-right: 6px; vertical-align: -1px; border: 1.5px solid #5b6470; }
    #progress-panel .legend .new::before { background: #e3ded5; }
    #progress-panel .legend .learning::before { background: #ffd96b; }
    #progress-panel .legend .mastered::before { background: #7ed99a; }
    #progress-panel .legend .locked::before { background: #f3f1ec; border-style: dashed; }
    #progress-panel .empty { color: var(--muted); font-size: 15px; margin: 8px 0; }
  `);

  function mastered(c) { return !!c && (c.mastery || 0) >= MASTERED; }

  // The state of every concept in the current lesson, by concept id.
  function states(all, lesson) {
    const out = {};
    for (const [id, c] of Object.entries(all)) {
      if (c.lesson !== lesson) continue;
      let state;
      if (mastered(c)) state = "mastered";
      else if ((c.requires || []).some(r => !mastered(all[r]))) state = "locked";
      else state = (c.attempts || []).length ? "learning" : "new";
      out[id] = state;
    }
    return out;
  }

  // Mermaid treats quotes, backticks and angle brackets in labels as syntax.
  const clean = (s) => String(s).replace(/"/g, "#quot;").replace(/`/g, "#96;").replace(/[<>]/g, "");

  function source(all, st) {
    const ids = Object.keys(st);
    const index = new Map(ids.map((id, i) => [id, `n${i}`]));
    const lines = ["graph LR"];
    for (const id of ids) {
      const c = all[id];
      const mark = { mastered: " ✓", locked: " 🔒", learning: ` (${Math.round((c.mastery || 0) * 100)}%)`, new: "" }[st[id]];
      lines.push(`  ${index.get(id)}["${clean(c.title || id)}${mark}"]:::${st[id]}`);
    }
    for (const id of ids) {
      for (const r of all[id].requires || []) {
        if (index.has(r)) lines.push(`  ${index.get(r)} --> ${index.get(id)}`);
      }
    }
    lines.push(
      "  classDef new fill:#e3ded5,stroke:#5b6470,color:#1f2328",
      "  classDef learning fill:#ffd96b,stroke:#8a6d00,color:#1f2328",
      "  classDef mastered fill:#7ed99a,stroke:#1f7a3d,color:#1f2328",
      "  classDef locked fill:#f3f1ec,stroke:#5b6470,stroke-dasharray:5 4,color:#656d76",
    );
    return lines.join("\n");
  }

  async function draw() {
    if (!open) return;
    dirty = false;
    const st = states(concepts, C.slug);
    if (!Object.keys(st).length) {
      body.innerHTML = `<p class="empty">No concepts for this lesson yet. They appear here once Claude adds them to the progress map.</p>`;
      return;
    }
    const mine = ++renderId;
    try {
      const { svg } = await mermaid.render(`progress-map-${mine}`, source(concepts, st));
      if (mine !== renderId) return; // a newer draw superseded this one
      body.innerHTML = `<div class="map">${svg}</div>
        <div class="legend"><span class="new">New</span><span class="learning">Learning</span><span class="mastered">Mastered</span><span class="locked">Locked until its prerequisites are mastered</span></div>`;
    } catch (e) {
      console.error(e);
    }
  }

  async function poll() {
    let text;
    try {
      const res = await fetch(FILE, { cache: "no-store" });
      if (res.status === 404) text = "";
      else if (!res.ok) return;
      else text = await res.text();
    } catch { return; }
    if (text === lastText) return;
    lastText = text;
    try { concepts = text ? (JSON.parse(text).concepts || {}) : {}; } catch { return; }
    dirty = true;
    draw();
  }

  function setOpen(v) {
    open = v;
    C.save("classroom.progressMap", v);
    panel.hidden = !v;
    button.setAttribute("aria-pressed", String(v));
    if (v && dirty) draw();
  }

  C.on("ready", () => {
    button = document.createElement("button");
    button.id = "btn-progress";
    button.textContent = "Progress";
    button.title = "Show which ideas you've mastered";
    button.onclick = () => setOpen(!open);
    C.addControl(button);
    panel = document.createElement("div");
    panel.id = "progress-panel";
    panel.setAttribute("aria-label", "Progress map");
    body = document.createElement("div");
    panel.appendChild(body);
    document.querySelector("header").after(panel);
    panel.hidden = !open;
    button.setAttribute("aria-pressed", String(open));
    poll();
    setInterval(poll, POLL_MS);
  });
})();
