// Diagram view: readable label contrast, larger text, open in new tab (#32).
//
//  1. After mermaid renders a diagram, every node, cluster and edge label that
//     is not already readable (WCAG contrast < 4.5 against its fill) gets an
//     inline dark or light colour, whichever contrasts more. Lessons set
//     classDef fills without a colour, and mermaid's dark theme then gives
//     light text on a light fill. Inline !important beats mermaid's own
//     <style>. Labels are <text>/<tspan> or, with htmlLabels, <foreignObject>
//     with HTML spans; both are handled.
//  2. Diagram text is 18px, like the body text (mermaid themeVariables).
//  3. A "Open" button under each diagram opens it full-size in a new tab
//     from a Blob, so no server support is needed.
(() => {
  const C = window.Classroom;
  if (!C) return;

  const DARK = "#1f2328", LIGHT = "#ffffff";

  // Re-initialise with the same settings core.js uses, plus the bigger font.
  // Runs before the first render, which only starts after the lesson fetch.
  if (window.mermaid) {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({
      startOnLoad: false, theme: dark ? "dark" : "default", securityLevel: "strict",
      themeVariables: { fontSize: "18px" },
    });
  }

  // ---------- colour maths ----------
  function parse(css) {
    const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.%]+))?\s*\)/.exec(css || "");
    if (!m) return null;
    let a = m[4] === undefined ? 1 : m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
    return { r: +m[1], g: +m[2], b: +m[3], a };
  }
  function over(top, under) {
    const a = top.a;
    return { r: top.r * a + under.r * (1 - a), g: top.g * a + under.g * (1 - a), b: top.b * a + under.b * (1 - a), a: 1 };
  }
  function lum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (h) => parse(`rgb(${parseInt(h.slice(1, 3), 16)},${parseInt(h.slice(3, 5), 16)},${parseInt(h.slice(5, 7), 16)})`);

  function pageBg(el) {
    let bg = { r: 255, g: 255, b: 255, a: 1 };
    const chain = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) chain.unshift(n);
    for (const n of chain) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) bg = over(c, bg);
    }
    return bg;
  }

  // The fill behind a label: the first filled shape in its group, else a
  // background colour, else the page.
  function fillOf(group, base) {
    for (const s of group.querySelectorAll("rect, polygon, circle, ellipse, path")) {
      if (s.closest(".label, .edgeLabel, foreignObject") && !s.matches(".label-container, .basic")) continue;
      const cs = getComputedStyle(s);
      const c = parse(cs.fill);
      if (c && cs.fill !== "none") return over({ ...c, a: c.a * (parseFloat(cs.fillOpacity) || 1) }, base);
    }
    return null;
  }

  function paint(label, bg) {
    const isHtml = !!label.closest("foreignObject");
    const cs = getComputedStyle(label);
    const now = parse(isHtml ? cs.color : cs.fill);
    if (now && contrast(over(now, bg), bg) >= 4.5) return;
    const want = contrast(hex(DARK), bg) >= contrast(hex(LIGHT), bg) ? DARK : LIGHT;
    label.style.setProperty(isHtml ? "color" : "fill", want, "important");
    if (!isHtml) label.style.setProperty("stroke", "none", "important");
  }

  function fixLabels(div) {
    const svg = div.querySelector("svg");
    if (!svg) return;
    const base = pageBg(div);
    const labelsIn = (g) => g.querySelectorAll("text, tspan, foreignObject span, foreignObject p, foreignObject div");
    svg.querySelectorAll("g.node, g.cluster").forEach(g => {
      const bg = fillOf(g, base) || base;
      labelsIn(g).forEach(l => paint(l, bg));
    });
    svg.querySelectorAll(".edgeLabel").forEach(g => {
      let bg = base;
      for (const el of [g, ...g.querySelectorAll("rect, span, div, p")]) {
        const cs = getComputedStyle(el);
        const c = parse(el instanceof SVGElement && el.tagName === "rect" ? cs.fill : cs.backgroundColor);
        if (c && c.a > 0) { bg = over(c, base); break; }
      }
      // an edge label's own text, or the group itself when it is the text
      (g.matches("text, tspan") ? [g] : [...labelsIn(g)]).forEach(l => paint(l, bg));
    });
  }

  // Mermaid sets width:100% and max-width = viewBox width, so a wide graph is
  // scaled down to the column and its text shrinks. Keep the scale at 0.8 or
  // more; the .mermaid container scrolls sideways when that is wider.
  const MIN_SCALE = 0.8;
  function viewBoxWidth(svg) {
    const vb = svg.viewBox && svg.viewBox.baseVal;
    return vb && vb.width ? vb.width : 0;
  }
  function keepReadable(svg) {
    const w = viewBoxWidth(svg);
    if (w) svg.style.minWidth = Math.round(w * MIN_SCALE) + "px";
  }

  // ---------- open in a new tab ----------
  function openFull(div) {
    const svg = div.querySelector("svg");
    if (!svg) return;
    const clone = svg.cloneNode(true);
    clone.removeAttribute("width"); clone.removeAttribute("height");
    const vw = viewBoxWidth(svg);
    // scale 1 (text at its true size); the page scrolls if the diagram is wider
    clone.setAttribute("style", vw ? `width:${vw}px;max-width:none;min-width:0;height:auto;display:block;margin:0 auto`
                                   : "width:100%;height:auto;display:block;margin:0 auto");
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const bg = getComputedStyle(document.body).backgroundColor;
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Diagram</title>`
      + `<meta name="viewport" content="width=device-width,initial-scale=1">`
      + `<style>body{margin:0;padding:24px;overflow:auto;background:${bg};}</style></head><body>`
      + new XMLSerializer().serializeToString(clone) + `</body></html>`;
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    window.open(url, "_blank");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  C.addStyle(`
    .diagram-tools { text-align: right; margin: -8px 0 10px; }
    .diagram-open { font-size: 0.8em; padding: 2px 10px; cursor: pointer; border-radius: 6px;
      border: 1px solid var(--border); background: var(--surface); color: var(--text); }
  `);

  C.on("diagram", (div) => {
    try { const svg = div.querySelector("svg"); if (svg) keepReadable(svg); } catch (e) { console.error("[diagram view]", e); }
    try { fixLabels(div); } catch (e) { console.error("[diagram view]", e); }
    if (!div.querySelector("svg")) return;
    if (div.nextElementSibling?.classList.contains("diagram-tools")) return;
    const bar = document.createElement("div");
    bar.className = "diagram-tools no-read";
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "diagram-open no-read";
    btn.textContent = "⤢ Open"; btn.title = "Open this diagram full size in a new tab";
    btn.onclick = () => openFull(div);
    bar.appendChild(btn);
    div.after(bar);
  });
})();
