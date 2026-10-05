// Code blocks formatted like VS Code (#46).
//
// Every fenced code block in a lesson or chat message becomes:
//   header strip: language name, a "Wrap" toggle, a "Copy" button
//   a line-number gutter (separate column, aria-hidden, not selectable, so it
//   is never copied or read) next to the code
// Code is syntax-coloured with highlight.js, loaded lazily from cdnjs on the
// first block: vs2015 theme in dark, vs in light, following the page theme
// (prefers-color-scheme, or :root[data-theme]). Long lines scroll sideways;
// Wrap turns wrapping on for one block (and hides the gutter, whose rows
// would no longer line up). mermaid blocks and the editable blocks of the
// python runner (feature 09) are left alone. Code is never read aloud: core
// skips <pre>, and nothing added here is a heading, paragraph or list item.
(() => {
  const C = window.Classroom;
  if (!C) return;

  const HLJS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1";
  const PLAIN = new Set(["text", "plaintext", "output", "console", "log", "txt"]);
  const SKIP = "pre.mermaid, .mermaid, .py-run, .cf-block";

  C.addStyle(`
    /* Baseline so a raw <pre> (before the render event wraps it) already looks like the framed block. */
    main pre:has(> code:not(.language-mermaid)), #chat-log pre:has(> code:not(.language-mermaid)) {
      margin: 1em 0; padding: 12px 12px 12px 56px; border: 2px solid var(--border, #ddd);
      border-radius: var(--radius, 12px); background: var(--code-bg, #f3f1ec); }
    /* Once framed, the gutter is a real column: drop the baseline's 56px gutter allowance too. */
    .cf-block pre { border: 0 !important; margin: 0 !important; border-radius: 0 !important; padding: 12px !important; }
    .cf-block { margin: 1em 0; border: 2px solid var(--border, #ddd); border-radius: var(--radius, 12px);
      overflow: hidden; background: var(--code-bg, #f3f1ec); }
    .cf-head { display: flex; align-items: center; gap: 8px; padding: 6px 10px;
      border-bottom: 1px solid var(--border, #ddd); font: 600 13px/1.2 system-ui, sans-serif; color: var(--muted, #666); }
    .cf-lang { flex: 1; text-transform: lowercase; letter-spacing: .03em; }
    .cf-head button { font: 600 13px/1 system-ui, sans-serif; padding: 6px 12px; border-radius: 999px; cursor: pointer;
      border: 1px solid var(--border, #ccc); background: var(--surface, transparent); color: var(--text, inherit); }
    .cf-head button:hover { border-color: var(--accent, #4a7); }
    .cf-head button[aria-pressed="true"] { background: var(--accent, #4a7); color: var(--accent-text, #fff); }
    .cf-body { display: flex; align-items: flex-start; }
    .cf-gutter { flex: none; padding: 12px 10px 12px 12px; text-align: right; user-select: none; -webkit-user-select: none;
      color: var(--muted, #888); opacity: .8; border-right: 1px solid var(--border, #ddd); }
    .cf-block pre { margin: 0; padding: 12px; flex: 1; min-width: 0; overflow-x: auto; border-radius: 0; background: none; }
    .cf-gutter, .cf-block pre, .cf-block pre code {
      font-family: "Cascadia Code", Consolas, "Courier New", monospace; font-size: 15.5px; line-height: 1.5; }
    .cf-block pre code, .cf-block pre code.hljs { padding: 0; background: none; white-space: pre; display: block; overflow: visible; }
    .cf-block.cf-wrapped pre code { white-space: pre-wrap; overflow-wrap: anywhere; }
    .cf-block.cf-wrapped .cf-gutter { display: none; }
    .cf-block:not(.cf-hl) pre code { color: var(--text, inherit); }
  `);

  // ---------- highlight.js, loaded once, on demand ----------
  // Theme links are created once. The script is never appended while one is pending, and after a
  // failure (offline) it is removed and not retried for RETRY_MS, so re-renders do not pile up tags.
  const RETRY_MS = 60000;
  let loading = null, retryAt = 0;
  function ensureLinks() {
    for (const [id, file] of [["cf-css-dark", "vs2015"], ["cf-css-light", "vs"]]) {
      if (document.getElementById(id)) continue;
      const l = document.createElement("link");
      l.rel = "stylesheet"; l.id = id; l.href = `${HLJS}/styles/${file}.min.css`;
      document.head.appendChild(l);
    }
    syncTheme();
  }
  function loadHljs() {
    if (window.hljs) return Promise.resolve(window.hljs);
    if (loading) return loading;
    if (Date.now() < retryAt) return Promise.resolve(null);
    ensureLinks();
    return (loading = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = `${HLJS}/highlight.min.js`;
      s.onload = () => { loading = null; resolve(window.hljs || null); };
      s.onerror = () => { s.remove(); loading = null; retryAt = Date.now() + RETRY_MS; resolve(null); }; // blocks stay plain
      document.head.appendChild(s);
    }));
  }

  function isDark() {
    const t = document.documentElement.dataset.theme;
    if (t === "dark") return true;
    if (t === "light") return false;
    return matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function syncTheme() {
    const d = isDark();
    const auto = !document.documentElement.dataset.theme;
    const dark = document.getElementById("cf-css-dark"), light = document.getElementById("cf-css-light");
    // media "not all" switches a sheet off. (link.disabled set before the sheet loads is lost.)
    // Following the OS, the active sheet gets the colour-scheme query rather than "all": the page's
    // --code-bg flips in the same style recalc as that query, while the change event that calls
    // this comes a tick later. So on a flip the old sheet drops out at once (code shows plain in
    // --text, readable) instead of e.g. black vs-theme text lingering on the dark --code-bg.
    if (dark) dark.media = d ? (auto ? "(prefers-color-scheme: dark)" : "all") : "not all";
    if (light) light.media = d ? "not all" : (auto ? "(prefers-color-scheme: light)" : "all");
  }
  new MutationObserver(syncTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", syncTheme);

  // ---------- one block ----------
  function copyText(text) {
    if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
    return new Promise((res, rej) => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? res() : rej(); } catch (e) { rej(e); } finally { ta.remove(); }
    });
  }

  function highlight(hljs, block) {
    const { code, text, tag } = block;
    if (PLAIN.has(tag)) return; // plain text and program output: framed and numbered, not coloured
    try {
      const r = tag && hljs.getLanguage(tag)
        ? hljs.highlight(text, { language: tag, ignoreIllegals: true })
        : hljs.highlightAuto(text);
      code.innerHTML = r.value;
      code.classList.add("hljs");
      block.wrap.classList.add("cf-hl");
      if (!tag && r.language) block.label.textContent = r.language;
    } catch (e) { console.error("[code-format]", e); }
  }

  // Must run after feature 09 (python runner), which swaps its `python run` <pre> for an editor
  // on the same render event: file order 09 < 20 guarantees that.
  function build(pre) {
    const code = pre.querySelector(":scope > code");
    if (!code || pre.closest(SKIP)) return null;
    const tag = ((/(?:^|\s)language-([^\s]+)/.exec(code.className) || [])[1] || "").toLowerCase();
    if (tag === "mermaid") return null;
    const text = code.textContent.replace(/\n$/, "");

    const wrap = document.createElement("div");
    wrap.className = "cf-block no-read";
    const head = document.createElement("div");
    head.className = "cf-head";
    const label = document.createElement("span");
    label.className = "cf-lang"; label.textContent = tag || "code";
    const wrapBtn = document.createElement("button");
    wrapBtn.type = "button"; wrapBtn.className = "cf-wrap"; wrapBtn.textContent = "Wrap";
    wrapBtn.setAttribute("aria-pressed", "false");
    wrapBtn.onclick = () => {
      const on = wrap.classList.toggle("cf-wrapped");
      wrapBtn.setAttribute("aria-pressed", String(on));
    };
    const copyBtn = document.createElement("button");
    copyBtn.type = "button"; copyBtn.className = "cf-copy"; copyBtn.textContent = "Copy";
    let timer = null;
    copyBtn.onclick = () => {
      copyText(text).then(() => {
        copyBtn.textContent = "Copied ✓";
        clearTimeout(timer);
        timer = setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
      }).catch(() => { copyBtn.textContent = "Copy failed"; });
    };
    head.append(label, wrapBtn, copyBtn);

    const gutter = document.createElement("div");
    gutter.className = "cf-gutter"; gutter.setAttribute("aria-hidden", "true");
    gutter.textContent = Array.from({ length: text.split("\n").length }, (_, i) => i + 1).join("\n");
    gutter.style.whiteSpace = "pre";

    const body = document.createElement("div");
    body.className = "cf-body";
    pre.replaceWith(wrap);
    body.append(gutter, pre);
    wrap.append(head, body);
    return { wrap, code, text, tag, label };
  }

  function decorate(root) {
    const blocks = [...root.querySelectorAll("pre")].map(build).filter(Boolean);
    if (!blocks.length) return;
    loadHljs().then((hljs) => { if (hljs) blocks.forEach((b) => highlight(hljs, b)); });
  }

  C.on("render", (main) => decorate(main));
  C.on("chatEntry", (entry, div) => { if (div) decorate(div); });
})();
