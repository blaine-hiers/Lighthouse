// Look and feel (#43): the behaviour that goes with the "Bold & playful" theme.
// The theme itself (design tokens, cards, step badges) is CSS in index.html.
//
// - Settings popover: the "⚙ Settings" button opens #settings-pop, which holds
//   the secondary controls (see Classroom.addSetting). Esc, a click outside, or
//   the button again closes it.
// - "Your turn" / "Try it" / a bold "Practice": a lesson paragraph or blockquote that starts with
//   those words gets the .your-turn class (and .try-it) so it is styled as a
//   task card. The text itself is untouched, so it is read aloud as written.
(() => {
  const C = window.Classroom;
  if (!C) return;

  // ---------- Settings popover ----------
  C.on("ready", () => {
    const btn = document.getElementById("btn-settings");
    const pop = document.getElementById("settings-pop");
    const header = document.querySelector("header");
    const body = document.getElementById("settings-body");
    if (!btn || !pop) return;
    // Fixed under the header and always inside the viewport, however the header wraps.
    const place = () => {
      const top = Math.max(8, Math.round(header.getBoundingClientRect().bottom + 8));
      pop.style.top = top + "px";
      pop.style.maxHeight = Math.max(160, window.innerHeight - top - 8) + "px";
    };
    const setOpen = (open) => {
      if (open) place();
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.onclick = () => setOpen(pop.hidden);
    window.addEventListener("resize", () => { if (!pop.hidden) place(); });
    window.addEventListener("scroll", () => { if (!pop.hidden) place(); }, { passive: true });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !pop.hidden) { setOpen(false); btn.focus(); }
    });
    document.addEventListener("click", (e) => {
      if (!pop.hidden && !pop.contains(e.target) && !btn.contains(e.target)) setOpen(false);
    });
    pop.addEventListener("focusout", (e) => {   // Tab out of the popover closes it
      const to = e.relatedTarget;
      if (to && !pop.contains(to) && to !== btn) setOpen(false);
    });

    // The "use Edge" tip (feature 06) lives in Settings as a line of text, never over the lesson.
    const adopt = (tip) => {
      if (tip.parentElement === body) return;
      tip.removeAttribute("style");
      tip.querySelectorAll("[style]").forEach(n => n.removeAttribute("style"));
      body.prepend(tip);
    };
    const existing = document.getElementById("edge-tip");
    if (existing) adopt(existing);
    new MutationObserver(() => { const t = document.getElementById("edge-tip"); if (t) adopt(t); })
      .observe(document.body, { childList: true });
  });

  // ---------- "Your turn" blocks ----------
  const TURN = /^[\s"'“”*_>]*(?:✋\s*)?[\s*_]*(your turn|try it)\b/i;

  C.on("render", (main) => {
    // Number the badges here, not with a CSS counter: counters skip display:none
    // sections (focus mode), which would show the wrong step. The colour cycles
    // through --step-1..4 (the intro before the first ## has no badge, colour 1).
    let step = 0;
    for (const sec of main.querySelectorAll("section")) {
      const h2 = sec.querySelector(":scope > .section-head h2");
      if (!h2) continue;
      h2.dataset.step = String(++step);
      sec.style.setProperty("--step", `var(--step-${(step - 1) % 4 + 1})`);
    }
    for (const el of main.querySelectorAll("blockquote, p")) {
      if (el.closest("pre, details, .no-read")) continue;
      if (el.tagName === "P" && el.closest("blockquote, li")) continue;
      const first = el.tagName === "BLOCKQUOTE" ? el.querySelector("p") : el;
      if (!first) continue;
      let m = TURN.exec(first.textContent);
      // A bold lead-in such as **Practice** also marks a task paragraph.
      const lead = first.firstElementChild;
      if (!m && lead && /^(STRONG|B)$/.test(lead.tagName) && first.firstChild === lead) {
        m = /^\s*(practice)\b/i.exec(lead.textContent);
      }
      if (!m) continue;
      el.classList.add("your-turn");
      el.classList.toggle("try-it", m[1].toLowerCase() === "try it");
    }
  });
})();
