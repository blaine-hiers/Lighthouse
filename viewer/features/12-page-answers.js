// Feature: answer quizzes in the page, through the Classroom channel
// (channel/README.md). Off unless the page was opened from the link the
// channel's viewer_url tool returns. That link points at the channel's own
// server (http://127.0.0.1:8766/viewer/...) and ends in #ch=<key>. Without it,
// or while the channel can't be reached, nothing changes and quizzes are
// answered in the terminal as before.
(() => {
  const C = window.Classroom;
  if (!C.slug) return;

  // Keep the original fetch: this file runs before any lesson or chat content
  // is rendered, so nothing injected later can wrap it to read the key.
  const fetch = window.fetch.bind(window);

  // The key lives only in this closure: not in storage, not in the URL.
  // Reloading the page drops it; ask Claude for the link again.
  const token = (/[#&]ch=([A-Za-z0-9_-]{20,200})/.exec(location.hash) || [])[1] || null;
  if (location.hash.includes("ch=")) history.replaceState(null, "", location.pathname + location.search);
  if (!token) return;

  let live = false;      // the channel answered /status with our key
  let pending = null;    // the one quiz it will accept an answer for
  let received = null;   // the last answer Claude confirmed with got_answer

  C.addStyle(`
    .msg.quiz.answering .answer-here { display: none; }
    .page-answer { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
    .page-answer button { font-size: 14px; padding: 4px 10px; }
    .page-answer form { display: flex; gap: 6px; flex: 1 1 100%; }
    .page-answer input { flex: 1; min-width: 0; font: inherit; font-size: 14px; padding: 4px 8px;
      border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); }
    .page-answer .note { flex: 1 1 100%; color: var(--muted); font-size: 13px; }
  `);

  async function status() {
    try {
      const res = await fetch("/status", { headers: { "X-Classroom-Token": token }, cache: "no-store" });
      const body = res.ok ? await res.json() : null;
      live = !!body?.ok;
      pending = live ? body.pending : null;
      received = live ? body.received : null;
    } catch {
      live = false; pending = null;
    }
    sync();
  }

  async function send(div, answer, type) {
    const box = div.querySelector(".page-answer");
    box.querySelectorAll("button, input").forEach(n => n.disabled = true);
    let ok = false;
    try {
      const res = await fetch("/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Classroom-Token": token },
        body: JSON.stringify({ quiz_id: div.dataset.quizId, answer, answer_type: type }),
      });
      ok = res.ok;
    } catch {}
    div.dataset.sent = ok ? "yes" : "failed";
    if (ok) pending = null;
    sync();
  }

  function controls(div) {
    const box = document.createElement("div");
    box.className = "page-answer no-read";
    for (const li of div.querySelectorAll(".quiz-q li")) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = li.dataset.label;
      b.onclick = () => send(div, li.dataset.label, "option");
      box.appendChild(b);
    }
    const form = document.createElement("form");
    const input = document.createElement("input");
    input.maxLength = 500;
    input.placeholder = "Or type your own answer";
    const go = document.createElement("button");
    go.textContent = "Send";
    form.append(input, go);
    form.onsubmit = (e) => { e.preventDefault(); if (input.value.trim()) send(div, input.value.trim(), "text"); };
    const note = document.createElement("div");
    note.className = "note";
    box.append(form, note);
    return box;
  }

  // Controls appear only on the quiz the channel is waiting for, and only
  // while it's reachable. Everything else keeps "Answer in the terminal."
  // Once sent, the note says honestly whether Claude has confirmed it.
  function sync() {
    for (const div of document.querySelectorAll(".msg.quiz[data-quiz-id]")) {
      const box = div.querySelector(".page-answer");
      if (div.dataset.sent) {
        const note = box?.querySelector(".note");
        if (!note) continue;
        note.textContent = div.dataset.sent === "failed" ? "Couldn't send that. Answer in the terminal instead."
          : received === div.dataset.quizId ? "Claude got your answer."
          : "Sent to Claude. If Claude doesn't respond, type your answer in the terminal.";
        continue;
      }
      const want = live && div.dataset.quizId === pending;
      if (want && !box) { div.appendChild(controls(div)); div.classList.add("answering"); }
      if (!want && box) { box.remove(); div.classList.remove("answering"); }
    }
  }

  C.on("chatEntry", (entry, div) => {
    if (entry.kind === "quiz" && entry.via === "channel" && div) { div.dataset.quizId = entry.quiz_id; status(); }
    if (entry.kind === "answer") status();
  });
  C.on("ready", () => { status(); setInterval(status, 2000); });
})();
