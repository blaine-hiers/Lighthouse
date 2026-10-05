// Feature: run Python in the lesson page.
//
//   ```python run                              an editor seeded with the block's code
//   ```python run file=practice/03-faded.py    an editor loaded from that lesson file,
//                                              with a Save button that writes it back
//
// marked keeps only the first word of a fence's info string ("python"), so the
// "run file=..." part is recovered from the markdown itself with marked.lexer,
// in document order, and matched to the rendered python blocks in the same order.
//
// Pyodide loads from the jsDelivr CDN on the first Run. It is pinned and never committed.
// Saving goes through viewer/serve.py (PUT under lessons/<slug>/practice/ only).
(() => {
  const C = window.Classroom;
  const PYODIDE_VERSION = "0.29.3";
  const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

  C.addStyle(`
    .py-run { margin: 1em 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); overflow: hidden; }
    .py-run textarea { display: block; width: 100%; min-height: 8em; resize: vertical; border: 0; padding: 10px 12px;
      font: 15px/1.5 ui-monospace, Consolas, monospace; background: var(--code-bg); color: var(--text); tab-size: 4; }
    .py-run .py-bar { display: flex; gap: 8px; align-items: center; padding: 6px 10px; border-top: 1px solid var(--border); }
    .py-run .py-status { color: var(--muted); font-size: 14px; margin-left: auto; }
    .py-run pre.py-out { margin: 0; padding: 8px 12px; min-height: 2.2em; border-top: 1px solid var(--border);
      white-space: pre-wrap; font: 14px/1.5 ui-monospace, Consolas, monospace; }
    .py-run pre.py-out .py-err { color: #c0392b; }
  `);

  // Fenced python blocks whose info string starts "python run", in document order.
  function runFences(md) {
    const found = [];
    marked.walkTokens(marked.lexer(md, { gfm: true }), (t) => {
      if (t.type !== "code" || !t.lang) return;
      const words = t.lang.trim().split(/\s+/);
      if (words[0] !== "python") return;
      const info = { run: words[1] === "run", file: null, code: t.text };
      for (const w of words.slice(2)) if (w.startsWith("file=")) info.file = w.slice(5);
      found.push(info);
    });
    return found;
  }

  // ---------- Pyodide (lazy, once) ----------
  let pyodidePromise = null;
  function loadPyodideOnce(onStatus) {
    pyodidePromise ||= (async () => {
      onStatus("loading Python…");
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = PYODIDE_BASE + "pyodide.js";
        s.onload = resolve;
        s.onerror = () => reject(new Error("could not load Pyodide from the CDN"));
        document.head.appendChild(s);
      });
      return await window.loadPyodide({ indexURL: PYODIDE_BASE });
    })().catch((e) => { pyodidePromise = null; throw e; });
    return pyodidePromise;
  }

  // Edits survive a re-render (the lesson page re-renders when lesson.md changes).
  const drafts = new Map();

  function build(pre, info, index) {
    const key = `${index}:${info.file || ""}`;
    const box = document.createElement("div");
    box.className = "py-run no-read";
    const ta = document.createElement("textarea");
    ta.spellcheck = false;
    ta.setAttribute("aria-label", "Python code");
    const bar = document.createElement("div");
    bar.className = "py-bar";
    const run = document.createElement("button");
    run.textContent = "Run";
    run.className = "py-btn-run";
    const save = document.createElement("button");
    save.textContent = "Save";
    save.className = "py-btn-save";
    const status = document.createElement("span");
    status.className = "py-status";
    const out = document.createElement("pre");
    out.className = "py-out";
    out.setAttribute("aria-live", "polite");
    bar.append(run);
    if (info.file) bar.append(save);
    bar.append(status);
    box.append(ta, bar, out);
    pre.replaceWith(box);

    const say = (msg) => { status.textContent = msg; };
    ta.value = drafts.has(key) ? drafts.get(key) : info.code;
    ta.oninput = () => drafts.set(key, ta.value);
    // Tab inserts spaces instead of leaving the box.
    ta.onkeydown = (e) => {
      if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        ta.setRangeText("    ", ta.selectionStart, ta.selectionEnd, "end");
        ta.oninput();
      }
    };

    if (info.file && !drafts.has(key)) {
      fetch(C.lessonUrl(info.file), { cache: "no-store" })
        .then((r) => (r.ok ? r.text() : Promise.reject()))
        .then((t) => { if (!drafts.has(key)) ta.value = t; })
        .catch(() => say("new file, not saved yet"));
    }

    const append = (text, cls) => {
      const span = document.createElement("span");
      if (cls) span.className = cls;
      span.textContent = text + "\n";
      out.appendChild(span);
    };

    run.onclick = async () => {
      run.disabled = true;
      out.textContent = "";
      try {
        const py = await loadPyodideOnce(say);
        say("running…");
        py.setStdout({ batched: (s) => append(s) });
        py.setStderr({ batched: (s) => append(s, "py-err") });
        const globals = py.globals.get("dict")();
        try { await py.runPythonAsync(ta.value, { globals }); }
        finally { globals.destroy(); }
        say("done");
      } catch (e) {
        append(String(e.message || e).trim(), "py-err");
        say("error");
      } finally {
        run.disabled = false;
      }
    };

    save.onclick = async () => {
      say("saving…");
      try {
        const res = await fetch(C.lessonUrl(info.file), {
          method: "PUT", headers: { "Content-Type": "text/plain; charset=utf-8" }, body: ta.value,
        });
        say(res.ok ? "saved" : `could not save (${res.status}); is viewer/serve.py running?`);
      } catch {
        say("could not save; is viewer/serve.py running?");
      }
    };
  }

  C.on("render", (main, md) => {
    const fences = runFences(md);
    const pres = [...main.querySelectorAll("pre > code.language-python")];
    // If the two lists disagree, don't guess which block is which.
    if (fences.length !== pres.length) return;
    pres.forEach((code, i) => { if (fences[i].run) build(code.parentElement, fences[i], i); });
  });
})();
