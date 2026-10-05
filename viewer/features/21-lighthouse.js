// Lighthouse theme: a night-sea and beacon palette, a lighthouse icon in the
// header and the browser tab, and a beam that sweeps from the lamp while
// something is being read aloud.
//
// - The palette only redefines the design tokens from index.html, on
//   :root[data-palette="lighthouse"], so every feature picks it up. It keeps
//   the same AA contrast promises as the default palette, in light and dark.
// - Settings has a "Theme" select (Lighthouse / Classic), saved as
//   classroom.palette. Lighthouse is the default.
// - The beam is CSS animation, so prefers-reduced-motion switches it off
//   (index.html stops every animation then); the lamp just stays lit.
(() => {
  const C = window.Classroom;
  if (!C) return;
  const KEY = "classroom.palette";
  const root = document.documentElement;

  const LIGHT = `
    --bg: #eef4f8; --surface: #ffffff; --text: #0f1e2e; --muted: #46586b;
    --border: #cfdde8; --border-strong: #6d8296;
    --accent: #1c4e80; --accent-text: #ffffff; --accent-dark: #123557; --accent-soft: #e1ecf6;
    --accent-2: #b91c1c; --accent-2-text: #ffffff; --accent-2-soft: #fde6e4;
    --success: #10715a; --success-text: #ffffff; --success-soft: #dcf3ea;
    --danger: #b42318; --danger-text: #ffffff; --danger-soft: #fde4e1;
    --step-1: #1c4e80; --step-2: #b91c1c; --step-3: #0f766e; --step-4: #92400e; --step-text: #ffffff;
    --speaking: #ffe58a; --code-bg: #e6eef5;
    --card-shadow: 0 2px 4px rgba(15, 40, 70, .06), 0 10px 28px rgba(15, 40, 70, .10);
    --beam: rgba(255, 201, 74, .55);`;
  const DARK = `
    color-scheme: dark;
    --bg: #0b1622; --surface: #13212f; --text: #eaf2f8; --muted: #a9bccc;
    --border: #24384b; --border-strong: #6f89a0;
    --accent: #ffc94a; --accent-text: #1a1200; --accent-dark: #c9962a; --accent-soft: #2e2a18;
    --accent-2: #ff8a80; --accent-2-text: #2b0000; --accent-2-soft: #3a1f22;
    --success: #4fd1a5; --success-text: #00241a; --success-soft: #133a30;
    --danger: #ff8f8f; --danger-text: #3a0000; --danger-soft: #402023;
    --step-1: #ffc94a; --step-2: #ff8a80; --step-3: #5fd4c4; --step-4: #8fb8ff; --step-text: #0b1622;
    --speaking: #4a3f12; --code-bg: #1a2b3c;
    --card-shadow: 0 2px 4px rgba(0, 0, 0, .35), 0 10px 28px rgba(0, 0, 0, .4);
    --beam: rgba(255, 201, 74, .45);`;

  C.addStyle(`
    :root[data-palette="lighthouse"] { color-scheme: light; ${LIGHT} }
    @media (prefers-color-scheme: dark) {
      :root[data-palette="lighthouse"]:not([data-theme="light"]) { ${DARK} }
    }
    :root[data-palette="lighthouse"][data-theme="dark"] { ${DARK} }
    :root[data-palette="lighthouse"] header { box-shadow: 0 4px 14px rgba(15, 40, 70, .10); }

    header .title .mark.lh { position: relative; display: inline-flex; width: 1.5em; height: 1.5em; }
    header .title .mark.lh svg { width: 100%; height: 100%; position: relative; z-index: 1; }
    .lh .lamp { fill: #ffc94a; transition: fill .3s ease; }
    .lh-beam {
      position: absolute; left: 50%; top: 22%; width: 3.2em; height: .9em; margin-top: -.45em;
      transform-origin: 0 50%; pointer-events: none; opacity: 0;
      background: linear-gradient(90deg, var(--beam), transparent);
      clip-path: polygon(0 45%, 100% 0, 100% 100%, 0 55%);
      transition: opacity .3s ease;
    }
    .lh.on .lh-beam { opacity: 1; animation: lh-sweep 3.2s ease-in-out infinite; }
    .lh.on .lamp { fill: #ffdf7a; }
    @keyframes lh-sweep {
      0%   { transform: rotate(-25deg); }
      50%  { transform: rotate(205deg); }
      100% { transform: rotate(335deg); }
    }
  `);

  const ICON = `<svg viewBox="0 0 32 32" aria-hidden="true">
    <path d="M12 9h8l3 20H9z" fill="#ffffff" stroke="#0f1e2e" stroke-width="1.2"/>
    <path d="M11.4 13.5h9.2l.7 4.5H10.7zM10.1 22.5h11.8l.7 4.5H9.4z" fill="#c62828"/>
    <rect class="lamp" x="12.5" y="4.5" width="7" height="4.5" rx="1"/>
    <path d="M11 4.5h10L16 1z" fill="#c62828"/>
    <path d="M10.5 9h11" stroke="#0f1e2e" stroke-width="1.4"/>
    <path d="M5 30h22" stroke="#1c4e80" stroke-width="2" stroke-linecap="round"/>
  </svg>`;

  const setPalette = (p) => {
    if (p === "lighthouse") root.setAttribute("data-palette", "lighthouse");
    else root.removeAttribute("data-palette");
  };
  let palette = C.load(KEY, "lighthouse");
  setPalette(palette);

  // Browser-tab icon.
  const fav = document.createElement("link");
  fav.rel = "icon";
  fav.type = "image/svg+xml";
  fav.href = "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">${ICON.replace(/^<svg[^>]*>|<\/svg>$/g, "").replace('class="lamp"', 'fill="#ffc94a"')}</svg>`);
  document.head.appendChild(fav);

  let mark = null;
  C.on("ready", () => {
    mark = document.querySelector("header .title .mark");
    if (mark) {
      mark.classList.add("lh");
      mark.innerHTML = `<span class="lh-beam"></span>${ICON}`;
    }

    const label = document.createElement("label");
    label.textContent = "Theme ";
    const sel = document.createElement("select");
    sel.id = "palette";
    for (const [v, name] of [["lighthouse", "Lighthouse"], ["classic", "Classic"]]) {
      const o = document.createElement("option");
      o.value = v; o.textContent = name;
      sel.appendChild(o);
    }
    sel.value = palette;
    sel.onchange = () => { palette = sel.value; C.save(KEY, palette); setPalette(palette); };
    label.appendChild(sel);
    C.addSetting(label);
  });

  // The beam sweeps while something is being read aloud.
  const beam = (on) => { if (mark) mark.classList.toggle("on", on); };
  C.on("speechStart", () => beam(true));
  C.on("idle", () => beam(false));
  C.on("stopped", () => beam(false));
})();
