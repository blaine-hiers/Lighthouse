// Feature: spaced review. A header badge showing how many concepts are due,
// read from ../progress/concepts.json (see progress/README.md). Hidden when
// none are due or the file is missing.
(() => {
  const C = window.Classroom;
  const badge = document.createElement("span");
  badge.id = "review-badge";
  badge.hidden = true;
  badge.title = "Concepts due for spaced review. Ask Claude: what's due?";
  C.addControl(badge);
  C.addStyle(`
    #review-badge { font-size: 14px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
      background: var(--accent); color: var(--accent-text); }
  `);

  async function update() {
    let due = 0;
    try {
      const res = await fetch("../progress/concepts.json", { cache: "no-store" });
      if (res.ok) {
        const concepts = Object.values((await res.json()).concepts || {});
        const now = Date.now();
        due = concepts.filter((c) => c.fsrs && Date.parse(c.fsrs.due) <= now).length;
      }
    } catch { /* missing or unreadable: treat as nothing due */ }
    badge.textContent = `${due} due`;
    badge.hidden = due === 0;
  }

  C.on("ready", update);
  setInterval(update, 60000);
})();
