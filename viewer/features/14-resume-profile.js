// Resume: on the lesson index, mark each lesson "in progress" or "finished"
// (finished = its lesson.md has a "## Recap" section) and list in-progress
// ones first. Order within each group stays newest first.
Classroom.addStyle(`
  .lesson-list .badge { float: right; font-size: 0.8em; padding: 2px 8px; border-radius: 10px;
    border: 1px solid currentColor; opacity: 0.85; }
  .lesson-list .badge.in-progress { font-weight: 600; }
`);

Classroom.on("index", async (list, names) => {
  if (!list) return;
  const done = {};
  await Promise.all(names.map(async (name) => {
    try {
      const res = await fetch(`../lessons/${encodeURIComponent(name)}/lesson.md`, { cache: "no-store" });
      done[name] = res.ok && /^## Recap\b/m.test(await res.text());
    } catch { done[name] = false; }
  }));
  const items = [...list.querySelectorAll("li")];
  for (const li of items) {
    const a = li.querySelector("a");
    const name = new URL(a.href).searchParams.get("lesson"); // already decoded
    const finished = !!done[name];
    const badge = document.createElement("span");
    badge.className = "badge " + (finished ? "finished" : "in-progress");
    badge.textContent = finished ? "finished" : "in progress";
    a.prepend(badge);
    li.dataset.state = finished ? "finished" : "in-progress";
  }
  const rank = (li) => (li.dataset.state === "in-progress" ? 0 : 1);
  list.append(...items.sort((a, b) => rank(a) - rank(b))); // stable sort
});
