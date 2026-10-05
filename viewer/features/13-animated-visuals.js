// Feature: diagrams that follow the narration, flowing edges, and explorables.
//
//  1. While a sentence is spoken, flowchart nodes whose label appears in it
//     (whole words, case-insensitive, longest label first) get .narrated.
//  2. A mermaid block containing the line "%% animate" gets .animated, which
//     runs a dash animation along its edges (off under reduced motion).
//  3. An explorable fenced block holds JSON like
//       {"vars": {"n": {"min": 1, "max": 10, "value": 3}}, "show": "n squared is {n*n}"}
//     and becomes range inputs plus live text. Its {...} expressions go through
//     a small tokenizer and parser below. There is no eval and no Function.
//
// Mermaid 11.4.1 flowchart SVG: nodes are `g.node` (label = its textContent),
// edges are `path.flowchart-link`. Sequence diagrams draw edges as
// `line.messageLine0` / `line.messageLine1`.
(() => {
  const C = window.Classroom;
  if (!C) return;

  // ---------- safe arithmetic evaluator ----------
  // expr    := sum
  // sum     := product (("+" | "-") product)*
  // product := unary (("*" | "/" | "%") unary)*
  // unary   := "-" unary | "+" unary | power
  // power   := atom ("**" unary)?            (right-associative; -2**2 is -4)
  // atom    := number | variable | call | "(" expr ")"
  // call    := ("Math.round" | "min" | "max" | "sqrt") "(" [expr ("," expr)*] ")"
  // Only these tokens exist; anything else is a syntax error. Variables and
  // functions are looked up in Maps, so names like "constructor" never resolve.
  const FUNCS = new Map([
    ["Math.round", { min: 1, max: 1, fn: Math.round }],
    ["min", { min: 1, max: Infinity, fn: Math.min }],
    ["max", { min: 1, max: Infinity, fn: Math.max }],
    ["sqrt", { min: 1, max: 1, fn: Math.sqrt }],
  ]);
  const MAX_LEN = 200, MAX_DEPTH = 40;

  function tokenize(src) {
    const toks = [];
    const re = /\s*(?:(\d+(?:\.\d+)?|\.\d+)|(Math\.round(?![\w.]))|([A-Za-z_]\w*)|(\*\*|[-+*\/%(),]))/y;
    let pos = 0;
    while (!/^\s*$/.test(src.slice(pos))) {
      re.lastIndex = pos;
      const m = re.exec(src);
      if (!m) throw new Error("unexpected character at " + pos);
      pos = re.lastIndex;
      if (m[1] !== undefined) toks.push({ t: "num", v: parseFloat(m[1]) });
      else if (m[2] !== undefined) toks.push({ t: "id", v: m[2] });
      else if (m[3] !== undefined) toks.push({ t: "id", v: m[3] });
      else toks.push({ t: "op", v: m[4] });
    }
    return toks;
  }

  function evaluate(src, vars) {
    if (typeof src !== "string" || src.length > MAX_LEN) throw new Error("bad expression");
    const scope = new Map(Object.entries(vars || {}).filter(([, v]) => typeof v === "number"));
    const toks = tokenize(src);
    let i = 0, depth = 0;
    const isOp = (v) => toks[i] && toks[i].t === "op" && toks[i].v === v;
    const expect = (v) => { if (!isOp(v)) throw new Error("expected " + v); i++; };
    const enter = () => { if (++depth > MAX_DEPTH) throw new Error("too deeply nested"); };

    function sum() {
      let v = product();
      while (isOp("+") || isOp("-")) { const op = toks[i++].v; const r = product(); v = op === "+" ? v + r : v - r; }
      return v;
    }
    function product() {
      let v = unary();
      while (isOp("*") || isOp("/") || isOp("%")) {
        const op = toks[i++].v; const r = unary();
        v = op === "*" ? v * r : op === "/" ? v / r : v % r;
      }
      return v;
    }
    function unary() {
      enter();
      try {
        if (isOp("-")) { i++; return -unary(); }
        if (isOp("+")) { i++; return unary(); }
        return power();
      } finally { depth--; }
    }
    function power() {
      const base = atom();
      if (isOp("**")) { i++; return base ** unary(); }
      return base;
    }
    function atom() {
      const tok = toks[i++];
      if (!tok) throw new Error("unexpected end");
      if (tok.t === "num") return tok.v;
      if (tok.t === "op" && tok.v === "(") { enter(); const v = sum(); depth--; expect(")"); return v; }
      if (tok.t === "id") {
        if (isOp("(")) {
          const f = FUNCS.get(tok.v);
          if (!f) throw new Error("unknown function " + tok.v);
          i++;
          const args = [];
          if (!isOp(")")) { enter(); do { args.push(sum()); } while (isOp(",") && ++i); depth--; }
          expect(")");
          if (args.length < f.min || args.length > f.max) throw new Error("wrong argument count for " + tok.v);
          return f.fn(...args);
        }
        if (!scope.has(tok.v)) throw new Error("unknown name " + tok.v);
        return scope.get(tok.v);
      }
      throw new Error("unexpected " + tok.v);
    }
    const result = sum();
    if (i !== toks.length) throw new Error("unexpected " + toks[i].v);
    return result;
  }

  const fmt = (n) => (Number.isFinite(n) ? String(Math.round(n * 10000) / 10000) : "undefined");

  // ---------- explorables ----------
  function buildExplorable(pre) {
    const box = document.createElement("div");
    box.className = "explorable no-read";
    let spec;
    try {
      spec = JSON.parse(pre.textContent);
      if (!spec || typeof spec !== "object" || typeof spec.show !== "string") throw new Error("needs a show string");
      if (!spec.vars || typeof spec.vars !== "object") throw new Error("needs vars");
    } catch (e) {
      box.textContent = "Explorable could not be read: " + e.message;
      return box;
    }
    const state = {};
    const out = document.createElement("div");
    out.className = "explorable-show";
    out.setAttribute("role", "status");
    const update = () => {
      out.textContent = spec.show.replace(/\{([^{}]*)\}/g, (_, expr) => {
        try { return fmt(evaluate(expr, state)); } catch { return "?"; }
      });
    };
    for (let [name, def] of Object.entries(spec.vars)) {
      const ok = /^[A-Za-z_]\w*$/.test(name) && name !== "Math" && def &&
        [def.min, def.max, def.value].every(Number.isFinite) && def.min <= def.max;
      if (!ok) { box.textContent = "Explorable variable " + name + " is not valid."; return box; }
      // Clamp like the slider will, so the text and the slider agree from the start.
      def = { ...def, value: Math.min(def.max, Math.max(def.min, def.value)) };
      state[name] = def.value;
      const row = document.createElement("label");
      row.className = "explorable-row";
      const label = document.createElement("span"); label.textContent = name;
      const input = document.createElement("input");
      input.type = "range"; input.min = def.min; input.max = def.max; input.value = def.value;
      input.step = Number.isFinite(def.step) ? def.step : 1;
      input.dataset.var = name;
      const val = document.createElement("output"); val.textContent = fmt(def.value);
      input.addEventListener("input", () => { state[name] = parseFloat(input.value); val.textContent = fmt(state[name]); update(); });
      row.append(label, input, val);
      box.appendChild(row);
    }
    box.appendChild(out);
    update();
    return box;
  }

  // ---------- narration highlighting ----------
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const norm = (s) => s.replace(/\s+/g, " ").trim();

  function clearNarrated() {
    document.querySelectorAll(".mermaid g.node.narrated").forEach(n => n.classList.remove("narrated"));
  }

  function highlight(item) {
    clearNarrated();
    const scope = item.el && (item.el.closest("section") || item.el.closest(".msg"));
    if (!scope) return;
    let sentence = " " + norm(item.text) + " ";
    const nodes = [...scope.querySelectorAll(".mermaid g.node")]
      .map(el => ({ el, label: norm(el.textContent) }))
      .filter(n => n.label)
      .sort((a, b) => b.label.length - a.label.length);
    for (const n of nodes) {
      const re = new RegExp("(?<![\\w])" + esc(n.label) + "(?![\\w])", "i");
      if (re.test(sentence)) {
        n.el.classList.add("narrated");
        // Blank the match so a shorter label inside it ("Server" in "Web Server") stays off.
        sentence = sentence.replace(re, (m) => " ".repeat(m.length));
      }
    }
  }

  function markAnimated(main, md) {
    // Use marked's own tokens, so indented (list-nested) and ~~~ fences pair up too.
    const sources = [];
    marked.walkTokens(marked.lexer(md), (t) => {
      if (t.type === "code" && (t.lang || "").split(/\s/)[0] === "mermaid") sources.push(t.text);
    });
    const divs = [...main.querySelectorAll(".mermaid")];
    if (sources.length !== divs.length) return; // can't pair them up safely
    divs.forEach((d, i) => d.classList.toggle("animated", /^\s*%%\s*animate\s*$/m.test(sources[i])));
  }

  C.visuals = { evaluate };

  C.addStyle(`
    .mermaid g.node.narrated :is(rect, path, polygon, circle, ellipse) {
      stroke: #f59e0b !important; stroke-width: 3.5px !important;
      filter: drop-shadow(0 0 6px rgba(245, 158, 11, .8));
    }
    .mermaid.animated :is(path.flowchart-link, line.messageLine0, line.messageLine1) {
      stroke-dasharray: 8 6; animation: classroom-flow 0.8s linear infinite;
    }
    @keyframes classroom-flow { to { stroke-dashoffset: -14; } }
    @media (prefers-reduced-motion: reduce) {
      .mermaid.animated :is(path.flowchart-link, line.messageLine0, line.messageLine1) { animation: none; }
    }
    .explorable { border: 1px solid #8884; border-radius: 8px; padding: 12px 14px; margin: 14px 0; }
    .explorable-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
    .explorable-row span { min-width: 3em; font-family: monospace; }
    .explorable-row input { flex: 1; }
    .explorable-row output { min-width: 3em; text-align: right; font-variant-numeric: tabular-nums; }
    .explorable-show { margin-top: 8px; font-weight: 600; }
  `);

  C.on("render", (main, md) => {
    main.querySelectorAll("pre > code.language-explorable").forEach(code => {
      const pre = code.parentElement;
      pre.replaceWith(buildExplorable(pre));
    });
    markAnimated(main, md);
  });
  C.on("speechStart", highlight);
  C.on("idle", clearNarrated);
})();
